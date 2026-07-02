# LP · Estimation hédoniste — Guide de reprise

> Document destiné à quelqu'un qui reprend le projet après Romain. Contient TOUT ce qu'il faut pour comprendre, opérer, faire évoluer le site. Lis-le en entier une première fois, puis reviens-y par section.

**Dernière mise à jour :** 2026-07-02
**Auteur initial :** Romain Marguet (`romain@lpsa.ch`)

---

## 1. À quoi sert le site

Outil interne à Leonard Properties (Genève) pour **estimer des biens immobiliers** par la méthode hédoniste. Les courtiers :

1. **Saisissent une description** du bien (adresse, quartier, surface, état…), soit à la main dans un formulaire, soit en collant un texte libre / déposant une photo — l'IA extrait alors les champs automatiquement.
2. **Reçoivent une estimation** (valeur vénale, prix/m², prix de présentation) calculée sur la base de **comparables filtrés** (même quartier, surface similaire, même type de bien).
3. **Consultent le rapport final** (imprimable en PDF, exportable en DOCX) qu'ils peuvent envoyer au client.

Le site est un **outil interne**, protégé par un mot de passe partagé pour toute l'équipe LP.

---

## 2. Les URLs importantes à connaître

| Ressource | URL |
|---|---|
| **Site en production** | https://web-production-fdba7.up.railway.app |
| **Dépôt de code (GitHub)** | https://github.com/LPinventaires/lp-estimation |
| **Branche déployée** | `main` (auto-deploy Railway à chaque push) |
| **Dashboard Railway** | https://railway.com/project/a0a7d9a7-330d-458f-a7d1-6b5f2e9fd245 |
| **Service web sur Railway** | Nom `web` (le connecté à GitHub) |
| **Base Postgres** | Service `Postgres` du même projet Railway |
| **Console API Anthropic** | https://console.anthropic.com (billing + gestion clé) |
| **Fichiers source des comparables** | `~/Library/CloudStorage/Dropbox/1. Staff (1)/1. Leonard Properties GE/23. Estimations/` |

**Nom du projet Railway** : `resplendent-flexibility` (généré aléatoirement par Railway au setup, jamais renommé).

---

## 3. Les mots de passe et clés d'accès

**Trois secrets** sont stockés dans les variables d'environnement du service `web` sur Railway. **Aucun** n'est dans le code — c'est important, ne les commit jamais dans le repo.

| Variable | À quoi ça sert | Comment la voir / rotater |
|---|---|---|
| `APP_PASSWORD` | Mot de passe partagé pour se connecter au site | Dashboard Railway → service `web` → onglet **Variables** → cliquer l'œil 👁 pour révéler. À la date du mémo : `Champel26`. |
| `ANTHROPIC_API_KEY` | Clé API pour Claude Opus 4.8 (extraction depuis texte/photo + génération de descriptions) | Générée sur https://console.anthropic.com/settings/keys. Si compromise : révoque sur console.anthropic.com, crée une nouvelle, mets-la dans la variable Railway. |
| `SECRET_KEY` | Clé Flask pour signer les cookies de session | Chaîne aléatoire de 48+ caractères. Si rotée, tous les utilisateurs sont déconnectés. Non critique. |
| `DATABASE_URL` | Chaîne de connexion Postgres | **Injectée automatiquement** par Railway (référence au service Postgres). Ne pas y toucher. |

### Comment changer le mot de passe du site
1. Dashboard Railway → service `web` → Variables → clique sur `APP_PASSWORD`
2. Édite la valeur → **Save**
3. Railway redéploie automatiquement en ~1 minute
4. Communique le nouveau mot de passe à l'équipe

### Comment rotater la clé Anthropic (si suspicion de fuite)
1. https://console.anthropic.com/settings/keys → clique la corbeille à côté de l'ancienne clé
2. **Create Key** → nomme-la (ex. `lp-estimation-2026-Q3`)
3. Copie la valeur (elle ne s'affiche qu'une fois)
4. Dashboard Railway → service `web` → Variables → édite `ANTHROPIC_API_KEY` → colle la nouvelle valeur → Save

---

## 4. Comment déployer un changement

