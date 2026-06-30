# LP · Estimation hédoniste

Site interne (Flask) pour estimer un **appartement** par la méthode hédoniste, dans l'esprit
de LP inventaire : thème gris foncé, épuré, **accès par code**.

Tu déposes une fiche ou un rapport (PDF / PPTX / DOCX) → les données du bien sont extraites et
pré-remplies → tu valides → l'app génère un **rapport hédoniste** rédigé dans le style LP
(méthode, comparables, valeur vénale, prix de présentation, réserves), imprimable en PDF.

## Lancer en local

```bash
cd lp-estimation
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py        # http://localhost:5002
```

Code d'accès par défaut : **`LP-estimation`** (à changer via la variable `APP_PASSWORD`).

## Comment ça marche

- **Glisser-déposer** : `parsing.py` lit le fichier (PDF/PPTX/DOCX) et repère adresse, quartier,
  surface, étage, état, parkings…
- **Localité → prix/m²** : la table `Références prix/m²` (onglet dédié) contient les comparables
  appartements extraits des estimations LP. L'app propose le prix/m² médian de l'adresse exacte,
  sinon du quartier — que le courtier ajuste.
- **Calcul** (déterministe) :
  `valeur vénale = prix/m² × surface + prix/m² × extérieurs × pondération + parkings`
  `prix de présentation = arrondi(valeur vénale × (1 + marge))`
- **Rapport** : `report.py` reprend mot pour mot les formulations standards des rapports LP et
  insère les éléments du bien.

## Données

L'app ne porte que sur les **appartements**. La table de référence est pré-remplie avec les
prix/m² appartements extraits des estimations fournies (Pâquis/Gevray, Eaux-Vives/Florissant,
Champel…). Plus tu ajoutes de références, plus l'estimation par localité est fiable.

> Aucun lien avec Dropbox ni aucun autre dossier : seules les estimations comptent.

## Déploiement Railway

Même principe que l'app Léonard : repo GitHub → projet Railway + service PostgreSQL →
variables `DATABASE_URL`, `SECRET_KEY`, `APP_PASSWORD`. Le `Procfile` lance `gunicorn app:app`.

## Pistes d'évolution

- Rédaction automatique des parties descriptives (situation, atouts) dans le style LP via l'API Claude.
- OCR des tableaux de comparables encore en image dans certains PDF, pour enrichir la base.
- Export `.docx` au gabarit exact LP (logo, photos, mise en page).
