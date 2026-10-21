#!/usr/bin/env python3
"""Complète les albums depuis internet à partir de leur ISBN : couverture et informations.

Sources : catalogue de la BnF (notices + couvertures), puis Open Library en secours.
Seules les couvertures manquantes sont importées (un album qui a déjà une couverture
locale n'est pas touché ; une couverture désignée par une URL est rapatriée en local)
et seuls les champs vides sont remplis : rien de ce que vous avez saisi n'est écrasé.

    python3 enrich.py --series "Trolls de Troy"   # une série
    python3 enrich.py --all                      # toute la collection
"""
import collections
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

USER_AGENT = "MaBedetheque/1.0 (collection personnelle)"
BNF_SRU = "https://catalogue.bnf.fr/api/SRU"
BNF_COVER = "https://catalogue.bnf.fr/couverture?&appName=NE&idArk={ark}&couverture=1"
OL_BOOK = "https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
OL_COVER = "https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
GOOD_COVER_BYTES = 40_000  # en dessous, on cherche une image plus grande ailleurs
PLACEHOLDER_TITLE = re.compile(r"^\s*(tome\s*\d+)?\s*$", re.I)
BNF_HTTPS = "https://catalogue.bnf.fr/"
BNF_BLOCK_SECONDS = 600
_bnf_https_blocked_until = 0.0


# --- Réseau -----------------------------------------------------------------

def http_get(url, timeout=15, retries=2):
    """Renvoie (contenu, type) ou None. Réessaie sur les erreurs réseau passagères."""
    global _bnf_https_blocked_until
    bnf = url.startswith(BNF_HTTPS)
    if bnf and time.time() < _bnf_https_blocked_until:
        return _bnf_http(url, timeout)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as res:
                return res.read(), res.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return None
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.6 * (attempt + 1))
    if bnf:
        _bnf_https_blocked_until = time.time() + BNF_BLOCK_SECONDS
        return _bnf_http(url, timeout)
    return None


def _bnf_http(url, timeout):
    """Le HTTPS de la BnF coupe les connexions après une rafale de requêtes ; son API reste
    joignable en HTTP (données publiques). Les couvertures redirigent vers HTTPS : on les
    saute alors, Open Library prend le relais."""
    if "/couverture?" in url:
        return None
    request = urllib.request.Request("http://" + url[len("https://"):], headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as res:
            return res.read(), res.headers.get("Content-Type", "")
    except (urllib.error.URLError, OSError):
        return None


def isbn13_to_10(isbn):
    if len(isbn) != 13 or not isbn.startswith("978"):
        return None
    core = isbn[3:12]
    check = (11 - sum((10 - i) * int(c) for i, c in enumerate(core)) % 11) % 11
    return core + ("X" if check == 10 else str(check))


# --- BnF --------------------------------------------------------------------

def person_name(creator):
    """« Arleston, Christophe (1963-....). Auteur du texte » → « Christophe Arleston »."""
    name = re.sub(r"\s*\(.*?\)|\..*$", "", creator).strip()
    if ", " in name:
        last, first = name.split(", ", 1)
        name = f"{first} {last}"
    return name


def bnf_lookup(isbn):
    for candidate in filter(None, (isbn, isbn13_to_10(isbn))):
        query = urllib.parse.urlencode({
            "version": "1.2", "operation": "searchRetrieve", "recordSchema": "dublincore",
            "maximumRecords": "1", "query": f'bib.isbn adj "{candidate}"',
        })
        res = http_get(f"{BNF_SRU}?{query}")
        if not res:
            continue
        xml = res[0].decode("utf-8", "replace")
        if not re.search(r"numberOfRecords>[1-9]", xml):
            continue
        field = lambda tag: [unescape(v) for v in re.findall(rf"<dc:{tag}>(.*?)</dc:{tag}>", xml, re.S)]
        full_title = (field("title") or [""])[0]
        title, _, responsibility = full_title.partition(" / ")
        responsibility = re.sub(r"[\s;,]+$", "", responsibility.strip())
        ark = re.search(r"(ark:/12148/cb\w+)", xml)
        fmt = " ".join(field("format"))
        pages = re.search(r"(\d+)\s*p\.", fmt)
        year = re.search(r"\d{4}", " ".join(field("date")))
        publisher = re.sub(r"\s*\(.*?\)", "", (field("publisher") or [""])[0]).strip()
        return {
            "title": title.strip(),
            "authors": responsibility[:1].upper() + responsibility[1:],
            "creators": [person_name(c) for c in field("creator")],
            "publisher": publisher,
            "year": int(year.group()) if year else None,
            "pages": int(pages.group(1)) if pages else None,
            "ark": ark.group(1) if ark else "",
            "source_url": f"https://catalogue.bnf.fr/{ark.group(1)}" if ark else "",
        }
    return None


def unescape(text):
    return (text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
            .replace("&apos;", "'").replace("&amp;", "&").strip())


# --- Open Library -----------------------------------------------------------

def openlibrary_lookup(isbn):
    res = http_get(OL_BOOK.format(isbn=isbn))
    if not res:
        return None
    try:
        data = json.loads(res[0]).get(f"ISBN:{isbn}")
    except ValueError:
        return None
    if not data:
        return None
    year = re.search(r"\d{4}", data.get("publish_date", ""))
    authors = [a["name"] for a in data.get("authors", []) if a.get("name")]
    return {
        "title": data.get("title", ""),
        "authors": ", ".join(authors),
        "creators": authors,
        "publisher": ", ".join(p["name"] for p in data.get("publishers", []) if p.get("name")),
        "year": int(year.group()) if year else None,
        "pages": data.get("number_of_pages"),
        "source_url": data.get("url", ""),
    }


# --- Couvertures ------------------------------------------------------------

def image_ext(data):
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:4] == b"GIF8":
        return ".gif"
    return None


