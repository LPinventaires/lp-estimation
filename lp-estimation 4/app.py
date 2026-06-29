import os
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
    logo = os.path.exists(os.path.join(app.static_folder, "logo.jpg"))
    return render_template("login.html", logo=logo)


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
@login_required
def index():
    estimations = Estimation.query.order_by(Estimation.created_at.desc()).all()
    return render_template("index.html", estimations=estimations, quartiers=QUARTIERS)


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
    _, pool = ref_for(e.quartier, e.address)
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


with app.app_context():
    db.create_all()
    seed()


if __name__ == "__main__":
    app.run(debug=True, port=5002)
