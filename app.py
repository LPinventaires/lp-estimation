import os
import csv
import io
import base64
from datetime import datetime
from functools import wraps
from typing import Optional
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, send_file)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from pydantic import BaseModel, Field

from parsing import extract_text, extract_fields
from report import build_report, QUARTIERS

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024

# Upload folder configuration
UPLOAD_FOLDER = os.path.join(app.static_folder, "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

db_url = os.environ.get("DATABASE_URL", "sqlite:///lp_estim.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# Code d'accès — à changer en prod via APP_PASSWORD
APP_PASSWORD = os.environ.get("APP_PASSWORD", "LP-estimation")


# ----------------------- MODÈLES -----------------------
class RefPrice(db.Model):
    """Référence prix/m² appartements, par adresse / quartier (issue des estimations LP)."""
    id = db.Column(db.Integer, primary_key=True)
    quartier = db.Column(db.String(120))
    adresse = db.Column(db.String(200))
    prix_m2 = db.Column(db.Float)
    annee = db.Column(db.String(20))
    source = db.Column(db.String(200))
    kind = db.Column(db.String(10), default="sold")  # sold | forsale | retenu


class Estimation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
    address = db.Column(db.String(200))
    quartier = db.Column(db.String(120))
    type_bien = db.Column(db.String(80))
    surface = db.Column(db.Float, default=0)
    pieces = db.Column(db.String(30))
    etage = db.Column(db.String(40))
    annee = db.Column(db.String(40))
    etat = db.Column(db.String(160))
    balcon = db.Column(db.Float, default=0)
    balcon_pond = db.Column(db.Float, default=0.5)
    parking_nb = db.Column(db.Integer, default=0)
    parking_val = db.Column(db.Float, default=0)
    prix_m2 = db.Column(db.Float, default=0)
    marge = db.Column(db.Float, default=0.07)
    description = db.Column(db.Text)
    atouts = db.Column(db.Text)
    inconvenients = db.Column(db.Text)
    courtier = db.Column(db.String(120))
    notes = db.Column(db.Text, default="")

    @property
    def val_principale(self):
        return (self.prix_m2 or 0) * (self.surface or 0)

    @property
    def val_balcon(self):
        return (self.prix_m2 or 0) * (self.balcon or 0) * (self.balcon_pond or 0)

    @property
    def val_parking(self):
        return (self.parking_nb or 0) * (self.parking_val or 0)

    @property
    def valeur_venale(self):
        return self.val_principale + self.val_balcon + self.val_parking

    @property
    def prix_presentation(self):
        return round(self.valeur_venale * (1 + (self.marge or 0)) / 10000) * 10000


class Setting(db.Model):
    """Application settings — logo filename, etc."""
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(500))


class AuditLog(db.Model):
    """Historique des actions pour audit."""
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    action = db.Column(db.String(100))  # created, updated, deleted, cloned
    estimation_id = db.Column(db.Integer, db.ForeignKey('estimation.id'))
    description = db.Column(db.Text)


class PriceAlert(db.Model):
    """Alertes pour changements de prix par quartier."""
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    quartier = db.Column(db.String(120))
    previous_price = db.Column(db.Float)
    new_price = db.Column(db.Float)
    triggered = db.Column(db.Boolean, default=False)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    email = db.Column(db.String(200), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(120))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


# ----------------------- AUTH -----------------------
def login_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*a, **k)
    return wrap


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


def own_estimations():
    """Query base — estimations de l'utilisateur connecté seulement."""
    return Estimation.query.filter_by(user_id=session.get("user_id"))


def own_estimation_or_404(eid):
    """Fetch une estimation appartenant à l'utilisateur connecté, sinon 404."""
    e = own_estimations().filter_by(id=eid).first()
    if not e:
        from flask import abort
        abort(404)
    return e


DEFAULT_LOGO_FILENAME = "leonard-logo.png"


def get_logo_path():
    """Retourne le chemin du logo actuel ou le logo par défaut Leonard Properties."""
    setting = Setting.query.filter_by(key="logo_filename").first()
    if setting and setting.value:
        full_path = os.path.join(app.config["UPLOAD_FOLDER"], setting.value)
        if os.path.exists(full_path):
            return setting.value
    # Fallback : logo par défaut committé dans le repo
    default_full = os.path.join(app.config["UPLOAD_FOLDER"], DEFAULT_LOGO_FILENAME)
    if os.path.exists(default_full):
        return DEFAULT_LOGO_FILENAME
    return None


