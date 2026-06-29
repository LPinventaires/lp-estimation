import os
import json
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify)
from flask_sqlalchemy import SQLAlchemy

from parsing import extract_text, extract_fields
from report import build_report, QUARTIERS

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024

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


# ----------------------- AUTH -----------------------
def login_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if APP_PASSWORD and not session.get("auth"):
            return redirect(url_for("login"))
        return f(*a, **k)
    return wrap


@app.route("/login", methods=["GET", "POST"])
def login():
    if not APP_PASSWORD:
        return redirect(url_for("index"))
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session["auth"] = True
            return redirect(url_for("index"))
        flash("Code incorrect.")
    return render_template("login.html", logo=True)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ----------------------- HELPERS -----------------------
def _addr_match(adresse, r):
    if not adresse or not r.adresse:
        return False
    a = adresse.lower().strip()
    b = r.adresse.lower().strip()
    key = a[:14]
    return key in b or b[:14] in a


def _extract_quarter_from_address(address):
    """Essaie d'extraire le quartier depuis l'adresse (comme 'Champel' dans 'Avenue de Champel 14')."""
    if not address:
        return None
    addr_lower = address.lower()
    for q in QUARTIERS:
        if q.lower() in addr_lower:
            return q
    return None


def _normalize_type(type_bien):
    """Normalise le type de bien pour comparaison."""
    if not type_bien:
        return "appartement"
    t = type_bien.lower().strip()
    if "villa" in t:
        return "villa"
    if "triplex" in t:
        return "triplex"
    if "duplex" in t:
        return "duplex"
    if "attique" in t:
        return "attique"
    return "appartement"