Le site se met à jour **automatiquement** à chaque push sur la branche `main` du repo GitHub.

### Depuis le Mac où le repo est cloné (`~/Desktop/lp-estimation`)

```bash
cd ~/Desktop/lp-estimation

# Modifier le code / les templates / les CSVs de comparables
# Puis :
git add .
git commit -m "Description claire du changement"
git push origin master:main
```

⚠️ **Attention** : la branche locale s'appelle `master` mais la branche distante est `main`. C'est pourquoi le push utilise `master:main`.

Railway détecte le push GitHub, rebuild le conteneur (Python 3.13, `pip install -r requirements.txt`, `gunicorn app:app`) et redéploie. Ça prend **1 à 3 minutes** selon la lourdeur des dépendances installées.

### Comment savoir si un déploiement a marché
1. Attends 2 minutes
2. Ouvre https://web-production-fdba7.up.railway.app → doit charger normalement
3. Si erreur 500 ou "Application error" : va sur le dashboard Railway → service `web` → onglet **Logs** → cherche la stack trace Python

---

## 5. Stack technique

| Composant | Version | Rôle |
|---|---|---|
| **Python** | 3.13 (imposé par Railway/nixpacks) | Runtime |
| **Flask** | 3.0.3 | Framework web |
| **Flask-SQLAlchemy** | 3.1.1 | ORM pour Postgres |
| **gunicorn** | 22.0.0 | Serveur WSGI (production) |
| **psycopg2-binary** | 2.9.10 | Driver Postgres |
| **anthropic** | 0.99.0 | Client Claude API |
| **pydantic** | Auto | Validation des schémas d'extraction Claude |
| **Pillow** | 11.0.0 | Redimensionnement des images avant envoi à Claude |
| **python-docx** | 1.1.2 | Export Word |
| **python-pptx** | 0.6.23 | Réserve, non utilisé activement |
| **pdfminer.six** | 20231228 | Extraction texte des PDF déposés |
| **openpyxl** | Dev-only | Consolidation des xlsx Dropbox (script one-shot) |
| **Postgres** | 15+ (via Railway) | Base de données |
| **Claude Opus 4.8** | `claude-opus-4-8` | Modèle IA utilisé pour extraction et rédaction (le plus capable d'Anthropic à ce jour) |

Toutes les dépendances Python sont dans `requirements.txt` à la racine du repo.

---

## 6. Structure du code

```
lp-estimation/
├── app.py                    # Flask app monolithique — TOUT est dedans
├── report.py                 # Génération du "corps du rapport" (méthode LP, texte)
├── parsing.py                # Extraction PDF/DOCX/PPTX déposés
├── requirements.txt          # Deps Python
├── Procfile                  # Commande de démarrage Railway : `web: gunicorn app:app`
├── railway.toml              # Config Railway (healthcheck /healthz)
├── HANDOFF.md                # Ce fichier
├── README.md                 # Vue courte
├── DEPLOIEMENT.md            # Notes historiques
├── static/
│   ├── style.css             # CSS luxe (Playfair serif, palette or/beige/brun)
│   └── uploads/
│       └── leonard-logo.png  # Logo officiel LP (committé pour survivre aux redeploys)
├── templates/                # Jinja2 templates
│   ├── base.html             # Layout principal (header, nav, footer)
│   ├── landing.html          # Page d'accueil publique (avant login)
│   ├── login.html            # Page de connexion (mdp seul)
│   ├── dashboard.html        # Tableau de bord de l'utilisateur
│   ├── estimation_form.html  # Formulaire "Nouvelle estimation" (gros textarea + drag-drop)
│   ├── _form_fields.html     # Sous-template : les champs éditables du formulaire
│   ├── report.html           # Page rapport (synthèse + 3 comparables + corps LP)
│   ├── classeur.html         # Archive groupée par quartier/type
│   ├── estimations_list.html # Historique en tableau
│   ├── settings.html         # Page paramètres (upload logo)
│   └── ...                   # Autres écrans mineurs
└── data/
    ├── comparables.csv           # 10 comparables Belfin/Champel60 originaux
    ├── comparables-lp.csv        # 410 comparables consolidés depuis les xlsx Dropbox
    └── comparables_enrichis.csv  # 3 comparables riches avec descriptions
```

### Le fichier `app.py` — vue d'ensemble

C'est un fichier monolithique d'environ 1200 lignes organisé en sections :

1. **Imports** (lignes 1-15)
2. **Config Flask + upload folder** (15-40)
3. **Modèles SQLAlchemy** (40-140) — `RefPrice`, `Estimation`, `Setting`, `AuditLog`, `PriceAlert`, `User`
4. **Auth helpers** (140-170) — `login_required`, `current_user`, `own_estimations()`, `own_estimation_or_404`
5. **Utils** (170-260) — logo, allowed_file, log_audit, upload settings…
6. **Auth routes** (`/login`, `/signup`, `/logout`) — mot de passe partagé, user "Leonard Properties" créé au premier login
7. **Helpers IA** (270-450) — `PhotoExtraction` (Pydantic), `_extract_from_content`, `_generate_lp_description`, prompt d'extraction
8. **Système de comparables** (300-540) — `find_comparables()` avec fallback progressif, `_do_search`, `_same_type_category`, mapping `NEIGHBOR_QUARTIERS`
9. **Routes principales** :
   - `/` — landing ou redirect vers dashboard
   - `/upload` — analyse PDF/DOCX/PPTX
   - `/upload-photo` — analyse image via Claude Vision
   - `/analyze-text` — analyse texte libre via Claude
   - `/estimation/new` (POST) — création + rédaction auto de la description si vide
   - `/estimation/<id>` — rapport
   - `/estimation/<id>/export.pdf` / `.csv`
   - `/estimation/<id>/delete` / `/clone` / `/notes`
   - `/classeur` — archive
   - `/estimations` — liste
   - `/dashboard` — tableau de bord
   - `/references` — gestion de la base de comparables (admin)
   - `/settings` — logo
   - `/analysis` + `/quick-analyze` — analyse rapide de texte (endpoint JSON)
10. **Seed & migration** (fin du fichier) — création des tables au démarrage, ALTER TABLE pour les colonnes ajoutées après coup, chargement des CSVs de comparables

---

## 7. La base de données Postgres

### Tables principales

| Table | Rôle | Colonnes clés |
|---|---|---|
| `user` | Compte(s) utilisateurs | `id`, `email`, `password_hash`. Un seul user "Leonard Properties" en pratique (compte partagé). |
| `estimation` | Chaque estimation créée | `id`, `user_id`, `address`, `quartier`, `type_bien`, `surface`, `prix_m2`, `marge`, `description`, `atouts`, `inconvenients`… |
| `ref_price` | Base de comparables (marché + LP) | `id`, `quartier`, `adresse`, `type_bien`, `surface`, `prix_m2`, `prix_total`, `annee`, `source`, `description`, `reference` |
| `setting` | Paramètres app (logo) | `key`, `value` |
| `audit_log` | Historique des actions sur les estimations | `action`, `estimation_id`, `created_at`, `description` |
| `price_alert` | Alertes prix (non utilisé activement) | — |

### Comment les comparables arrivent en base
Au démarrage de l'app, la fonction `load_comparables_csv()` :
1. Supprime toutes les entrées `ref_price` qui ont une source parmi les CSVs (`CSV_FILES` dans `app.py`)
2. Relit chaque fichier `data/*.csv` et insère les lignes fraîchement

**Conséquence pratique** : modifier un CSV + push = les comparables se mettent à jour au prochain déploiement Railway. Pas besoin de toucher à la DB à la main.

### Backup de la base
Endpoint `/api/backup` (à implémenter — voir tâche 8 dans le TODO). Alternative en attendant : Railway → service Postgres → onglet **Data** → tu peux exporter/inspecter les tables directement.

---

## 8. Comment les CSVs de comparables sont organisés

### `data/comparables.csv` — 10 lignes originales de Belfin/Champel 60
Colonnes : `id, adresse, quartier, type_bien, surface, prix, prix_m2, annee, etat, date_vente`
Toutes les lignes sont à Champel.

### `data/comparables-lp.csv` — 410 lignes consolidées depuis les xlsx Dropbox
Format identique. Généré une fois par un script one-shot qui a parcouru tous les fichiers `Comparables*.xlsx` du dossier `Estimations/` de la Dropbox LP.

⚠️ **Attention** : pour les maisons/villas, la surface est souvent celle de la parcelle (terrain), pas de l'habitable. Le rapport affiche un encart "prix/m² approché" quand le bien target est une maison, pour prévenir le lecteur.

Le script d'origine se trouve dans l'historique Git (commit `b636375`) — si tu veux le rejouer avec de nouveaux xlsx dans la Dropbox, retrouve le bash utilisé et adapte le path.

### `data/comparables_enrichis.csv` — 3 lignes très détaillées
Colonnes : `annee, adresse, quartier, type_bien, description, surface_m2, prix_chf, prix_m2, reference`
Format prix suisse avec apostrophes typographiques : `6'100'000`, `13'555`.
Descriptions multi-lignes. Ces 3 comparables apparaissent **en priorité** dans les 3 cartes du rapport quand ils matchent (car ils ont une description riche).

### Comment ajouter de nouveaux comparables enrichis
1. Ajoute une ligne au CSV enrichi en respectant le format
2. Colonnes obligatoires : `annee, adresse, quartier, type_bien, surface_m2, prix_chf, description`
3. `prix_m2` peut être vide (calculé automatiquement)
4. `reference` est un identifiant libre (ex : `HP12134B`)
5. `git add data/comparables_enrichis.csv && git commit -m "..." && git push`

---

## 9. Comment fonctionne l'IA sur le site

### Claude Opus 4.8 est utilisé pour trois choses

**A. Extraction de champs depuis un texte libre** — endpoint `/analyze-text`
Le courtier colle une phrase style *"Avenue de Champel 64, appartement duplex de 274 m² rénové 2024, 5.5 pièces, 3e étage…"*. Claude renvoie un JSON structuré avec adresse, quartier, type, surface, etc. — via le schéma Pydantic `PhotoExtraction`.

**B. Extraction depuis une photo / capture d'annonce** — endpoint `/upload-photo`
Même chose mais Claude Vision analyse l'image (redimensionnée à 1568px max côté par Pillow avant envoi pour maîtriser les coûts).

**C. Rédaction automatique de la description LP** — dans `/estimation/new` (POST)
Si le champ description est vide au moment de générer, Claude rédige un paragraphe de 3–5 phrases dans le style sobre et factuel LP, à partir des autres champs. Le prompt inclut des atouts prédéfinis par quartier (Champel, Eaux-Vives, Miremont).

### Coûts approximatifs
Le site tourne sur **Claude Opus 4.8** (modèle le plus capable d'Anthropic à ce jour, tarif $10/$50 par MTok). Ordres de grandeur :

- Extraction texte : ~0.02 USD par appel
- Extraction photo : ~0.02–0.04 USD par appel
- Rédaction description auto : ~0.01 USD par appel
- Génération du rapport complet : ~0.05–0.10 USD par estimation (une fois, puis mis en cache en base)

Un usage quotidien de 10 estimations coûte environ **0.5–1.0 USD/jour** soit **15–25 USD/mois**. À surveiller sur https://console.anthropic.com/settings/usage.

Si tu veux baisser les coûts sans perdre trop en qualité, tu peux repasser sur `claude-opus-4-8` (moitié prix) ou `claude-sonnet-5` (encore plus abordable) — il suffit de changer les 3 occurrences de `model="claude-opus-4-8"` dans `app.py`.

### Que se passe-t-il si la clé Anthropic est absente ou révoquée ?
- Les endpoints d'analyse renvoient une erreur claire à l'utilisateur ("Clé API Anthropic non configurée")
- La rédaction auto ne se déclenche pas mais l'estimation est quand même sauvée (la description reste vide)
- **Le site continue de fonctionner** sur son cœur (formulaire manuel, calcul, rapport, comparables)

---

## 10. Le système de comparables en détail

### Filtre progressif (fonction `find_comparables()` dans `app.py`)

Pour un bien cible (quartier + surface + type_bien), l'algo essaie 5 niveaux de filtre dans l'ordre :

1. **Strict** : même quartier + surface ±20 % + même famille de bien (appartements OU maisons)
2. **Élargi surface** : même quartier + surface ±40 % + même famille
3. **Sans surface** : même quartier + même famille
4. **Voisins** : quartier + quartiers voisins (mapping `NEIGHBOR_QUARTIERS`) + même famille
5. **Le plus large** : voisins, tous types résidentiels

L'algo s'arrête au premier niveau qui donne au moins **3 comparables** (constante `MIN_COMPARABLES`). Le niveau utilisé est affiché en tête du rapport ("Filtre strict" / "Filtre élargi" / "Filtre étendu" / "Filtre le plus large").

### Deux familles de type de bien
- **Appartements** : Appartement, Duplex, Attique, Penthouse, Triplex
- **Maisons** : Maison individuelle, Villa, Maison, Hôtel Particulier

Ces ensembles sont dans les constantes `APARTMENT_TYPES` / `HOUSE_TYPES` en haut de `app.py`.

### Tri des comparables retournés
1. Ceux avec une description riche d'abord (les enrichis)
2. Puis année desc (le plus récent)
3. Puis LP > vendus > retenus > à vendre

Seuls les **3 premiers** sont affichés dans le rapport final. Une ligne discrète en-dessous indique "+ N autres comparables disponibles" si la base en contient plus.

---

## 11. Comment développer en local

### Setup initial
```bash
cd ~/Desktop/lp-estimation
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Créer une DB SQLite locale (auto au premier lancement)
python app.py
# → http://localhost:5002
```

### Variables d'env pour le dev local
Sans variables : SQLite local (`lp_estim.db`), mot de passe `LP-estimation` (fallback), pas d'IA.

Pour un dev complet en local :
```bash
export ANTHROPIC_API_KEY="sk-ant-..."  # ta clé de dev
export APP_PASSWORD="local-dev-pwd"
python app.py
```

### Reset de la DB locale
```bash
rm -f lp_estim.db instance/lp_estim.db
python app.py  # recrée les tables + seed les comparables du CSV
```

---

## 12. FAQ opérationnelle

**Q : Comment ajouter un nouvel utilisateur ?**
R : Pas nécessaire dans le modèle actuel — c'est un mot de passe partagé pour toute l'équipe LP. Tout le monde se connecte en tant que "Leonard Properties" (compte unique). Si tu veux passer à des comptes individuels, il faudrait réactiver le flow `signup` désactivé en juillet 2026 (chercher "signup" dans le code — actuellement redirige vers /login).

**Q : Comment restreindre l'accès à une seule personne / rendre le site privé ?**
R : Deux options :
1. Changer le mot de passe (`APP_PASSWORD` sur Railway) — la solution en place
2. Passer sur des comptes individuels (voir Q précédente)

**Q : Comment ajouter un nouveau quartier de Genève ?**
R : Édite la liste `QUARTIERS` dans `report.py`. Push. Le nouveau quartier apparaît automatiquement dans le dropdown du formulaire et dans les recherches.

**Q : Comment ajouter un nouveau type de bien ?**
R : Édite `templates/_form_fields.html` (ajoute une `<option>` dans le `<select name="type_bien">`), puis mets à jour `APARTMENT_TYPES` ou `HOUSE_TYPES` dans `app.py` pour le classer dans la bonne famille.

**Q : Comment changer le logo affiché ?**
R : Option 1 (persistant) : remplace `static/uploads/leonard-logo.png` dans le repo, push.
Option 2 (temporaire) : connecte-toi au site → Paramètres → upload un logo. Attention, celui-ci vit dans le conteneur Railway et disparaît au prochain redéploiement.

**Q : Le site marche mais l'analyse texte/photo échoue avec une erreur d'API.**
R : Va sur https://console.anthropic.com/settings/billing — le solde est peut-être à zéro. Recharge le compte ou vérifie que la clé est bien active sur https://console.anthropic.com/settings/keys.

**Q : Le déploiement a raté ?**
R : Railway → service `web` → onglet **Logs** → cherche l'erreur Python dans la stack trace. Les erreurs classiques :
- Syntaxe Python cassée → revert le commit fautif
- Migration DB : le code démarre mais la DB n'est pas au bon schéma → connecte-toi à Postgres via Railway et regarde `\d ref_price` etc.
- Dépendance qui n'a pas de wheel Python 3.13 → épingle une version plus récente

**Q : Comment récupérer un backup complet de la base ?**
R : (Nécessite l'endpoint `/api/backup` — cf. tâche 8). En attendant : dashboard Railway → service Postgres → onglet **Data** → chaque table est visible et exportable.

**Q : Combien coûte le site par mois ?**
R :
- Railway (web + Postgres) : ~10–20 USD/mois selon usage
- Anthropic API : 5–15 USD/mois selon volume d'estimations
- Domaine si custom : 12–30 CHF/an (pas encore en place)
- Total réaliste : **~25 USD/mois**

---

## 13. Historique du projet et décisions

Le projet a démarré en mai 2026 avec un formulaire manuel classique et un mot de passe partagé. Il a évolué en plusieurs vagues :

1. **Juin 2026** — mise en place initiale, formulaire, comparables Champel/Eaux-Vives/Miremont, PDF export
2. **Début juillet 2026** — refonte majeure :
   - Comptes individuels (essai) puis retour au mot de passe partagé
   - Refonte "luxe" du design (serif Playfair, palette or/beige/brun, cartes)
   - Analyse texte + photo par Claude Opus 4.8
   - Rédaction auto de la description
   - Section "Classeur" (archive par quartier/type)
   - Ajout de tous les quartiers de Genève (~46)
   - Système de comparables filtrés (quartier + surface ±20 % + type)
   - Consolidation de 410 comparables depuis les xlsx Dropbox
   - Ingestion des comparables enrichis (avec descriptions)
   - Fallback progressif pour ne jamais retourner 0 comparables
   - 3 cartes riches en tête du rapport (adresse serif, prix, description)

L'historique complet est dans le git log — chaque commit a un message détaillé expliquant les motivations.

---

## 14. À faire ensuite (roadmap connue au moment du handoff)

Priorisé par valeur métier :

- [ ] **Refonte du corps du rapport LP par Claude** — remplacer le template statique dans `report.py` par une génération IA qui produit les sections officielles LP (méthode hédoniste, comparables, positionnement, valeur, réserves)
- [ ] **Export Word (.docx)** au gabarit LP officiel — livrable client, à côté du PDF actuel
- [ ] **Enrichir les 410 comparables Neatalerts** avec des descriptions générées par Claude (~0.20 CHF one-shot)
- [ ] **Tags atouts / inconvénients** à cocher dans le formulaire (Piscine, Cave, Vue lac, Ascenseur, Dernier étage, Terrasse, Jardin, Cheminée, Parking couvert…) + zone libre
- [ ] **Recherche + filtres dans le Classeur** (barre d'adresse, filtre année, tranche prix)
- [ ] **Endpoint `/api/backup`** — JSON complet, auth requise

**Non prévus** (Romain a décliné en juillet 2026) :
- Domaine personnalisé
- Signature du courtier dans le rapport
- Rate limiting sur `/login`
- Hash d'intégrité du PDF

---

## 15. Contacts et ressources

- **Créateur du projet** : Romain Marguet — `romain@lpsa.ch`
- **Compte GitHub** : `LPinventaires` (organisation qui héberge le repo)
- **Compte Railway** : le compte perso de Romain, workspace `lpinventaires's Project` (plan Pro)
- **Compte Anthropic** : sur `console.anthropic.com` avec l'email `romain@lpsa.ch` (à confirmer si transmission)

---

## 16. Point crucial de sécurité

Trois secrets ont été **partagés en clair dans un chat IA** au cours du développement :
- Un PAT GitHub → rotaté début juillet 2026
- Un token API Railway → rotaté fin juillet 2026
- Le mot de passe Postgres Railway → doit être rotaté par précaution (Railway → service Postgres → Variables → regenerate `POSTGRES_PASSWORD`)
- Le mot de passe du site `Champel26` → visible dans l'historique du chat, à changer si le site sort du cercle interne

Si tu reprends le projet, **rotate tous les secrets** avant d'accepter la responsabilité — c'est 5 minutes de travail et ça garantit qu'aucun ancien accès ne peut être réutilisé.

---

*Fin du guide. Bienvenue dans le projet. Bon courage.*
