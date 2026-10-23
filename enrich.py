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
import threading
import time
import unicodedata
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
BNF_BLOCK_SECONDS = 300  # durée mesurée d'un blocage : ~4 min 40
_bnf_https_blocked_until = 0.0
# La BnF bloque le HTTPS après une rafale (mesuré : 13 requêtes en 22 s suffisent ; 30 requêtes
# espacées de 3 s passent) : ses requêtes passent donc une par une, espacées.
BNF_MIN_INTERVAL = 3.0
_bnf_lock = threading.Lock()
# Mode patient (ligne de commande) : si la BnF bloque, on attend qu'elle se débloque au lieu
# de sauter ses couvertures. Le serveur reste non patient : une page ne peut pas attendre 5 min.
PATIENT = False
BNF_PATIENCE_SECONDS = 15 * 60
_bnf_wait_lock = threading.Lock()
_bnf_last_request = 0.0


def _bnf_throttle():
    global _bnf_last_request
    with _bnf_lock:
        wait = _bnf_last_request + BNF_MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _bnf_last_request = time.time()


# --- Réseau -----------------------------------------------------------------

def http_get(url, timeout=15, retries=2):
    """Renvoie (contenu, type) ou None. Réessaie sur les erreurs réseau passagères."""
    global _bnf_https_blocked_until
    bnf = url.startswith(BNF_HTTPS)
    if bnf and time.time() < _bnf_https_blocked_until:
        return _bnf_http(url, timeout)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        if bnf:
            _bnf_throttle()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as res:
                return res.read(), res.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return None
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.6 * (attempt + 1))
    if bnf and PATIENT and _wait_for_bnf():
        return http_get(url, timeout, retries)
    if bnf:
        _bnf_https_blocked_until = time.time() + BNF_BLOCK_SECONDS
        return _bnf_http(url, timeout)
    return None


def _wait_for_bnf():
    """Attend la levée du blocage HTTPS de la BnF (un seul fil vérifie, les autres attendent)."""
    with _bnf_wait_lock:
        probe = BNF_COVER.format(ark="ark:/12148/cb41303810c")
        start = time.time()
        while time.time() - start < BNF_PATIENCE_SECONDS:
            try:
                urllib.request.urlopen(urllib.request.Request(probe, headers={"User-Agent": USER_AGENT}),
                                       timeout=10).read()
                return True
            except (urllib.error.URLError, OSError):
                print("  BnF momentanément bloquée, nouvel essai dans 1 min…", flush=True)
                time.sleep(60)
        return False


def _bnf_http(url, timeout):
    """Le HTTPS de la BnF coupe les connexions après une rafale de requêtes ; son API reste
    joignable en HTTP (données publiques). Les couvertures redirigent vers HTTPS : on les
    saute alors, Open Library prend le relais."""
    if "/couverture?" in url:
        return None
    request = urllib.request.Request("http://" + url[len("https://"):], headers={"User-Agent": USER_AGENT})
    _bnf_throttle()
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


# --- Recherche d'ISBN par série + tome (BnF, notices UNIMARC) ---------------