def allowed_file(filename):
    """Vérifie si le fichier a une extension autorisée."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def log_audit(action, est_id, description=""):
    """Enregistre une action dans l'audit log."""
    log = AuditLog(action=action, estimation_id=est_id, description=description)
    db.session.add(log)
    db.session.commit()


SHARED_EMAIL = "team@leonardproperties.local"


def _shared_user():
    """Compte partagé Leonard Properties : tout le monde se connecte dessus."""
    u = User.query.filter_by(email=SHARED_EMAIL).first()
    if not u:
        u = User(email=SHARED_EMAIL, name="Leonard Properties")
        u.set_password(os.environ.get("APP_PASSWORD", "LP-estimation"))
        db.session.add(u)
        db.session.commit()
    return u


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    expected = (os.environ.get("APP_PASSWORD") or "LP-estimation").strip()
    if request.method == "POST":
        password = (request.form.get("password") or "").strip()
        if password and password == expected:
            session["user_id"] = _shared_user().id
            return redirect(url_for("dashboard"))
        flash("Mot de passe incorrect.")
    return render_template("login.html", logo=get_logo_path())


@app.route("/signup")
def signup():
    """Ancienne route d'inscription — désactivée, redirige vers login."""
    return redirect(url_for("login"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    """Paramètres — téléchargement du logo."""
    current_logo = get_logo_path()

    if request.method == "POST":
        # Vérifie s'il y a un fichier dans la requête
        if "logo_file" not in request.files:
            flash("Aucun fichier sélectionné.", "error")
            return redirect(url_for("settings"))

        file = request.files["logo_file"]

        if file.filename == "":
            flash("Aucun fichier sélectionné.", "error")
            return redirect(url_for("settings"))

        # Vérifie l'extension et la taille
        if not allowed_file(file.filename):
            flash("Format non autorisé. Accepté: PNG, JPG, GIF.", "error")
            return redirect(url_for("settings"))

        if file.content_length and file.content_length > 2 * 1024 * 1024:
            flash("Fichier trop volumineux. Max 2 MB.", "error")
            return redirect(url_for("settings"))

        try:
            # Génère un nom de fichier sécurisé avec timestamp
            ext = file.filename.rsplit(".", 1)[1].lower()
            filename = f"logo_{int(datetime.utcnow().timestamp())}.{ext}"
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)

            # Supprime l'ancien logo si présent
            if current_logo:
                old_path = os.path.join(app.config["UPLOAD_FOLDER"], current_logo)
                try:
                    os.remove(old_path)
                except OSError:
                    pass

            # Sauvegarde le nouveau fichier
            file.save(filepath)

            # Metà jour la base de données
            setting = Setting.query.filter_by(key="logo_filename").first()
            if setting:
                setting.value = filename
            else:
                setting = Setting(key="logo_filename", value=filename)
                db.session.add(setting)
            db.session.commit()

            flash("Logo mis à jour avec succès.", "success")
            return redirect(url_for("settings"))

        except Exception as e:
            flash(f"Erreur lors de l'upload: {str(e)}", "error")
            return redirect(url_for("settings"))

    return render_template("settings.html", current_logo=current_logo)


# ----------------------- HELPERS -----------------------
def _addr_match(adresse, r):
    if not adresse or not r.adresse:
        return False
    a = adresse.lower().strip()
    b = r.adresse.lower().strip()
    key = a[:14]
    return key in b or b[:14] in a


def ref_for(quartier, adresse):
    """Retourne (prix_m2 proposé, comparables de la localité).

    Le prix proposé privilégie les ventes de l'adresse exacte, sinon la médiane du quartier.
    Les comparables affichés couvrent l'adresse ET le quartier (plus utile dans le rapport).
    """
    refs = RefPrice.query.all()
    by_addr = [r for r in refs if _addr_match(adresse, r)]
    by_quartier = [r for r in refs if quartier and r.quartier and
                   r.quartier.lower() == quartier.lower()]
    # pool = union (adresse + quartier), sans doublon
    pool, seen = [], set()
    for r in by_addr + by_quartier:
        if r.id not in seen:
            seen.add(r.id)
            pool.append(r)

    def median(vals):
        vals = sorted(vals)
        return vals[len(vals) // 2] if vals else None

    addr_sold = [r.prix_m2 for r in by_addr if r.prix_m2 and r.kind in ("sold", "retenu")]
    quartier_sold = [r.prix_m2 for r in by_quartier if r.prix_m2 and r.kind in ("sold", "retenu")]
    proposed = median(addr_sold) or median(quartier_sold)
    return proposed, pool


# ----------------------- ROUTES -----------------------
@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    """Reçoit un fichier déposé, en extrait les champs, pré-remplit le formulaire."""
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Aucun fichier"}), 400
    text = extract_text(file)
    fields = extract_fields(text)
    proposed, pool = ref_for(fields.get("quartier"), fields.get("address"))
    if proposed and not fields.get("prix_m2"):
        fields["prix_m2"] = round(proposed)
    fields["_refs"] = [{"adresse": r.adresse, "quartier": r.quartier,
                        "prix_m2": r.prix_m2, "annee": r.annee, "kind": r.kind} for r in pool]
    return jsonify(fields)


class PhotoExtraction(BaseModel):
    """Champs extraits d'une photo de fiche/annonce d'un bien immobilier à Genève."""
    address: str = Field(default="", description="Adresse complète (ex: Avenue de Miremont 30)")
    quartier: str = Field(default="", description="Quartier — un de: Champel, Eaux-Vives, Miremont (vide si autre ou incertain)")
    type_bien: str = Field(default="", description="Type — ex: Appartement, Duplex, Attique, Triplex")
    surface: Optional[float] = Field(default=None, description="Surface habitable ou pondérée en m² (nombre uniquement)")
    pieces: str = Field(default="", description="Nombre de pièces (ex: 5, 4.5)")
    etage: str = Field(default="", description="Étage (ex: 3, RDC)")
    annee: str = Field(default="", description="Année de construction ou de rénovation (ex: 2016)")
    etat: str = Field(default="", description="État du bien (ex: Excellent, Rénové, À rafraîchir)")
    balcon: Optional[float] = Field(default=None, description="Surface totale des extérieurs (balcon/loggia/terrasse) en m²")
    parking_nb: Optional[int] = Field(default=None, description="Nombre de places de parking")
    description: str = Field(default="", description="Description libre du bien (2-4 phrases max)")


def _resize_image_for_vision(raw: bytes, max_edge: int = 1568) -> tuple[bytes, str]:
    """Redimensionne + reencode l'image pour Claude Vision. Retourne (bytes, media_type)."""
    from PIL import Image, ImageOps
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_edge:
        img.thumbnail((max_edge, max_edge))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


@app.route("/upload-photo", methods=["POST"])
@login_required
def upload_photo():
    """Analyse une photo (fiche, capture d'écran, photo du bien) via Claude Vision et pré-remplit le formulaire."""
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Aucun fichier"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "Clé API Anthropic non configurée sur le serveur (ANTHROPIC_API_KEY manquante)."}), 500

    try:
        raw = file.read()
        img_bytes, media_type = _resize_image_for_vision(raw)
    except Exception as e:
        return jsonify({"error": f"Image illisible (format non supporté ou fichier corrompu) : {e}"}), 400

    b64 = base64.standard_b64encode(img_bytes).decode()

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        prompt = (
            "Cette image montre une fiche, une annonce, une capture d'écran ou une photo d'un bien "
            "immobilier à Genève. Extrais les champs demandés pour pré-remplir un formulaire "
            "d'estimation LP. Règles :\n"
            "- Pour 'quartier' : renvoie exactement 'Champel', 'Eaux-Vives' ou 'Miremont'. "
            "Si le bien est ailleurs ou si tu n'es pas sûr, laisse vide.\n"
            "- Pour 'surface' : donne un nombre en m² (ex: 120). Si 'surface pondérée' et 'surface "
            "PPE' apparaissent, prends la pondérée.\n"
            "- 'description' : 2 à 4 phrases en français, style sobre et factuel LP.\n"
            "- Si un champ n'est pas visible, laisse-le vide (chaîne vide ou null).\n"
            "- Ne devine pas : mieux vaut vide que faux."
        )
        response = client.messages.parse(
            model="claude-opus-4-8",
            max_tokens=1500,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
            output_format=PhotoExtraction,
        )
        extracted = response.parsed_output
    except Exception as e:
        return jsonify({"error": f"Erreur lors de l'analyse par l'IA : {e}"}), 502

    fields = extracted.model_dump()
    # normaliser : convertir Nones en valeurs vides pour le JS front-end
    fields = {k: ("" if v is None else v) for k, v in fields.items()}

    # comparables locaux (comme le fait déjà /upload)
    proposed, pool = ref_for(fields.get("quartier"), fields.get("address"))
    if proposed:
        fields["prix_m2"] = round(proposed)
    fields["_refs"] = [{"adresse": r.adresse, "quartier": r.quartier,
                        "prix_m2": r.prix_m2, "annee": r.annee, "kind": r.kind} for r in pool]
    return jsonify(fields)


QUARTIER_ATOUTS = {
    "Champel": "quartier résidentiel prisé de la rive gauche, écoles internationales et hôpitaux à proximité, ambiance calme et verte",
    "Eaux-Vives": "quartier vivant entre le lac et le parc La Grange, commerces et restaurants nombreux, très bien desservi",
    "Miremont": "secteur résidentiel de Champel-Miremont, calme, familial, vues sur la ville et proximité des parcs",
}


def _generate_lp_description(fields: dict) -> str:
    """Génère un paragraphe LP (3-5 phrases) via Claude Opus 4.8. Retourne '' si l'API n'est pas dispo."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ""
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        atouts_quartier = QUARTIER_ATOUTS.get(fields.get("quartier") or "", "")
        prompt = f"""Rédige un paragraphe de 3 à 5 phrases décrivant ce bien immobilier dans le style sobre, factuel et élégant de Leonard Properties (courtier à Genève).

Consignes :
- Français impeccable, phrases complètes, ton professionnel LP.
- Évite les superlatifs vides ("magnifique", "exceptionnel", "unique") sauf s'ils sont justifiés par les faits.
- Décris le bien, mentionne le quartier et ses atouts, cite l'état/année si pertinent.
- Ne mentionne pas de champ vide ou inconnu.
- Pas de listes à puces, pas d'en-têtes, uniquement un ou deux paragraphes courts.
- N'invente rien qui ne soit pas dans les données ci-dessous.

Bien à décrire :
- Adresse : {fields.get('address') or 'non renseignée'}
- Quartier : {fields.get('quartier') or 'non renseigné'}{f' ({atouts_quartier})' if atouts_quartier else ''}
- Type : {fields.get('type_bien') or 'non renseigné'}
- Surface : {fields.get('surface') or '?'} m²
- Pièces : {fields.get('pieces') or '?'}
- Étage : {fields.get('etage') or '?'}
- Année : {fields.get('annee') or '?'}
- État : {fields.get('etat') or '?'}
- Extérieurs : {fields.get('balcon') or 0} m²
- Parkings : {fields.get('parking_nb') or 0}

Réponds uniquement avec le paragraphe (pas de préambule, pas de guillemets)."""
        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text
    except Exception as e:
        app.logger.warning(f"Description IA échouée : {e}")
        return ""


@app.route("/estimation/new", methods=["GET", "POST"])
@login_required
def estimation_new():
    if request.method == "POST":
        f = request.form
        def num(k, d=0.0):
            try:
                return float(str(f.get(k, "")).replace("'", "").replace(" ", "").replace(",", ".") or d)
            except ValueError:
                return d
        description = (f.get("description") or "").strip()
        # Si l'utilisateur n'a rien mis, on demande à Claude de rédiger un paragraphe LP
        if not description:
            description = _generate_lp_description({
                "address": f.get("address"), "quartier": f.get("quartier"),
                "type_bien": f.get("type_bien"), "surface": num("surface"),
                "pieces": f.get("pieces"), "etage": f.get("etage"),
                "annee": f.get("annee"), "etat": f.get("etat"),
                "balcon": num("balcon"), "parking_nb": int(num("parking_nb")),
            })
        e = Estimation(
            user_id=session.get("user_id"),
            address=f.get("address"), quartier=f.get("quartier"),
            type_bien=f.get("type_bien"), surface=num("surface"),
            pieces=f.get("pieces"), etage=f.get("etage"), annee=f.get("annee"),
            etat=f.get("etat"), balcon=num("balcon"), balcon_pond=num("balcon_pond", 0.5),
            parking_nb=int(num("parking_nb")), parking_val=num("parking_val"),
            prix_m2=num("prix_m2"), marge=num("marge", 0.07),
            description=description, atouts=f.get("atouts"),
            inconvenients=f.get("inconvenients"), courtier=f.get("courtier"))
        db.session.add(e)
        db.session.commit()
        return redirect(url_for("estimation_report", eid=e.id))
    return render_template("estimation_form.html", quartiers=QUARTIERS, prefill={})


@app.route("/estimation/<int:eid>")
@login_required
def estimation_report(eid):
    e = own_estimation_or_404(eid)
    _, pool = ref_for(e.quartier, e.address)
    sold = [r for r in pool if r.kind in ("sold", "retenu")]
    forsale = [r for r in pool if r.kind == "forsale"]
    html = build_report(e, sold, forsale)
    return render_template("report.html", e=e, report_html=html)


@app.route("/estimation/<int:eid>/delete", methods=["POST"])
@login_required
def estimation_delete(eid):
    e = own_estimation_or_404(eid)
    db.session.delete(e)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/references")
@login_required
def references():
    refs = RefPrice.query.order_by(RefPrice.quartier, RefPrice.adresse).all()
    return render_template("references.html", refs=refs, quartiers=QUARTIERS)


@app.route("/references/add", methods=["POST"])
@login_required
def references_add():
    f = request.form
    try:
        pm = float(str(f.get("prix_m2", "")).replace("'", "").replace(" ", "") or 0)
    except ValueError:
        pm = 0
    db.session.add(RefPrice(quartier=f.get("quartier"), adresse=f.get("adresse"),
                            prix_m2=pm, annee=f.get("annee"), source=f.get("source"),
                            kind=f.get("kind", "sold")))
    db.session.commit()
    return redirect(url_for("references"))


@app.route("/references/<int:rid>/delete", methods=["POST"])
@login_required
def references_delete(rid):
    r = RefPrice.query.get_or_404(rid)
    db.session.delete(r)
    db.session.commit()
    return redirect(url_for("references"))


@app.route("/analysis")
@login_required
def analysis():
    """Page d'analyse rapide avec texte libre."""
    return render_template("analysis.html")


@app.route("/quick-analyze", methods=["POST"])
@login_required
def quick_analyze():
    """Analyse un texte libre et retourne une estimation complète."""
    data = request.get_json() or {}
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"error": "Texte vide"}), 400

    # Extraire les données du texte
    fields = extract_fields(text)

    # Récupérer les références prix
    proposed, pool = ref_for(fields.get("quartier"), fields.get("address"))
    if proposed and not fields.get("prix_m2"):
        fields["prix_m2"] = round(proposed)

    # Construire l'objet Estimation
    def num(v, d=0.0):
        try:
            if isinstance(v, (int, float)):
                return float(v)
            return float(str(v or d).replace("'", "").replace(" ", "").replace(",", ".") or d)
        except (ValueError, TypeError):
            return d

    est = Estimation(
        address=fields.get("address", ""),
        quartier=fields.get("quartier", ""),
        type_bien=fields.get("type_bien", ""),
        surface=num(fields.get("surface")),
        pieces=fields.get("pieces", ""),
        etage=fields.get("etage", ""),
        annee=fields.get("annee", ""),
        etat=fields.get("etat", ""),
        balcon=num(fields.get("balcon")),
        balcon_pond=num(fields.get("balcon_pond", 0.5), 0.5),
        parking_nb=int(num(fields.get("parking_nb"))),
        parking_val=num(fields.get("parking_val")),
        prix_m2=num(fields.get("prix_m2")),
        marge=num(fields.get("marge", 0.07), 0.07),
        description=fields.get("description", "")
    )

    # Retourner le résultat
    sold = [r for r in pool if r.kind in ("sold", "retenu")]
    return jsonify({
        "address": est.address,
        "quartier": est.quartier,
        "surface": est.surface,
        "condition": est.etat,
        "estimated_price": int(est.prix_presentation),
        "price_per_m2": int(est.prix_m2) if est.prix_m2 else 0,
        "comparables": [
            {
                "address": r.adresse,
                "surface": est.surface,
                "price": int(est.prix_presentation),
                "price_per_m2": int(r.prix_m2)
            } for r in sold[:3]
        ]
    })


