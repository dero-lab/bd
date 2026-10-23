# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Application de gestion d'une collection de BD (« Ma Bédéthèque »). Tout est en français : interface,
messages d'erreur, commentaires et README. Garder cette langue.

## Commandes

Aucune dépendance ni étape de build : Python 3.9 (bibliothèque standard, `sqlite3` inclus) et
HTML/CSS/JS vanilla. Node n'est pas installé sur la machine.

```sh
python3 server.py [--port 8000] [--host 0.0.0.0] [--demo]   # site local + API
python3 import_csv.py ma-bedetheque.csv                       # import CSV « ; » (idempotent)
python3 enrich.py --series "Trolls de Troy" | --all           # couvertures/infos depuis internet
python3 export_static.py                                      # public/collection.json pour la vitrine
```

Il n'y a pas de suite de tests. Pour vérifier une modification :
- tester l'API avec `curl` sur une **copie** de `data/` et un autre port (ne pas toucher à la base
  réelle de l'utilisateur, qui fait souvent tourner `server.py` sur le port 8000) ;
- vérifier la syntaxe JS avec JavaScriptCore :
  `/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc -e "checkSyntax('public/js/app.js')"`.

## Architecture

**Serveur (`server.py`)** : `ThreadingHTTPServer` qui sert `public/` en statique, les images de
`data/covers/` sous `/covers/`, et une API JSON. Les routes sont déclarées dans la table `ROUTES`
(méthode, regex, fonction). Chaque handler a la signature `(conn, query, body, *ids_capturés)`,
reçoit une connexion SQLite déjà en transaction et lève `ApiError(status, message)` pour les erreurs.
Le schéma est dans `SCHEMA`. **Pour ajouter une colonne à une base existante**, l'ajouter aussi dans
`MIGRATIONS` : elle est créée par `ALTER TABLE` au démarrage. Au démarrage, `cleanup_covers()`
supprime les images de `data/covers/` qui ne sont référencées par aucun album.

**Front (`public/`)** : une seule page avec routage par hash (`#/`, `#/series`, `#/series/:id`), dans
`app.js`. Le front fonctionne dans **deux modes** :
- en local, `api()` appelle le serveur ;
- en ligne (Vercel, Netlify, GitHub Pages, sans serveur), `detectMode()` constate que `/api/stats`
  ne répond pas en JSON. Il charge alors `collection.json` et `staticApi()` imite les routes GET.
  La classe `body.readonly` masque tout ce qui porte `data-action` ou `.edit-only`.

Conséquences :
- toute nouvelle donnée affichée par une route GET doit aussi être produite par `export_static.py`
  et, si besoin, gérée dans `staticApi()` ;
- les chemins des fichiers statiques et des couvertures exportées sont **relatifs**, parce que
  GitHub Pages sert le site sous `/<dépôt>/`.

**Enrichissement (`enrich.py`)** : il est utilisé par le serveur (routes `/enrich`, `/lookup`,
`/find-isbn`) et en ligne de commande.
- Sources :
  - BnF SRU au format `dublincore` pour les détails d'un ISBN ;
  - BnF SRU au format `unimarcxchange` (zones 225/461 série + `$v` tome, 010 ISBN, 101 langue) pour
    trouver l'ISBN d'un tome à partir de la série ;
  - Open Library en secours.
- Le HTTPS de la BnF coupe les connexions après une rafale de requêtes. Le code se rabat alors sur
  HTTP pour l'API et retient le blocage pendant 10 minutes. Les couvertures BnF, qui exigent
  HTTPS, sont sautées pendant ce temps, et seul Open Library est interrogé. Pour éviter le
  blocage, toutes les requêtes vers la BnF passent une à une, espacées d'au moins
  `BNF_MIN_INTERVAL` secondes (`_bnf_throttle`). Ne pas retirer cette limite.
- Mesures du blocage : il se déclenche après 13 requêtes en 22 s, ou après quelques dizaines de
  requêtes même espacées de 3 s, et dure environ 5 min. En ligne de commande, `enrich.py` passe
  en mode `PATIENT` : il attend la levée du blocage, en vérifiant chaque minute, au lieu de sauter
  les couvertures. Un import complet prend donc du temps. Le serveur n'est pas patient, car une
  requête HTTP ne peut pas attendre aussi longtemps.

## Règles métier à préserver

- **Couvertures toujours locales.** `localize_cover()` (server.py) télécharge dans `data/covers/`
  toute couverture donnée par URL à l'enregistrement d'un album. Une nouvelle URL invalide est
  refusée. Une URL déjà enregistrée qui renvoie 404 est oubliée, et une panne passagère ne bloque
  pas la sauvegarde. L'enrichissement oublie aussi les URL mortes.
- **Ne jamais écraser une saisie de l'utilisateur.** L'enrichissement ne remplit que les champs
  vides. Il ne remplace le titre que s'il vaut « Tome N », ou s'il est vide. Il n'importe une
  couverture que si l'album n'en a pas en local (`/covers/…`). Une URL externe est rapatriée en local.
- **ISBN trouvé automatiquement** : seulement pour les albums « à acheter » (`owned = 0`), et
  seulement si l'éditeur de l'édition figure déjà dans la série. Pour un album possédé, l'édition
  ne se devine pas : le bouton « Trouver l'ISBN » du formulaire propose les candidats.
- **Tomes manquants** : ils ne sont pas stockés. `missing_tomes()` les calcule à chaque lecture
  (de 1 à max(`total_tomes`, plus grand tome connu), désactivable par `track_missing`). Les albums
  `owned = 0` forment la liste « à acheter ».
- **Suppression d'une série** : seulement si elle n'a plus aucun album (y compris « à acheter »).
  Le serveur répond 409 sinon, et le bouton n'apparaît que sur une série vide.
- **`import_csv.py`** : relançable sans doublon (même ISBN, ou même série + tome + titre). Un
  `Statut` autre que « Possédé » donne `owned = 0`.

## Déploiement

Le site publié est le dossier `public/` (`vercel.json`, `netlify.toml`, et
`.github/workflows/pages.yml` pour GitHub Pages). Ce qui est publié :
- `data/` est ignoré par git, car il contient les notes personnelles. La vitrine publie
  l'instantané `public/collection.json` et `public/covers/`, régénérés par `export_static.py`
  avant chaque commit de mise à jour.
- Les notes des albums ne sont pas exportées.