def find_cover(isbn, ark, existing_url):
    """Télécharge la meilleure couverture disponible ; renvoie (octets, extension) ou None."""
    candidates = []
    if existing_url:
        candidates.append(existing_url)
    if ark:
        candidates.append(BNF_COVER.format(ark=ark))
    if isbn:
        candidates.append(OL_COVER.format(isbn=isbn))
    best = None
    for url in dict.fromkeys(candidates):  # sans doublon, ordre conservé
        res = http_get(url)
        if not res or len(res[0]) < 2000 or not image_ext(res[0]):
            continue
        if not best or len(res[0]) > len(best[0]):
            best = (res[0], image_ext(res[0]))
        if len(best[0]) >= GOOD_COVER_BYTES:
            break
    return best


def save_image(covers_dir, image):
    data, ext = image
    name = uuid.uuid4().hex + ext
    (covers_dir / name).write_bytes(data)
    return "/covers/" + name


# --- Traitement d'un album / d'une série -------------------------------------

def lookup(isbn, existing_cover="", want_cover=True):
    """Informations et couverture (octets, extension) pour un ISBN, sans rien écrire."""
    isbn = re.sub(r"[^0-9Xx]", "", isbn or "")
    info = (bnf_lookup(isbn) or openlibrary_lookup(isbn) or {}) if isbn else {}
    cover = find_cover(isbn, info.get("ark"), existing_cover) if want_cover else None
    return info, cover


def needs_cover(album):
    return not (album["cover"] or "").startswith("/covers/")


def needs_info(album):
    """Notice jamais récupérée (pas de lien source) et au moins un champ vide."""
    return bool(album["isbn"]) and not album["source_url"] and \
        any(not album[k] for k in ("authors", "publisher", "year", "pages"))


def album_updates(album, info, cover_path):
    """Champs à mettre à jour : uniquement ceux qui sont vides."""
    updates = {}
    if cover_path:
        updates["cover"] = cover_path
    if info.get("title") and PLACEHOLDER_TITLE.match(album["title"] or ""):
        updates["title"] = info["title"]
    for key in ("authors", "publisher", "year", "pages", "source_url"):
        if info.get(key) and not album.get(key):
            updates[key] = info[key]
    return updates


def enrich_series(conn, series_id, covers_dir, workers=3):
    series = conn.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
    albums = [dict(r) for r in conn.execute("SELECT * FROM albums WHERE series_id = ?", (series_id,))]
    todo = [a for a in albums if needs_info(a) or (needs_cover(a) and (a["isbn"] or a["cover"]))]

    def work(album):
        missing = needs_cover(album)
        return (album, *lookup(album["isbn"], album["cover"] if missing else "", want_cover=missing))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, todo))

    covers = infos = 0
    not_found = []
    creators = collections.Counter()
    publishers = collections.Counter()
    for album, info, image in results:
        cover_path = save_image(covers_dir, image) if image else None
        if needs_cover(album) and not image:
            not_found.append(album["title"])
        updates = album_updates(album, info, cover_path)
        if cover_path:
            covers += 1
        if set(updates) - {"cover"}:
            infos += 1
        if updates:
            sets = ", ".join(f"{k} = :{k}" for k in updates)
            conn.execute(f"UPDATE albums SET {sets} WHERE id = :id", {**updates, "id": album["id"]})
        if info.get("creators"):
            creators[" & ".join(dict.fromkeys(info["creators"]))] += 1
        if info.get("publisher"):
            publishers[info["publisher"]] += 1

    series_updates = {}
    if not series["author"] and creators:
        series_updates["author"] = creators.most_common(1)[0][0]
    if not series["publisher"] and publishers:
        series_updates["publisher"] = publishers.most_common(1)[0][0]
    if series_updates:
        sets = ", ".join(f"{k} = :{k}" for k in series_updates)
        conn.execute(f"UPDATE series SET {sets} WHERE id = :id", {**series_updates, "id": series_id})

    return {
        "checked": len(todo),
        "covers": covers,
        "infos": infos,
        "missing_covers": sum(1 for a in albums if needs_cover(a)) - covers,
        "not_found": not_found,
        "without_isbn": sum(1 for a in albums if not a["isbn"]),
    }


def main():
    import argparse
    from server import COVERS, db, init_db

    parser = argparse.ArgumentParser(description="Complète couvertures et informations des albums.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--series", help="titre de la série (ou une partie du titre)")
    group.add_argument("--all", action="store_true", help="toutes les séries")
    args = parser.parse_args()

    init_db()
    with db() as conn:
        if args.all:
            targets = conn.execute("SELECT id, title FROM series ORDER BY title").fetchall()
        else:
            targets = conn.execute("SELECT id, title FROM series WHERE title LIKE ? ORDER BY title",
                                   (f"%{args.series}%",)).fetchall()
        if not targets:
            parser.error(f"aucune série ne correspond à « {args.series} »")
    for sid, title in targets:
        with db() as conn:  # une transaction par série : le travail est conservé au fil de l'eau
            r = enrich_series(conn, sid, COVERS)
        print(f"{title} : {r['covers']} couverture(s) importée(s), {r['infos']} album(s) complété(s)"
              + (f", {r['missing_covers']} sans couverture" if r["missing_covers"] else ""))


if __name__ == "__main__":
    main()