# ----------------------- ESTIMATIONS HISTORIQUE & GESTION -----------------------
@app.route("/estimations")
@login_required
def estimations_list():
    """Liste toutes les estimations avec recherche et filtres."""
    q = request.args.get("q", "").strip()
    quartier = request.args.get("quartier", "").strip()

    query = own_estimations()
    if q:
        query = query.filter(
            db.or_(
                Estimation.address.ilike(f"%{q}%"),
                Estimation.quartier.ilike(f"%{q}%")
            )
        )
    if quartier:
        query = query.filter_by(quartier=quartier)

    estimations = query.order_by(Estimation.created_at.desc()).all()
    return render_template("estimations_list.html", estimations=estimations,
                         quartiers=QUARTIERS, current_q=q, current_quartier=quartier)


@app.route("/estimation/<int:eid>/clone", methods=["POST"])
@login_required
def estimation_clone(eid):
    """Clone une estimation existante."""
    e = own_estimation_or_404(eid)
    new_e = Estimation(
        address=e.address, quartier=e.quartier, type_bien=e.type_bien,
        surface=e.surface, pieces=e.pieces, etage=e.etage, annee=e.annee,
        etat=e.etat, balcon=e.balcon, balcon_pond=e.balcon_pond,
        parking_nb=e.parking_nb, parking_val=e.parking_val,
        prix_m2=e.prix_m2, marge=e.marge, description=e.description,
        atouts=e.atouts, inconvenients=e.inconvenients, courtier=e.courtier,
        notes=f"Copie de {e.id}"
    )
    db.session.add(new_e)
    db.session.commit()
    log_audit("cloned", new_e.id, f"Clonée de {e.id}")
    return redirect(url_for("estimation_report", eid=new_e.id))