def normalize(text):
    """« Les Tuniques bleues (Dupuis). » → « tuniques bleues » : pour comparer des titres."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\(.*?\)", " ", text)
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    return " ".join(w for w in words if w not in ("le", "la", "les", "l", "the"))


def tome_number(value):
    """« 11 », « T. 11 », « tome 11 » → 11 ; « 11-13 » (intégrale) → None."""
    m = re.fullmatch(r"(?:t(?:ome)?\.?\s*)?0*(\d+)", unescape(value).lower().strip(" ."))
    return int(m.group(1)) if m else None


def unimarc_fields(record, tag):
    return [dict((code, unescape(v)) for code, v in re.findall(r'code="(\w)">(.*?)</mxc:subfield>', f, re.S))
            for f in re.findall(rf'<mxc:datafield tag="{tag}".*?</mxc:datafield>', record, re.S)]


def series_match(candidate, target):
    """0 : même série ; 1 : la série est nommée dedans (« Une aventure d'Astérix ») ; None sinon."""
    candidate = normalize(candidate)
    if candidate == target:
        return 0
    if re.search(rf"(^| ){re.escape(target)}( |$)", candidate):
        return 1
    return None


def find_isbn_candidates(series_title, tome, publishers=(), ref_year=None):
    """Éditions françaises d'un tome trouvées à la BnF, la plus probable en premier.

    Classement : nom de série exact, puis éditeur déjà présent dans la série, puis année
    la plus proche des autres albums (pour rester dans la même édition)."""
    name = re.sub(r"\s*\(.*?\)", "", series_title or "").strip()
    if not name or not tome:
        return []
    query = urllib.parse.urlencode({
        "version": "1.2", "operation": "searchRetrieve", "recordSchema": "unimarcxchange",
        "maximumRecords": "50", "query": f'bib.title all "{name}" and bib.title all "{tome}"',
    })
    res = http_get(f"{BNF_SRU}?{query}", timeout=25)
    if not res:
        return []
    target = normalize(name)
    known_pubs = {normalize(p) for p in publishers if p} - {""}
    found = {}
    for record in re.findall(r"<mxc:record.*?</mxc:record>", res[0].decode("utf-8", "replace"), re.S):
        languages = {f.get("a") for f in unimarc_fields(record, "101")}
        if languages and "fre" not in languages:
            continue
        scores = [series_match(f.get(code, ""), target)
                  for tag, code in (("225", "a"), ("461", "t")) for f in unimarc_fields(record, tag)
                  if tome_number(f.get("v", "")) == int(tome)]
        scores = [sc for sc in scores if sc is not None]
        isbns = [re.sub(r"[^0-9Xx]", "", f.get("a", "")) for f in unimarc_fields(record, "010")]
        isbns = [i for i in isbns if len(i) in (10, 13)]
        if not scores or not isbns:
            continue
        edition = (unimarc_fields(record, "210") + unimarc_fields(record, "214") or [{}])[0]
        year = re.search(r"\d{4}", edition.get("d", ""))
        publisher = edition.get("c", "")
        pub = normalize(publisher)
        same_publisher = not known_pubs or any(k and (k in pub or pub in k) for k in known_pubs)
        candidate = {
            "isbn": isbns[0],
            "title": (unimarc_fields(record, "200") or [{}])[0].get("a", ""),
            "publisher": publisher,
            "year": int(year.group()) if year else None,
            "same_publisher": same_publisher,
            "_rank": (min(scores), not same_publisher,
                      abs((int(year.group()) if year else 9999) - ref_year) if ref_year
                      else (int(year.group()) if year else 9999)),
        }
        if isbns[0] not in found or candidate["_rank"] < found[isbns[0]]["_rank"]:
            found[isbns[0]] = candidate
    ranked = sorted(found.values(), key=lambda c: c["_rank"])
    return [{k: v for k, v in c.items() if k != "_rank"} for c in ranked]


def series_context(albums, series_publisher=""):
    """Éditeurs et année de référence d'une série, pour classer les éditions candidates."""
    publishers = {a["publisher"] for a in albums if a.get("publisher")} | {series_publisher}
    years = sorted(a["year"] for a in albums if a.get("year") and a.get("owned", 1))
    return {"publishers": publishers, "ref_year": years[len(years) // 2] if years else None}


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


def fetch_image(url, timeout=10, max_bytes=15_000_000):
    """Télécharge l'image d'une URL donnée : (octets, extension), « gone » si l'adresse n'existe
    pas (404/410), ou None si l'échec est peut-être passager ou si ce n'est pas une image."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as res:
            data = res.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        return "gone" if e.code in (404, 410) else None
    except (urllib.error.URLError, OSError, ValueError):
        return None
    ext = image_ext(data)
    # moins de 200 octets : image vide de remplacement (ex. pixel transparent)
    return (data, ext) if ext and 200 <= len(data) <= max_bytes else None


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
    # ISBN recherché automatiquement seulement pour les albums « à acheter » : pour un album
    # possédé, l'édition exacte ne peut pas être devinée (bouton du formulaire à la place).
    needs_isbn = lambda a: not a["isbn"] and a["tome"] and not a["owned"]
    todo = [a for a in albums
            if needs_isbn(a) or needs_info(a) or (needs_cover(a) and (a["isbn"] or a["cover"]))]

    context = series_context(albums, series["publisher"])

    def work(album):
        found_isbn = ""
        if needs_isbn(album):
            candidates = find_isbn_candidates(series["title"], album["tome"], **context)
            # Accepté seulement chez un éditeur déjà présent dans la série (évite les homonymes)
            found_isbn = candidates[0]["isbn"] if candidates and candidates[0]["same_publisher"] else ""
        missing = needs_cover(album)
        isbn = album["isbn"] or found_isbn
        known_ark = re.search(r"ark:/12148/cb\w+", album["source_url"] or "")
        if known_ark and not needs_info(album):
            # Notice déjà récupérée : seule la couverture manque, inutile d'interroger le catalogue
            image = find_cover(isbn, known_ark.group(), album["cover"]) if missing else None
            return album, found_isbn, {}, image
        return (album, found_isbn, *lookup(isbn, album["cover"] if missing else "", want_cover=missing))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, todo))

    covers = infos = isbns = 0
    not_found = []
    creators = collections.Counter()
    publishers = collections.Counter()
    for album, found_isbn, info, image in results:
        cover_path = save_image(covers_dir, image) if image else None
        if needs_cover(album) and not image:
            not_found.append(album["title"])
        updates = album_updates(album, info, cover_path)
        # URL externe morte : on l'oublie pour ne plus solliciter internet à chaque affichage
        if (not image and (album["cover"] or "").startswith("http")
                and fetch_image(album["cover"]) == "gone"):
            updates["cover"] = ""
        if found_isbn:
            updates["isbn"] = found_isbn
            isbns += 1
        if cover_path:
            covers += 1
        if set(updates) - {"cover", "isbn"}:
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
        "isbns": isbns,
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

    global PATIENT
    PATIENT = True
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
              + (f", {r['isbns']} ISBN trouvé(s)" if r["isbns"] else "")
              + (f", {r['missing_covers']} sans couverture" if r["missing_covers"] else ""))


if __name__ == "__main__":
    main()
