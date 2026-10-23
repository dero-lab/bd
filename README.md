# Ma Bédéthèque

Petit site pour gérer sa collection de bandes dessinées : séries, albums et couvertures.

## Lancer le site

Aucune installation nécessaire : seul Python 3 suffit (SQLite est inclus).

```sh
python3 server.py           # puis ouvrir http://localhost:8000
python3 server.py --demo    # pré-remplit avec Tintin, Astérix et Blacksad (si la base est vide)
python3 server.py --port 8080 --host 0.0.0.0   # accessible depuis le téléphone sur le réseau local
```

## Fonctionnalités

- **Accueil** : statistiques (séries, albums, albums lus) et derniers albums ajoutés.
- **Séries** : liste avec recherche (titre, auteur, genre), progression de lecture, création.
- **Page série** : tous les albums avec leur couverture ; ajout, modification et suppression.
- **Réglages** (`#/reglages`) : thème clair/sombre, couleur principale, présentation des albums
  (grille, couvertures seules, liste), taille des couvertures, ordre des séries, tomes manquants
  affichés ou non, animations. Les choix sont retenus par le navigateur (aussi sur la vitrine).
- **Couvertures** : envoi d'une image (redimensionnée dans le navigateur), URL d'image,
  ou recherche automatique via l'ISBN. Dans tous les cas l'image est **enregistrée dans
  `data/covers/`** (une URL est téléchargée à l'enregistrement de l'album) : le site affiche
  les couvertures sans aucune requête sur internet. Sans image, une couverture illustrée est générée.

## Couvertures et informations depuis internet

À partir de l'ISBN, le site interroge le catalogue de la **BnF** (titre, auteurs, éditeur,
année, pages, couverture) puis **Open Library** en secours. Les couvertures sont
téléchargées dans `data/covers/` : le site n'a plus besoin d'internet pour les afficher.

- **Page d'une série → « Compléter depuis internet »** : traite tous les albums de la série.
- **Formulaire d'un album → « Via l'ISBN »** : traite l'album en cours de saisie.
- **En ligne de commande** : `python3 enrich.py --series "Trolls de Troy"` ou `python3 enrich.py --all`.

**Tomes manquants sans ISBN** : l'ISBN est cherché dans le catalogue de la BnF à partir du
nom de la série et du numéro de tome (éditions françaises uniquement, en privilégiant
l'éditeur et l'époque des albums que vous possédez). Il est renseigné automatiquement pour
les albums « à acheter » s'il est publié chez un éditeur déjà présent dans la série ; sinon,
le bouton « Trouver l'ISBN » du formulaire propose les éditions trouvées dans le champ ISBN.

Seules les **couvertures manquantes** sont importées : un album qui a déjà une couverture
locale n'est jamais modifié (une couverture donnée par URL est rapatriée en local). De même,
seuls les champs vides sont remplis, et le titre n'est remplacé que s'il vaut « Tome N ».

## Publier la collection en ligne

La version en ligne est une **vitrine en lecture seule** : la gestion se fait toujours en local
avec `server.py`, puis on publie un instantané de la collection.

```sh
python3 export_static.py          # écrit public/collection.json (+ public/covers/)
git add -A && git commit -m "Mise à jour de la collection" && git push
```

Le site publié est le dossier `public/`, sans étape de compilation. Il fonctionne sur :

- **Vercel** : importer le dépôt GitHub, la configuration est dans `vercel.json`.
- **Netlify** : importer le dépôt GitHub, la configuration est dans `netlify.toml`.
- **GitHub Pages** : *Settings → Pages → Source : GitHub Actions* ; le workflow
  `.github/workflows/pages.yml` publie à chaque push sur `main`.

Les notes personnelles des albums ne sont pas publiées. La base `data/` reste locale
(ignorée par git) : pensez à la sauvegarder.

## Organisation

```
server.py            serveur HTTP + API JSON (bibliothèque standard Python)
public/index.html    page unique (navigation par #/…)
public/css/style.css styles
public/js/app.js     logique de l'interface
export_static.py     export de la vitrine en ligne (public/collection.json)
import_csv.py        import d'un fichier CSV
enrich.py            couvertures et informations depuis la BnF / Open Library
data/collection.db   base SQLite (créée au premier lancement, non versionnée)
data/covers/         couvertures envoyées
```

Pour sauvegarder la collection, copiez simplement le dossier `data/`.