@app.route("/estimation/<int:eid>/notes", methods=["POST"])
@login_required
def estimation_notes(eid):
    """Éditer les notes d'une estimation."""
    e = own_estimation_or_404(eid)
    e.notes = request.form.get("notes", "")
    db.session.commit()
    log_audit("updated_notes", e.id)
    flash("Notes mises à jour.")
    return redirect(url_for("estimation_report", eid=e.id))


APARTMENT_TYPES = {"Appartement", "Duplex", "Attique", "Penthouse", "Triplex"}
HOUSE_TYPES = {"Maison individuelle", "Villa", "Maison"}


def _classify_type(type_bien):
    if not type_bien:
        return "autres"
    if type_bien in APARTMENT_TYPES:
        return "appartements"
    if type_bien in HOUSE_TYPES:
        return "maisons"
    return "autres"


@app.route("/classeur")
@login_required
def classeur():
    """Archive de toutes les estimations de l'utilisateur, groupée par quartier puis catégorie."""
    estimations = own_estimations().order_by(Estimation.created_at.desc()).all()
    grouped = {}  # quartier -> {"appartements": [], "maisons": [], "autres": []}
    for e in estimations:
        q = e.quartier or "Sans quartier"
        grouped.setdefault(q, {"appartements": [], "maisons": [], "autres": []})
        grouped[q][_classify_type(e.type_bien)].append(e)

    # Ordre voulu : les 3 quartiers principaux d'abord, puis le reste alphabétique
    priority = ["Champel", "Eaux-Vives", "Miremont"]
    ordered_quartiers = [q for q in priority if q in grouped]
    ordered_quartiers += sorted(q for q in grouped if q not in priority)

    return render_template(
        "classeur.html",
        grouped=grouped,
        quartiers=ordered_quartiers,
        total=len(estimations),
    )


