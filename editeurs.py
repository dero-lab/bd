#!/usr/bin/env python3
"""Albums à paraître annoncés par les éditeurs français et belges.

Chaque source lit la page « à paraître » d'un éditeur (une ou quelques pages couvrent tout
son programme des semaines à venir). Seuls les tomes des séries « en cours » de la collection,
postérieurs au dernier tome connu, sont retenus et rangés dans la table `releases`, avec leur
date de sortie.

Pages autorisées par les robots.txt des éditeurs (vérifié le 23/10/2026). Les recherches des
libraires (Place des Libraires, Bedetheque, Decitre…) sont interdites aux robots ou bloquées :
elles ne sont pas utilisées. Le Lombard, Casterman et Bamboo n'ont pas de page lisible.

Le format de ces pages peut changer : une source qui ne donne plus rien est signalée, sans
bloquer les autres.
"""
import html
import json
import re
import time
import urllib.parse
from datetime import date

import enrich

MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]
SITE_INTERVAL = 1.0  # secondes entre deux requêtes vers un même éditeur

_last_request = {}


def fetch(url):
    """Page HTML d'un éditeur, ou "" en cas d'échec."""
    host = urllib.parse.urlparse(url).netloc
    wait = _last_request.get(host, 0) + SITE_INTERVAL - time.time()
    if wait > 0:
        time.sleep(wait)
    res = enrich.http_get(url, timeout=25)
    _last_request[host] = time.time()
    return res[0].decode("utf-8", "replace") if res else ""


def text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def parse_date(value):
    """« 19.11.2026 », « 06/11/2026 », « 22 octobre 2026 », « 2026-11-06… » → « 2026-11-06 »."""
    value = text(value).lower()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        return m.group(0)
    m = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", value)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.search(r"(\d{1,2}) (" + "|".join(MONTHS) + r") (\d{4})", value)
    if m:
        return f"{m.group(3)}-{MONTHS.index(m.group(2)) + 1:02d}-{int(m.group(1)):02d}"
    return None


def split_tome(title):
    """« Trolls de Troy T27 », « Brume - Tome 05 », « Nains T27 - Le Cycle… » → (série, 27, suite).
    Une intégrale ou un coffret (« Intégrale T15 à T18 ») n'est pas un tome de la série."""
    m = re.match(r"^(.*?)[\s,:–-]*\b(?:T|Tome)\s*\.?\s*0*(\d+)\b[\s:–-]*(.*)$", text(title), re.I)
    if not m or not m.group(1) or re.search(r"(?i)int[ée]grale|coffret|\bT\d+ [àa] ", text(title)):
        return text(title), None, ""
    return m.group(1).strip(), int(m.group(2)), (m.group(3) or "").strip()


# --- Une fonction par famille de sites : elle renvoie des annonces --------------
# {"series", "tome", "title", "date", "isbn", "url", "cover"} ; date et isbn peuvent manquer
# si la source a une fonction `resolve` qui les lit sur la page de l'album.

