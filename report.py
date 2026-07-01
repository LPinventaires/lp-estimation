"""Génération du rapport d'estimation hédoniste au style LEONARD PROPERTIES.

Les blocs de texte standards reprennent mot pour mot la syntaxe des rapports LP
fournis en exemple (méthode hédoniste, conseils, calcul, prix de présentation, réserves).
Les chiffres sont calculés de façon déterministe ; seule la rédaction est mise en forme.
"""

QUARTIERS = [
    "Champel", "Eaux-Vives", "Miremont",
]


def _chf(v):
    try:
        return "{:,.0f}".format(round(float(v))).replace(",", "'")
    except (ValueError, TypeError):
        return "—"


def _m2(v):
    try:
        s = "{:,.1f}".format(float(v)).replace(",", "'")
        return s.rstrip("0").rstrip(".")
    except (ValueError, TypeError):
        return "—"


METHODE = (
    "Pour évaluer un appartement en PPE, seule la méthode hédoniste est pertinente. "
    "Nous appliquons cette méthode comparative, qui s’appuie sur un historique des ventes "
    "que nous tenons soigneusement à jour depuis 2008. Dans le cadre de la méthode hédoniste, "
    "nous comparons les prix d’achat de biens (vendus ou à vendre) aussi similaires que possible, "
    "en tenant compte de leurs caractéristiques. Les paramètres déterminants incluent : "
    "l’emplacement, la qualité de construction, l’architecture, le coefficient d’impôt, le vis-à-vis, "
    "le voisinage direct, l’âge et l’état du bâtiment, la vue, le calme, l’accès, les prestations du "
    "bâtiment, l’étage, le stationnement, ainsi que d’autres critères de qualité (ascenseur, sécurité, "
    "piscine, balcons, etc.). Nos experts évaluent ces différents paramètres en fonction de leur "
    "importance pour déterminer la valeur vénale."
)

VENALE_INTRO = (
    "Notre objectif est de déterminer un prix au m² pertinent pour estimer la valeur de votre "
    "appartement. Pour y parvenir, nous analysons les appartements comparables récemment vendus "
    "afin d’établir un prix moyen au m². Nous réalisons également cette analyse avec des appartements "
    "similaires actuellement en vente, donc en compétition avec votre bien. Il est important de noter "
    "que les prix demandés des appartements à vendre sont toujours supérieurs aux prix effectifs des "
    "transactions."
)

RESERVES = (
    "Tout élément ou fait qui n’aurait pas été porté à la connaissance de l’expert et qui pourrait "
    "modifier son appréciation, est dûment réservé. Nous portons à votre attention le fait qu’en "
    "fonction de l’évolution du marché, cette estimation n’a qu’une valeur limitée dans le temps."
)


def _comp_table(rows, prix_label="Prix"):
    if not rows:
        return '<p class="empty">Aucun comparable enregistré pour cette localité.</p>'
    body = ""
    vals = []
    for r in rows:
        if r.prix_m2:
            vals.append(r.prix_m2)
        body += (f"<tr><td>{r.annee or ''}</td><td>{r.adresse or ''}</td>"
                 f"<td class='num'>{_chf(r.prix_m2)}</td><td>{r.source or ''}</td></tr>")
    moy = sum(vals) / len(vals) if vals else None
    foot = (f"<tfoot><tr><td colspan='2'>Prix/m² moyen</td>"
            f"<td class='num'>{_chf(moy)}</td><td></td></tr></tfoot>") if moy else ""
    return (f"<table><thead><tr><th>Date</th><th>Adresse</th>"
            f"<th class='num'>{prix_label}/m²</th><th>Réf.</th></tr></thead>"
            f"<tbody>{body}</tbody>{foot}</table>")