@app.route("/dashboard")
@login_required
def dashboard():
    """Tableau de bord de l'utilisateur — ses estimations et un CTA nouvelle estimation."""
    uid = session.get("user_id")
    total = own_estimations().count()
    # prix_presentation est une @property Python : on approxime en SQL par prix_m2*surface*(1+marge)
    by_quartier = db.session.query(
        Estimation.quartier,
        db.func.count(Estimation.id),
        db.func.avg(Estimation.prix_m2),
        db.func.avg(Estimation.prix_m2 * Estimation.surface * (1 + Estimation.marge))
    ).filter(Estimation.user_id == uid).group_by(Estimation.quartier).all()

    recent = own_estimations().order_by(Estimation.created_at.desc()).limit(10).all()

    # Audit logs restreints aux estimations de l'utilisateur.
    own_ids = [row[0] for row in db.session.query(Estimation.id).filter_by(user_id=uid).all()]
    logs = (AuditLog.query.filter(AuditLog.estimation_id.in_(own_ids))
            .order_by(AuditLog.created_at.desc()).limit(20).all()) if own_ids else []

    return render_template("dashboard.html", total=total, by_quartier=by_quartier,
                         recent=recent, logs=logs, user=current_user())


@app.route("/estimation/<int:eid>/export.pdf")
@login_required
def estimation_export_pdf(eid):
    """Exporte une estimation en PDF (via HTML printable)."""
    e = own_estimation_or_404(eid)
    _, pool = ref_for(e.quartier, e.address)
    sold = [r for r in pool if r.kind in ("sold", "retenu")]
    forsale = [r for r in pool if r.kind == "forsale"]
    html = build_report(e, sold, forsale)

    return render_template("export_pdf.html", e=e, report_html=html)