def parse_drupal_catalogue(source):
    """Soleil et Delcourt (même groupe, même site) : vignettes « product-thumbnail »."""
    page = fetch(source["url"])
    base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(source["url"]))
    found = []
    for block in page.split('class="product-thumbnail album-thumbnail')[1:]:
        title = re.search(r'__title[^>]*>\s*<a href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not title:
            continue
        series, tome, rest = split_tome(title.group(2))
        subtitle = re.search(r'field-name-field-subtitle[^>]*>(.*?)</div>', block, re.S)
        when = re.search(r'__date[^>]*>(.*?)</div>', block, re.S)
        isbn = re.search(r'/product/(97[89]\d{10})\.', block)
        cover = re.search(r'src="(/sites/default/files/[^"]*/product/97[89]\d{10}\.jpg[^"]*)"', block)
        found.append({
            "series": series, "tome": tome, "title": text(subtitle.group(1)) if subtitle else rest,
            "date": parse_date(when.group(1)) if when else None, "isbn": isbn.group(1) if isbn else "",
            "url": base + title.group(1).strip(), "cover": base + html.unescape(cover.group(1)) if cover else "",
        })
    return found


def parse_dargaud(source):
    """Dargaud : une page par mois, chaque album porte ses données en JSON (data-tracking-infos)."""
    base = "https://www.dargaud.com"
    first = fetch(source["url"])
    pages = [first] + [fetch(base + path) for path in
                       dict.fromkeys(re.findall(r'href="(/bd-a-paraitre/\d{2}-\d{4})"', first))]
    found = []
    for page in pages:
        for block in page.split('<article class="card-album')[1:]:
            infos = re.search(r'data-tracking-infos="([^"]+)"', block)
            if not infos:
                continue
            data = json.loads(html.unescape(infos.group(1)))
            tome = re.search(r"\d+", data.get("variant_album") or "")
            when = re.search(r'<time datetime="([^"]+)"', block)
            link = re.search(r'class="card-album__link[^"]*" href="([^"]+)"', block)
            cover = re.search(r'<img src="(https://bdi\.dlpdomain\.com/[^"]+)"', block)
            subtitle = re.search(r'card-album__subtitle[^>]*>(.*?)</', block, re.S)
            found.append({
                "series": data.get("titre_serie", ""), "tome": int(tome.group()) if tome else None,
                "title": text(subtitle.group(1)) if subtitle else "",
                "date": parse_date(when.group(1)) if when else None, "isbn": data.get("ean_album", ""),
                "url": base + link.group(1) if link else source["url"], "cover": cover.group(1) if cover else "",
            })
    return found


def parse_glenat(source):
    """Glénat (et Vents d'Ouest) : cartes produit avec l'ISBN dans l'adresse."""
    page = fetch(source["url"])
    found = []
    for m in re.finditer(r'<a [^>]*href="(/[^"]*-(97[89]\d{10})/)"[^>]*>(.*?)</a>', page, re.S):
        title = re.search(r'class="[^"]*\bTitle\b[^"]*">(.*?)</div>', m.group(3), re.S)
        when = re.search(r'ReleaseDate[^"]*">(.*?)</div>', m.group(3), re.S)
        if not title or not when:
            continue
        series, tome, rest = split_tome(title.group(1))
        found.append({"series": series, "tome": tome, "title": rest, "date": parse_date(when.group(1)),
                      "isbn": m.group(2), "url": "https://www.glenat.com" + m.group(1), "cover": ""})
    return found


def parse_dupuis(source):
    """Dupuis : la page des parutions ne liste que des adresses (série et tome dedans) ;
    la date et l'ISBN sont lus sur la page de l'album, seulement pour les séries suivies."""
    page = fetch(source["url"])
    found = []
    for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data.get("itemListElement", []) if isinstance(data, dict) else []:
            m = re.match(r"https://www\.dupuis\.com/([^/]+)/bd/([^/]+)/\d+", item.get("url", ""))
            tome = re.search(r"-tome-(\d+)(?:-(.*))?$", m.group(2)) if m else None
            if tome:
                found.append({"series": m.group(1).replace("-", " "), "tome": int(tome.group(1)),
                              "title": "", "date": None, "isbn": "", "url": item["url"], "cover": ""})
    return found


def resolve_dupuis(entry):
    page = fetch(entry["url"])
    for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and "Book" in str(data.get("@type")):
            image = data.get("image")
            image = image[0] if isinstance(image, list) and image else image
            image = image.get("url") if isinstance(image, dict) else image
            # L'« isbn » de ce JSON change à chaque visite : le vrai est dans l'adresse de la couverture
            isbn = re.search(r'og:image" content="[^"]*/album/(97[89]\d{10})-couv', page)
            return {**entry, "title": data.get("name", ""), "isbn": isbn.group(1) if isbn else "",
                    "date": parse_date(data.get("datePublished", "")),
                    "cover": image if isinstance(image, str) else ""}
    return None


SOURCES = [
    {"id": "soleil", "name": "Soleil", "country": "France", "parse": parse_drupal_catalogue,
     "url": "https://www.editions-soleil.fr/bd/a-paraitre-bandes-dessinees"},
    {"id": "delcourt", "name": "Delcourt", "country": "France", "parse": parse_drupal_catalogue,
     "url": "https://www.editions-delcourt.fr/bd/a-paraitre-bandes-dessinees"},
    {"id": "dargaud", "name": "Dargaud", "country": "France", "parse": parse_dargaud,
     "url": "https://www.dargaud.com/bd-a-paraitre"},
    {"id": "glenat", "name": "Glénat", "country": "France", "parse": parse_glenat,
     "url": "https://www.glenat.com/bd/a-paraitre/"},
    {"id": "dupuis", "name": "Dupuis", "country": "Belgique", "parse": parse_dupuis, "resolve": resolve_dupuis,
     "url": "https://www.dupuis.com/catalogue/FR/parutions.html"},
]


def check_publishers(conn):
    """Range dans `releases` les albums annoncés pour les séries en cours de la collection.
    Renvoie le nombre d'annonces retenues et les sources qui n'ont rien donné."""
    followed = {}
    for r in conn.execute("SELECT s.id, s.title, MAX(a.tome) AS last FROM series s "
                          "LEFT JOIN albums a ON a.series_id = s.id WHERE s.status = 'en cours' GROUP BY s.id"):
        followed[enrich.normalize(r["title"])] = (r["id"], r["last"] or 0)
    kept, failed = 0, []
    for source in SOURCES:
        try:
            entries = source["parse"](source)
        except Exception:  # page modifiée ou inaccessible : on passe à l'éditeur suivant
            entries = []
        if not entries:
            failed.append(source["name"])
            continue
        for entry in entries:
            match = followed.get(enrich.normalize(entry["series"]))
            if not match or not entry["tome"] or entry["tome"] <= match[1]:
                continue
            if source.get("resolve"):
                entry = source["resolve"](entry)
            if not entry or not entry["date"]:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO releases (series_id, tome, title, isbn, publisher, year, release_date, "
                "source, url, cover) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (match[0], entry["tome"], entry["title"], entry["isbn"], source["name"],
                 int(entry["date"][:4]), entry["date"], source["name"], entry["url"], entry["cover"]))
            kept += 1
    return {"announced": kept, "failed": failed}


if __name__ == "__main__":
    # Aperçu de ce que lit chaque source, sans rien enregistrer
    today = date.today().isoformat()
    for source in SOURCES:
        entries = source["parse"](source)
        print(f"== {source['name']} ({source['country']}) : {len(entries)} albums")
        for e in entries[:5]:
            print(f"   {e['date'] or '?'}  {e['series']} T{e['tome']}  {e['title']}  {e['isbn']}"
                  + ("  (déjà paru)" if e["date"] and e["date"] < today else ""))
