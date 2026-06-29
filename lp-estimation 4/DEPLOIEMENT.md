# Déployer « LP · Estimation hédoniste » sur Railway

Même principe que l'app Léonard. ~15 minutes. Tu réutilises tes comptes GitHub et Railway existants.

---

## Étape 1 — Mettre le code sur GitHub

1. Décompresse `lp-estimation.zip` (double-clic). Tu obtiens un dossier `lp-estimation`.
2. Va sur **github.com** → bouton vert **New** (nouveau dépôt).
   - Owner : ton compte (ou l'organisation **LPinventaires**, comme Léonard).
   - Repository name : `lp-estimation`
   - Laisse **Private** coché.
   - **Ne coche PAS** « Add a README ».
   - Clique **Create repository**.
3. Sur la page du dépôt vide, clique le lien **« uploading an existing file »**
   (ou *Add file → Upload files*).
4. Ouvre le dossier `lp-estimation`, sélectionne **tout son contenu** et fais-le
   glisser dans la zone GitHub :
   - les fichiers : `app.py`, `parsing.py`, `report.py`, `requirements.txt`,
     `Procfile`, `.gitignore`, `README.md`
   - les dossiers : `templates/` et `static/`
5. Clique **Commit changes**. Le code est sur GitHub. ✅

> Le `.gitignore` empêche d'envoyer le `venv` et la base locale — c'est voulu.

---

## Étape 2 — Créer le service sur Railway

1. Va sur **railway.app** → ton tableau de bord.
2. **New Project** → **Deploy from GitHub repo** → autorise GitHub si demandé →
   choisis **lp-estimation**.
3. Railway détecte Python, installe `requirements.txt` et lance `gunicorn` (via le `Procfile`).

---

## Étape 3 — Ajouter la base de données PostgreSQL

1. Dans le projet Railway → **New** (ou *+ Create*) → **Database** → **Add PostgreSQL**.
2. Une base Postgres est créée à côté de ton service web.

---

## Étape 4 — Régler les variables d'environnement

Sur le **service web** (pas la base) → onglet **Variables** → ajoute :

| Variable        | Valeur                                            |
|-----------------|---------------------------------------------------|
| `DATABASE_URL`  | `${{Postgres.DATABASE_URL}}` (référence la base)  |
| `SECRET_KEY`    | une longue chaîne aléatoire (ex. tape n'importe quoi de long) |
| `APP_PASSWORD`  | le code d'accès que tu choisis (remplace `LP-estimation`) |

> `${{Postgres.DATABASE_URL}}` : Railway propose l'autocomplétion quand tu tapes `${{` —
> choisis le service Postgres. Ça branche l'app sur la base automatiquement.

Railway redéploie tout seul après chaque changement de variable.

---

## Étape 5 — Ouvrir le site

1. Sur le service web → **Settings** → **Networking** → **Generate Domain**.
2. Tu obtiens une URL du type `https://lp-estimation-production-xxxx.up.railway.app`.
3. Ouvre-la : page de connexion grise → entre ton `APP_PASSWORD`. 🎉

Le site est en ligne, accessible de partout, sans terminal.

---

## En cas de souci

- **Page « Application failed to respond »** : attends 1–2 min (premier déploiement),
  puis recharge. Sinon, regarde les **Logs** du service web sur Railway.
- **Erreur base de données** : vérifie que `DATABASE_URL` pointe bien vers le service Postgres
  (variable `${{Postgres.DATABASE_URL}}`).
- **Une modif du code** : tu la repousses sur GitHub (Upload files / commit), Railway redéploie seul.

---

## Mettre à jour les données prix/m²

Une fois en ligne, connecte-toi et va dans **Références prix/m²** pour ajouter/corriger
les comparables par quartier. C'est ce qui rend les estimations plus précises.