@app.route("/import-csv", methods=["GET", "POST"])
@login_required
def import_csv():
    """Importe des estimations depuis un fichier CSV."""
    if request.method == "POST":
        if "file" not in request.files:
            flash("Aucun fichier sélectionné.")
            return redirect(url_for("import_csv"))

        file = request.files["file"]
        if not file.filename.endswith(".csv"):
            flash("Veuillez uploader un fichier CSV.")
            return redirect(url_for("import_csv"))

        try:
            stream = io.StringIO(file.read().decode("utf-8"))
            reader = csv.DictReader(stream)

            count = 0
            for row in reader:
                def num(k, d=0.0):
                    try:
                        v = row.get(k, "")
                        return float(str(v).replace("'", "").replace(",", ".") or d)
                    except (ValueError, TypeError):
                        return d

                est = Estimation(
                    address=row.get("address", ""),
                    quartier=row.get("quartier", ""),
                    type_bien=row.get("type_bien", ""),
                    surface=num("surface"),
                    pieces=row.get("pieces", ""),
                    etage=row.get("etage", ""),
                    annee=row.get("annee", ""),
                    etat=row.get("etat", ""),
                    balcon=num("balcon"),
                    balcon_pond=num("balcon_pond", 0.5),
                    parking_nb=int(num("parking_nb")),
                    parking_val=num("parking_val"),
                    prix_m2=num("prix_m2"),
                    marge=num("marge", 0.07),
                    description=row.get("description", ""),
                    atouts=row.get("atouts", ""),
                    inconvenients=row.get("inconvenients", ""),
                    courtier=row.get("courtier", ""),
                    notes=row.get("notes", "")
                )
                db.session.add(est)
                count += 1

            db.session.commit()
            flash(f"{count} estimations importées avec succès!")
            return redirect(url_for("estimations_list"))
        except Exception as e:
            flash(f"Erreur lors de l'import: {str(e)}")
            return redirect(url_for("import_csv"))

    return render_template("import_csv.html")


