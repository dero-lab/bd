#!/usr/bin/env python3
"""Serveur de la collection BD : fichiers statiques + API JSON sur SQLite.

Aucune dépendance externe. Lancement :
    python3 server.py            # http://localhost:8000
    python3 server.py --demo     # pré-remplit la base avec quelques séries (si vide)
    python3 server.py --port 8080
"""
import argparse
import base64
import json
import mimetypes
import re
import sqlite3
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

import enrich

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DATA = ROOT / "data"
COVERS = DATA / "covers"
DB_PATH = DATA / "collection.db"
MAX_BODY = 12 * 1024 * 1024  # 12 Mo (couverture encodée en base64 incluse)
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    author      TEXT DEFAULT '',
    publisher   TEXT DEFAULT '',
    genre       TEXT DEFAULT '',
    status      TEXT DEFAULT 'en cours',
    description TEXT DEFAULT '',
    total_tomes INTEGER,
    track_missing INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS albums (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id  INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    tome       INTEGER,
    title      TEXT NOT NULL,
    year       INTEGER,
    isbn       TEXT DEFAULT '',
    cover      TEXT DEFAULT '',
    is_read    INTEGER DEFAULT 0,
    owned      INTEGER DEFAULT 1,
    authors    TEXT DEFAULT '',
    publisher  TEXT DEFAULT '',
    pages      INTEGER,
    source_url TEXT DEFAULT '',
    notes      TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_albums_series ON albums(series_id);
"""

# Colonnes ajoutées après la première version : ajoutées aux bases existantes au démarrage.
MIGRATIONS = {
    "series": {"total_tomes": "INTEGER", "track_missing": "INTEGER DEFAULT 1"},
    "albums": {"owned": "INTEGER DEFAULT 1", "authors": "TEXT DEFAULT ''", "publisher": "TEXT DEFAULT ''",
               "pages": "INTEGER", "source_url": "TEXT DEFAULT ''"},
}

SERIES_FIELDS = ("title", "author", "publisher", "genre", "status", "description")
ALBUM_FIELDS = ("tome", "title", "year", "isbn", "cover", "is_read", "owned", "notes",
                "authors", "publisher", "pages")

DEMO = [
    ("Les Aventures de Tintin", "Hergé", "Casterman", "Aventure", "terminée",
     "Le jeune reporter et son fidèle Milou parcourent le monde.",
     [(1, "Tintin au pays des Soviets", 1930), (2, "Tintin au Congo", 1931),
      (3, "Tintin en Amérique", 1932), (4, "Les Cigares du pharaon", 1934),
      (5, "Le Lotus bleu", 1936)]),
    ("Astérix", "Goscinny & Uderzo", "Dargaud", "Humour", "en cours",
     "Nous sommes en 50 avant Jésus-Christ. Toute la Gaule est occupée… Toute ? Non !",
     [(1, "Astérix le Gaulois", 1961), (2, "La Serpe d'or", 1962),
      (3, "Astérix et les Goths", 1963)]),
    ("Blacksad", "Díaz Canales & Guarnido", "Dargaud", "Polar", "en cours",
     "Un détective chat dans une Amérique des années 50 peuplée d'animaux.",
     [(1, "Quelque part entre les ombres", 2000), (2, "Arctic-Nation", 2003),
      (3, "Âme rouge", 2005)]),
]


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(demo=False):
    COVERS.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
        for table, columns in MIGRATIONS.items():
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        if demo and conn.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 0:
            for title, author, publisher, genre, status, desc, albums in DEMO:
                sid = conn.execute(
                    "INSERT INTO series (title, author, publisher, genre, status, description) "
                    "VALUES (?, ?, ?, ?, ?, ?)", (title, author, publisher, genre, status, desc)
                ).lastrowid
                conn.executemany(
                    "INSERT INTO albums (series_id, tome, title, year) VALUES (?, ?, ?, ?)",
                    [(sid, t, at, y) for t, at, y in albums],
                )


def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def clean_series(payload):
    data = {k: str(payload.get(k) or "").strip() for k in SERIES_FIELDS}
    if not data["title"]:
        raise ApiError(400, "Le titre de la série est obligatoire.")
    if data["status"] not in ("en cours", "terminée", "abandonnée"):
        data["status"] = "en cours"
    total = to_int(payload.get("total_tomes"))
    data["total_tomes"] = total if total and 0 < total <= 999 else None
    data["track_missing"] = 0 if payload.get("track_missing") in (False, 0, "0") else 1
    return data


def to_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def save_cover(data_url):
    """Enregistre une image envoyée en data URL et renvoie son chemin public."""
    m = re.match(r"^data:(image/[a-z+]+);base64,(.+)$", data_url, re.S)
    if not m or m.group(1) not in IMAGE_TYPES:
        raise ApiError(400, "Format d'image non supporté (JPEG, PNG, WebP ou GIF).")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        raise ApiError(400, "Image corrompue.")
    name = uuid.uuid4().hex + IMAGE_TYPES[m.group(1)]
    (COVERS / name).write_bytes(raw)
    return "/covers/" + name


def delete_cover(path):
    if path and path.startswith("/covers/"):
        target = (COVERS / Path(path).name)
        if target.is_file():
            target.unlink()


def clean_album(payload, previous_cover=""):
    data = {k: payload.get(k) for k in ALBUM_FIELDS}
    data["title"] = str(data["title"] or "").strip()
    if not data["title"]:
        raise ApiError(400, "Le titre de l'album est obligatoire.")
    data["tome"] = to_int(data["tome"])
    data["year"] = to_int(data["year"])
    data["isbn"] = re.sub(r"[^0-9Xx]", "", str(data["isbn"] or ""))
    data["notes"] = str(data["notes"] or "").strip()
    data["authors"] = str(data["authors"] or "").strip()
    data["publisher"] = str(data["publisher"] or "").strip()
    data["pages"] = to_int(data["pages"])
    data["is_read"] = 1 if data["is_read"] else 0
    data["owned"] = 0 if payload.get("owned") is False else 1
    cover = str(data["cover"] or "").strip()
    if payload.get("cover_data"):
        cover = save_cover(payload["cover_data"])
    elif cover and not (cover.startswith("/covers/") or re.match(r"^https?://", cover)):
        raise ApiError(400, "L'URL de couverture doit commencer par http:// ou https://")
    if previous_cover and previous_cover != cover:
        delete_cover(previous_cover)
    data["cover"] = cover
    return data


def missing_tomes(tomes, total_tomes, track=1):
    """Numéros absents entre 1 et max(nombre de tomes parus, plus grand tome connu)."""
    if not track:
        return []
    known = {t for t in tomes if t is not None and t > 0}
    last = max([total_tomes or 0, *known], default=0)
    return [n for n in range(1, last + 1) if n not in known]


def tomes_by_series(conn):
    result = {}
    for r in conn.execute("SELECT series_id, tome FROM albums WHERE tome IS NOT NULL"):
        result.setdefault(r["series_id"], []).append(r["tome"])
    return result


# --- Routes API ------------------------------------------------------------

def api_stats(conn, _q, _b):
    stats = conn.execute(
        "SELECT (SELECT COUNT(*) FROM series) AS series, "
        "(SELECT COUNT(*) FROM albums WHERE owned = 1) AS albums, "
        "(SELECT COUNT(*) FROM albums WHERE owned = 1 AND is_read = 1) AS read, "
        "(SELECT COUNT(*) FROM albums WHERE owned = 0) AS wanted"
    ).fetchone()
    tomes = tomes_by_series(conn)
    gaps = sum(len(missing_tomes(tomes.get(r["id"], []), r["total_tomes"], r["track_missing"]))
               for r in conn.execute("SELECT id, total_tomes, track_missing FROM series"))
    recent = rows(conn.execute(
        "SELECT a.*, s.title AS series_title FROM albums a JOIN series s ON s.id = a.series_id "
        "WHERE a.owned = 1 ORDER BY a.created_at DESC, a.id DESC LIMIT 8"
    ))
    return {**dict(stats), "missing": stats["wanted"] + gaps, "recent": recent}


def api_series_list(conn, query, _b):
    q = (query.get("q") or [""])[0].strip()
    sql = (
        "SELECT s.*, SUM(a.owned = 1) AS album_count, SUM(a.owned = 1 AND a.is_read = 1) AS read_count, "
        "SUM(a.owned = 0) AS wanted_count, "
        "(SELECT cover FROM albums WHERE series_id = s.id AND cover <> '' "
        " ORDER BY tome IS NULL, tome, id LIMIT 1) AS cover "
        "FROM series s LEFT JOIN albums a ON a.series_id = s.id "
    )
    params = []
    if q:
        sql += "WHERE s.title LIKE ? OR s.author LIKE ? OR s.genre LIKE ? "
        params = ["%" + q + "%"] * 3
    sql += "GROUP BY s.id ORDER BY s.title COLLATE NOCASE"
    tomes = tomes_by_series(conn)
    result = rows(conn.execute(sql, params))
    for s in result:
        s["missing_count"] = (s["wanted_count"] or 0) + len(missing_tomes(tomes.get(s["id"], []), s["total_tomes"], s["track_missing"]))
    return result


def get_series(conn, sid):
    s = conn.execute("SELECT * FROM series WHERE id = ?", (sid,)).fetchone()
    if not s:
        raise ApiError(404, "Série introuvable.")
    return dict(s)


def api_series_get(conn, _q, _b, sid):
    series = get_series(conn, sid)
    series["albums"] = rows(conn.execute(
        "SELECT * FROM albums WHERE series_id = ? ORDER BY tome IS NULL, tome, year, id", (sid,)
    ))
    series["missing_tomes"] = missing_tomes([a["tome"] for a in series["albums"]], series["total_tomes"],
                                             series["track_missing"])
    return series


def api_series_create(conn, _q, body):
    d = clean_series(body)
    sid = conn.execute(
        "INSERT INTO series (title, author, publisher, genre, status, description, total_tomes, track_missing) "
        "VALUES (:title, :author, :publisher, :genre, :status, :description, :total_tomes, :track_missing)", d
    ).lastrowid
    return get_series(conn, sid)


def api_series_update(conn, _q, body, sid):
    get_series(conn, sid)
    d = clean_series(body)
    conn.execute(
        "UPDATE series SET title=:title, author=:author, publisher=:publisher, genre=:genre, "
        "status=:status, description=:description, total_tomes=:total_tomes, "
        "track_missing=:track_missing WHERE id=:id", {**d, "id": sid}
    )
    return get_series(conn, sid)


def api_series_delete(conn, _q, _b, sid):
    get_series(conn, sid)
    for (cover,) in conn.execute("SELECT cover FROM albums WHERE series_id = ?", (sid,)).fetchall():
        delete_cover(cover)
    conn.execute("DELETE FROM series WHERE id = ?", (sid,))
    return {"ok": True}


def get_album(conn, aid):
    a = conn.execute("SELECT * FROM albums WHERE id = ?", (aid,)).fetchone()
    if not a:
        raise ApiError(404, "Album introuvable.")
    return dict(a)


def api_album_create(conn, _q, body, sid):
    get_series(conn, sid)
    d = clean_album(body)
    aid = conn.execute(
        "INSERT INTO albums (series_id, tome, title, year, isbn, cover, is_read, owned, notes, "
        "authors, publisher, pages) VALUES (:sid, :tome, :title, :year, :isbn, :cover, :is_read, "
        ":owned, :notes, :authors, :publisher, :pages)", {**d, "sid": sid}
    ).lastrowid
    return get_album(conn, aid)


def api_series_add_missing(conn, _q, _b, sid):
    """Ajoute chaque tome manquant de la série comme album « à acheter »."""
    series = api_series_get(conn, None, None, sid)
    conn.executemany(
        "INSERT INTO albums (series_id, tome, title, owned) VALUES (?, ?, ?, 0)",
        [(sid, n, f"Tome {n}") for n in series["missing_tomes"]],
    )
    return {"added": len(series["missing_tomes"])}


def api_series_enrich(conn, _q, _b, sid):
    """Importe les couvertures manquantes et complète les champs vides de la série."""
    get_series(conn, sid)
    return enrich.enrich_series(conn, sid, COVERS)


def api_lookup(_conn, query, _b):
    """Recherche par ISBN pour le formulaire ; la couverture n'est téléchargée que si demandée."""
    isbn = (query.get("isbn") or [""])[0]
    if not re.sub(r"[^0-9Xx]", "", isbn):
        raise ApiError(400, "ISBN manquant.")
    info, image = enrich.lookup(isbn, want_cover=(query.get("cover") or ["0"])[0] == "1")
    if not info and not image:
        raise ApiError(404, "Aucune information trouvée pour cet ISBN.")
    result = {k: info.get(k) for k in ("title", "authors", "publisher", "year", "pages", "source_url")}
    result["cover"] = enrich.save_image(COVERS, image) if image else ""
    return result


def cleanup_covers():
    """Supprime les images qui ne sont plus utilisées (ex. recherche ISBN annulée)."""
    with db() as conn:
        used = {Path(c).name for (c,) in conn.execute("SELECT cover FROM albums WHERE cover LIKE '/covers/%'")}
    for f in COVERS.iterdir():
        if f.is_file() and f.name not in used:
            f.unlink()


def api_album_update(conn, _q, body, aid):
    current = get_album(conn, aid)
    d = clean_album(body, previous_cover=current["cover"])
    conn.execute(
        "UPDATE albums SET tome=:tome, title=:title, year=:year, isbn=:isbn, cover=:cover, "
        "is_read=:is_read, owned=:owned, notes=:notes, authors=:authors, publisher=:publisher, "
        "pages=:pages WHERE id=:id", {**d, "id": aid}
    )
    return get_album(conn, aid)


def api_album_delete(conn, _q, _b, aid):
    delete_cover(get_album(conn, aid)["cover"])
    conn.execute("DELETE FROM albums WHERE id = ?", (aid,))
    return {"ok": True}


ROUTES = [
    ("GET", r"/api/stats", api_stats),
    ("GET", r"/api/series", api_series_list),
    ("POST", r"/api/series", api_series_create),
    ("GET", r"/api/series/(\d+)", api_series_get),
    ("PUT", r"/api/series/(\d+)", api_series_update),
    ("DELETE", r"/api/series/(\d+)", api_series_delete),
    ("POST", r"/api/series/(\d+)/albums", api_album_create),
    ("POST", r"/api/series/(\d+)/missing", api_series_add_missing),
    ("POST", r"/api/series/(\d+)/enrich", api_series_enrich),
    ("GET", r"/api/lookup", api_lookup),
    ("PUT", r"/api/albums/(\d+)", api_album_update),
    ("DELETE", r"/api/albums/(\d+)", api_album_delete),
]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def log_message(self, fmt, *args):
        if not self.path.startswith(("/css", "/js", "/covers", "/favicon")):
            super().log_message(fmt, *args)

    # -- utilitaires
    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(413, "Fichier trop volumineux.")
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "JSON invalide.")

    def handle_api(self, method):
        url = urlparse(self.path)
        for m, pattern, func in ROUTES:
            match = re.fullmatch(pattern, url.path)
            if match and m == method:
                try:
                    body = self.read_json() if method in ("POST", "PUT") else {}
                    with db() as conn:
                        result = func(conn, parse_qs(url.query), body, *map(int, match.groups()))
                    self.send_json(201 if method == "POST" else 200, result)
                except ApiError as e:
                    self.send_json(e.status, {"error": e.message})
                except Exception as e:  # pragma: no cover
                    self.log_error("Erreur API : %r", e)
                    self.send_json(500, {"error": "Erreur interne du serveur."})
                return
        self.send_json(404, {"error": "Route inconnue."})

    def serve_cover(self):
        name = Path(unquote(urlparse(self.path).path)).name
        target = COVERS / name
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

    # -- verbes HTTP
    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self.handle_api("GET")
        if path.startswith("/covers/"):
            return self.serve_cover()
        return super().do_GET()

    def do_POST(self):
        self.handle_api("POST")

    def do_PUT(self):
        self.handle_api("PUT")

    def do_DELETE(self):
        self.handle_api("DELETE")


def main():
    parser = argparse.ArgumentParser(description="Ma collection de BD")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--demo", action="store_true", help="ajoute des séries d'exemple si la base est vide")
    args = parser.parse_args()
    init_db(demo=args.demo)
    cleanup_covers()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"📚 Collection BD disponible sur http://{args.host}:{args.port}  (Ctrl+C pour arrêter)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nAu revoir !")


if __name__ == "__main__":
    main()