def ref_for(quartier, adresse, type_bien=None, surface=None):
    """Retourne (prix_m2 proposé, comparables intelligents de la localité).

    Recherche intelligente :
    1. Même type de bien (priorité haute)
    2. Même quartier (priorité 1)
    3. Surface proche (±15% si on a la donnée) — bonus si présent
    4. Année récente (bonus)

    Retourne les 2-3 meilleurs comparables basés sur score de pertinence.
    """
    refs = RefPrice.query.all()
    norm_type = _normalize_type(type_bien)

    # Essayer d'extraire le quartier de l'adresse si non fourni
    extracted_q = _extract_quarter_from_address(adresse)
    search_quartier = quartier or extracted_q

    # Filtrer par type de bien
    refs_by_type = [r for r in refs if _normalize_type(r.kind) == "sold" or r.kind in ("sold", "retenu")]

    # Score de pertinence pour chaque référence
    scored_refs = []
    for r in refs_by_type:
        score = 0
        reasons = []

        # Type identique (10 pts)
        if hasattr(r, 'type_bien') and _normalize_type(r.type_bien) == norm_type:
            score += 10
            reasons.append("type_match")

        # Même quartier (8 pts)
        if search_quartier and r.quartier and r.quartier.lower() == search_quartier.lower():
            score += 8
            reasons.append("quartier")

        # Adresse exacte (12 pts)
        if _addr_match(adresse, r):
            score += 12
            reasons.append("adresse")

        # Surface proche (5 pts, si on a la donnée)
        if surface and hasattr(r, 'surface') and r.surface:
            diff = abs(r.surface - surface) / surface
            if diff <= 0.15:
                score += 5
                reasons.append("surface")

        # Année récente (3 pts bonus par année après 2020)
        if r.annee:
            try:
                year = int(r.annee)
                if year >= 2020:
                    score += min(3 * (year - 2019), 9)
                    reasons.append("recent")
            except (ValueError, TypeError):
                pass

        if score > 0 and r.prix_m2:
            scored_refs.append((r, score, reasons))

    # Trier par score (meilleurs en premier)
    scored_refs.sort(key=lambda x: x[1], reverse=True)

    # Pool : prendre les 3 meilleurs, puis compléter avec adresse/quartier
    pool = [r for r, _, _ in scored_refs[:3]]

    # Ajouter les références non scorées mais pertinentes (adresse ou quartier)
    by_addr = [r for r in refs if _addr_match(adresse, r) and r not in pool]
    by_quartier = [r for r in refs if search_quartier and r.quartier and
                   r.quartier.lower() == search_quartier.lower() and r not in pool]

    seen = {r.id for r in pool}
    for r in by_addr + by_quartier:
        if r.id not in seen and len(pool) < 8:
            pool.append(r)
            seen.add(r.id)

    def median(vals):
        vals = sorted(vals)
        return vals[len(vals) // 2] if vals else None

    addr_sold = [r.prix_m2 for r in by_addr if r.prix_m2 and r.kind in ("sold", "retenu")]
    quartier_sold = [r.prix_m2 for r in by_quartier if r.prix_m2 and r.kind in ("sold", "retenu")]
    proposed = median(addr_sold) or median(quartier_sold)
    return proposed, pool


# ----------------------- ROUTES -----------------------
CLASSEUR = [
    ("Appartements", ["Appartement"]),
    ("Maisons", ["Maison", "Villa"]),
    ("Attiques", ["Attique"]),
    ("Duplex & Triplex", ["Duplex", "Triplex"]),
]


def _classeur(estimations):
    groups = {label: [] for label, _ in CLASSEUR}
    autres = []
    for e in estimations:
        t = (e.type_bien or "").strip().lower()
        placed = False
        for label, kinds in CLASSEUR:
            if any(k.lower() in t for k in kinds):
                groups[label].append(e)
                placed = True
                break
        if not placed:
            autres.append(e)
    if autres:
        groups["Autres"] = autres
    return groups


@app.route("/")
@login_required
def index():
    estimations = Estimation.query.order_by(Estimation.created_at.desc()).all()
    groups = _classeur(estimations)
    return render_template("index.html", estimations=estimations,
                           groups=groups, quartiers=QUARTIERS)


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    """Reçoit un fichier déposé, en extrait les champs, pré-remplit le formulaire."""
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Aucun fichier"}), 400
    text = extract_text(file)
    fields = extract_fields(text)
    proposed, pool = ref_for(fields.get("quartier"), fields.get("address"),
                             fields.get("type_bien"), fields.get("surface"))
    if proposed and not fields.get("prix_m2"):
        fields["prix_m2"] = round(proposed)
    fields["_refs"] = [{"adresse": r.adresse, "quartier": r.quartier,
                        "prix_m2": r.prix_m2, "annee": r.annee, "kind": r.kind} for r in pool]
    return jsonify(fields)


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
        e = Estimation(
            address=f.get("address"), quartier=f.get("quartier"),
            type_bien=f.get("type_bien"), surface=num("surface"),
            pieces=f.get("pieces"), etage=f.get("etage"), annee=f.get("annee"),
            etat=f.get("etat"), balcon=num("balcon"), balcon_pond=num("balcon_pond", 0.5),
            parking_nb=int(num("parking_nb")), parking_val=num("parking_val"),
            prix_m2=num("prix_m2"), marge=num("marge", 0.07),
            description=f.get("description"), atouts=f.get("atouts"),
            inconvenients=f.get("inconvenients"), courtier=f.get("courtier"))
        db.session.add(e)
        db.session.commit()
        return redirect(url_for("estimation_report", eid=e.id))
    return render_template("estimation_form.html", quartiers=QUARTIERS, prefill={})


@app.route("/estimation/<int:eid>")
@login_required
def estimation_report(eid):
    e = Estimation.query.get_or_404(eid)
    _, pool = ref_for(e.quartier, e.address, e.type_bien, e.surface)
    sold = [r for r in pool if r.kind in ("sold", "retenu")]
    forsale = [r for r in pool if r.kind == "forsale"]
    html = build_report(e, sold, forsale)
    return render_template("report.html", e=e, report_html=html)


@app.route("/estimation/<int:eid>/delete", methods=["POST"])
@login_required
def estimation_delete(eid):
    e = Estimation.query.get_or_404(eid)
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


# ----------------------- FILTRES -----------------------
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


def _load_comparables_from_json():
    """Charge les 184 comparables du JSON comparables.json."""
    comparables = []
    try:
        # Chercher le JSON dans le répertoire courant, puis parent
        json_path = os.path.join(os.path.dirname(__file__), 'comparables.json')
        if not os.path.exists(json_path):
            json_path = os.path.join(os.path.dirname(__file__), '..', 'comparables.json')
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Parcourir les propriétés dans 'all_properties' ou 'by_type'
                all_props = data.get('all_properties', [])
                if not all_props:
                    # Fallback si 'all_properties' n'existe pas
                    for type_key, props in data.get('by_type', {}).items():
                        all_props.extend(props)

                for prop in all_props:
                    address = prop.get('address', '')
                    prop_type = prop.get('type', 'Appartement')
                    year = prop.get('year')
                    if address and address.strip() and not address.startswith('~$'):
                        # Essayer d'extraire le quartier de l'adresse
                        quartier = _extract_quarter_from_address(address)
                        comparables.append({
                            'quartier': quartier,
                            'adresse': address,
                            'prix_m2': None,  # À enrichir plus tard
                            'annee': str(year) if year else None,
                            'source': 'Comparables JSON',
                            'kind': 'sold',
                            'type_bien': prop_type
                        })
    except Exception as e:
        print(f"Erreur lors du chargement du JSON: {e}")
    return comparables


def seed():
    db.create_all()
    if RefPrice.query.first():
        return

    # 15 Références prix/m² SEED DATA — extraites des estimations LP fournies
    seed_data = [
        # quartier, adresse, prix_m2, annee, source, kind, type_bien
        ("Pâquis", "Abraham-Gevray 1 (lots 3.03/4.03)", 15527, "2025", "Estimation Gevray 1", "sold", "Appartement"),
        ("Pâquis", "Abraham-Gevray 1 (lots 5.05/6.04)", 19567, "2024", "Estimation Gevray 1", "sold", "Appartement"),
        ("Pâquis", "Abraham-Gevray 1 (lot 5.07)", 19215, "2024", "Estimation Gevray 1", "sold", "Appartement"),
        ("Pâquis", "Abraham-Gevray 1 (attique 10.01/9.01)", 29319, "2024", "Estimation Gevray 1", "sold", "Attique"),
        ("Pâquis", "Abraham-Gevray 1 (lots 5.02/6.02)", 21343, "2022", "Estimation Gevray 1", "sold", "Appartement"),
        ("Pâquis", "Abraham-Gevray 1 (retenu)", 22000, "2026", "Estimation Gevray 1 — prix retenu", "retenu", "Appartement"),
        ("Eaux-Vives", "Rue Abraham-Constantin 4-6", 19494, "2024", "Estimation Florissant 47", "sold", "Appartement"),
        ("Eaux-Vives", "Avenue Alfred-Bertrand 13", 21531, "2024", "Estimation Florissant 47", "sold", "Appartement"),
        ("Eaux-Vives", "Avenue Peschier 24", 15918, "2023", "Estimation Florissant 47", "sold", "Appartement"),
        ("Eaux-Vives", "Route de Florissant 47 (retenu)", 19876, "2025", "Estimation Florissant 47 — prix retenu", "retenu", "Appartement"),
        ("Champel", "Avenue de Champel 14", 21120, "2023", "Estimation Florissant 47", "sold", "Appartement"),
        ("Champel", "Avenue de Champel 14", 22719, "2022", "Estimation Florissant 47", "sold", "Appartement"),
        ("Champel", "Rue Monnier 1", 18478, "2023", "Estimation Florissant 47", "forsale", "Appartement"),
        ("Champel", "Chemin Tour de Champel 12", 17883, "2025", "Estimation Champel 60", "forsale", "Appartement"),
        ("Champel", "Avenue de Miremont 30 (retenu)", 14000, "2026", "Estimation Miremont 30 — prix retenu", "retenu", "Appartement"),
    ]

    # Charger les seed data
    for q, a, pm, an, src, k, typ in seed_data:
        db.session.add(RefPrice(quartier=q, adresse=a, prix_m2=pm, annee=an, source=src, kind=k))

    # Charger les 184 comparables du JSON
    json_comparables = _load_comparables_from_json()
    for comp in json_comparables:
        db.session.add(RefPrice(
            quartier=comp['quartier'],
            adresse=comp['adresse'],
            prix_m2=comp['prix_m2'],
            annee=comp['annee'],
            source=comp['source'],
            kind=comp['kind']
        ))

    db.session.commit()
    print(f"Database seeded: {len(seed_data)} seed data + {len(json_comparables)} comparables = {len(seed_data) + len(json_comparables)} total")


with app.app_context():
    db.create_all()
    seed()



# ----------------------- ROUTES CLASSEUR COMPARABLES -----------------------

@app.route("/classeur")
@login_required
def classeur():
    """Page interactive du classeur de comparables."""
    return render_template("classeur.html")


@app.route("/api/comparables")
@login_required
def api_comparables():
    """API JSON pour les comparables enrichis avec matching."""
    try:
        # Chercher le JSON matché dans le répertoire courant
        json_path = os.path.join(os.path.dirname(__file__), 'comparables_matched.json')
        if not os.path.exists(json_path):
            # Fallback sur enriched
            json_path = os.path.join(os.path.dirname(__file__), 'comparables_enriched.json')
        if not os.path.exists(json_path):
            json_path = os.path.join(os.path.dirname(__file__), 'comparables.json')

        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return jsonify(data)
        else:
            return jsonify({"error": "Comparables file not found"}), 404
    except Exception as e:
        print(f"Erreur API comparables: {e}")
        return jsonify({"error": str(e)}), 500



if __name__ == "__main__":
    app.run(debug=True, port=5002)