@app.route("/theme/<theme>")
@login_required
def set_theme(theme):
    """Définit le thème (dark/light)."""
    if theme in ["dark", "light"]:
        session["theme"] = theme
    return redirect(request.referrer or url_for("index"))


@app.route("/estimation/<int:eid>/export.csv")
@login_required
def estimation_export_csv(eid):
    """Exporte une estimation en CSV."""
    e = own_estimation_or_404(eid)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Propriété", "Valeur"])
    writer.writerow(["Adresse", e.address])
    writer.writerow(["Quartier", e.quartier])
    writer.writerow(["Type de bien", e.type_bien])
    writer.writerow(["Surface", e.surface])
    writer.writerow(["Pièces", e.pieces])
    writer.writerow(["État", e.etat])
    writer.writerow(["Balcon", e.balcon])
    writer.writerow(["Parking", e.parking_nb])
    writer.writerow(["Prix/m²", e.prix_m2])
    writer.writerow(["Valeur estimée", e.prix_presentation])
    writer.writerow(["Notes", e.notes])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"estimation_{e.id}.csv"
    )


@app.route("/export-all.csv")
@login_required
def export_all_csv():
    """Exporte toutes les estimations de l'utilisateur en CSV."""
    estimations = own_estimations().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "address", "quartier", "type_bien", "surface", "pieces", "etage", "annee",
        "etat", "balcon", "balcon_pond", "parking_nb", "parking_val", "prix_m2",
        "marge", "description", "atouts", "inconvenients", "courtier", "notes"
    ])

    for e in estimations:
        writer.writerow([
            e.address, e.quartier, e.type_bien, e.surface, e.pieces, e.etage, e.annee,
            e.etat, e.balcon, e.balcon_pond, e.parking_nb, e.parking_val, e.prix_m2,
            e.marge, e.description, e.atouts, e.inconvenients, e.courtier, e.notes
        ])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"estimations_all.csv"
    )