def build_report(e, sold, forsale):
    """Construit le corps HTML du rapport, mis en forme façon LP."""
    type_bien = e.type_bien or "Appartement"
    intro = (
        f"J’ai le plaisir de vous faire parvenir notre estimation de valeur vénale pour votre "
        f"{type_bien.lower()}{', sis ' + e.address if e.address else ''}, ainsi qu’une stratégie de "
        f"commercialisation adaptée à vos objectifs."
    )

    desc = e.description or (
        f"Ce {type_bien.lower()}"
        + (f" de {_m2(e.surface)} m²" if e.surface else "")
        + (f", situé au {e.etage} étage" if e.etage else "")
        + (f", dans le quartier de {e.quartier}" if e.quartier else "")
        + (f", se présente dans un état {e.etat.lower()}." if e.etat else ".")
    )

    # Lignes du calcul
    lignes = (f"<tr><td>Surface habitable</td><td class='num'>{_m2(e.surface)}</td>"
              f"<td class='num'>{_chf(e.prix_m2)} · 100 %</td>"
              f"<td class='num'>{_chf(e.val_principale)}</td></tr>")
    if e.balcon:
        lignes += (f"<tr><td>Balcon / loggia / terrasse</td><td class='num'>{_m2(e.balcon)}</td>"
                   f"<td class='num'>{_chf(e.prix_m2)} · {round((e.balcon_pond or 0)*100)} %</td>"
                   f"<td class='num'>{_chf(e.val_balcon)}</td></tr>")
    if e.parking_nb:
        lignes += (f"<tr><td>Parking ({e.parking_nb} × {_chf(e.parking_val)})</td>"
                   f"<td class='num'>—</td><td class='num'>—</td>"
                   f"<td class='num'>{_chf(e.val_parking)}</td></tr>")

    atouts_html = ""
    if e.atouts:
        items = "".join(f"<li>{a.strip()}</li>" for a in e.atouts.split("\n") if a.strip())
        atouts_html += f"<h3>Atouts</h3><ul>{items}</ul>"
    if e.inconvenients:
        items = "".join(f"<li>{a.strip()}</li>" for a in e.inconvenients.split("\n") if a.strip())
        atouts_html += f"<h3>Points d’attention</h3><ul>{items}</ul>"

    return f"""
<p>Cher Monsieur, Chère Madame,</p>
<p>{intro}</p>

<h2>1. Méthode d’évaluation</h2>
<p>{METHODE}</p>

<h2>2. Descriptif du bien</h2>
<p>{desc}</p>
{atouts_html}

<h2>3. Estimation hédoniste — comparables</h2>
<h3>A. Comparables directs / transactions d’appartements vendus</h3>
{_comp_table(sold)}
<h3>B. Comparables directs / appartements actuellement en vente</h3>
{_comp_table(forsale, "Prix demandé")}
<p>En tenant compte des caractéristiques des propriétés similaires vendues et à vendre, et de la
moyenne des prix au m² obtenue, nous retenons un prix au m² de <strong>CHF {_chf(e.prix_m2)}/m²</strong>.</p>

<h2>4. Calcul de la valeur vénale</h2>
<p>{VENALE_INTRO}</p>
<table>
  <thead><tr><th>Bâtiment</th><th class="num">Surface (m²)</th><th class="num">Prix/m² · pondération</th><th class="num">Total</th></tr></thead>
  <tbody>{lignes}</tbody>
  <tfoot><tr><td colspan="3">TOTAL VALEUR VÉNALE</td><td class="num">CHF {_chf(e.valeur_venale)}</td></tr></tfoot>
</table>

<h2>5. Valeur vénale et prix de présentation</h2>
<table class="recap">
  <tr><td>Valeur de marché (méthode hédoniste comparative)</td><td class="num">CHF {_chf(e.valeur_venale)}</td></tr>
  <tr class="hl"><td>Prix de présentation suggéré (marge de négociation et commission incluses)</td><td class="num">CHF {_chf(e.prix_presentation)}</td></tr>
</table>
<p>Ce prix serait considéré comme un prix de présentation initial ambitieux et réaliste, qui nous
permettrait de jauger le marché pour ensuite procéder à une adaptation si nécessaire.</p>

<h2>Conclusions &amp; réserves</h2>
<p>{RESERVES}</p>
<p class="sign">{e.courtier or 'LEONARD PROPERTIES SA'}</p>
"""