# ----------------------- FILTRES ET CONTEXTE -----------------------
@app.context_processor
def inject_helpers():
    """Injecte les fonctions helper dans les templates."""
    return dict(get_logo_path=get_logo_path)


@app.template_filter("chf")
def chf(v):
    try:
        return "{:,.0f}".format(float(v)).replace(",", "'")
    except (ValueError, TypeError):
        return "—"


@app.template_filter("m2")
def m2(v):
    try:
        s = "{:,.1f}".format(float(v)).replace(",", "'")
        return s.rstrip("0").rstrip(".")
    except (ValueError, TypeError):
        return "—"


def seed():
    db.create_all()
    if RefPrice.query.first():
        return
    # Références prix/m² APPARTEMENTS, extraites des estimations LP fournies
    data = [
        # quartier, adresse, prix_m2, annee, source, kind
        ("Pâquis", "Abraham-Gevray 1 (lots 3.03/4.03)", 15527, "2025", "Estimation Gevray 1", "sold"),
        ("Pâquis", "Abraham-Gevray 1 (lots 5.05/6.04)", 19567, "2024", "Estimation Gevray 1", "sold"),
        ("Pâquis", "Abraham-Gevray 1 (lot 5.07)", 19215, "2024", "Estimation Gevray 1", "sold"),
        ("Pâquis", "Abraham-Gevray 1 (attique 10.01/9.01)", 29319, "2024", "Estimation Gevray 1", "sold"),
        ("Pâquis", "Abraham-Gevray 1 (lots 5.02/6.02)", 21343, "2022", "Estimation Gevray 1", "sold"),
        ("Pâquis", "Abraham-Gevray 1 (retenu)", 22000, "2026", "Estimation Gevray 1 — prix retenu", "retenu"),
        ("Eaux-Vives", "Rue Abraham-Constantin 4-6", 19494, "2024", "Estimation Florissant 47", "sold"),
        ("Eaux-Vives", "Avenue Alfred-Bertrand 13", 21531, "2024", "Estimation Florissant 47", "sold"),
        ("Eaux-Vives", "Avenue Peschier 24", 15918, "2023", "Estimation Florissant 47", "sold"),
        ("Eaux-Vives", "Route de Florissant 47 (retenu)", 19876, "2025", "Estimation Florissant 47 — prix retenu", "retenu"),
        ("Champel", "Avenue de Champel 14", 21120, "2023", "Estimation Florissant 47", "sold"),
        ("Champel", "Avenue de Champel 14", 22719, "2022", "Estimation Florissant 47", "sold"),
        ("Champel", "Rue Monnier 1", 18478, "2023", "Estimation Florissant 47", "forsale"),
        ("Champel", "Chemin Tour de Champel 12", 17883, "2025", "Estimation Champel 60", "forsale"),
        ("Champel", "Avenue de Miremont 30 (retenu)", 14000, "2026", "Estimation Miremont 30 — prix retenu", "retenu"),
    ]
    for q, a, pm, an, src, k in data:
        db.session.add(RefPrice(quartier=q, adresse=a, prix_m2=pm, annee=an, source=src, kind=k))
    db.session.commit()


def _migrate():
    """Ajoute les colonnes ajoutées après le déploiement initial (sans framework de migration)."""
    from sqlalchemy import text
    try:
        db.session.execute(text("ALTER TABLE estimation ADD COLUMN user_id INTEGER"))
        db.session.commit()
    except Exception:
        db.session.rollback()


with app.app_context():
    db.create_all()
    _migrate()
    seed()


if __name__ == "__main__":
    app.run(debug=True, port=5002)
