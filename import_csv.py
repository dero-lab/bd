#!/usr/bin/env python3
"""Importe un export CSV (séparateur « ; ») dans la base de la collection.

    python3 import_csv.py ma-bedetheque.csv

Colonnes attendues : Série;Tome;Titre;Auteurs;Éditeur;Parution;ISBN / EAN;Statut
Relançable sans risque : un album déjà présent (même ISBN, ou même série + tome + titre)
n'est pas réimporté. Un statut autre que « Possédé » range l'album dans « à acheter ».
"""
import argparse
import collections
import csv
import re

from server import db, init_db


def year_of(parution):
    m = re.search(r"(\d{4})", parution or "")
    return int(m.group(1)) if m else None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv_file")
    parser.add_argument("--no-covers", action="store_true",
                        help="ne pas renseigner la couverture Open Library à partir de l'ISBN")
    args = parser.parse_args()

    with open(args.csv_file, encoding="utf-8-sig", newline="") as f:
        records = list(csv.DictReader(f, delimiter=";"))

    by_series = collections.defaultdict(list)
    for r in records:
        name = (r.get("Série") or "").strip()
        if name and (r.get("Titre") or "").strip():
            by_series[name].append(r)

    init_db()
    created_series = added = skipped = 0
    with db() as conn:
        for name, items in by_series.items():
            row = conn.execute("SELECT id FROM series WHERE title = ? COLLATE NOCASE", (name,)).fetchone()
            if row:
                sid = row["id"]
            else:
                # Éditeur et auteurs de la série : les plus fréquents parmi ses albums
                publisher = collections.Counter(i["Éditeur"].strip() for i in items if i["Éditeur"].strip())
                authors = collections.Counter(i["Auteurs"].strip() for i in items if i["Auteurs"].strip())
                sid = conn.execute(
                    "INSERT INTO series (title, author, publisher) VALUES (?, ?, ?)",
                    (name, authors.most_common(1)[0][0] if authors else "",
                     publisher.most_common(1)[0][0] if publisher else ""),
                ).lastrowid
                created_series += 1

            for i in items:
                title = i["Titre"].strip()
                tome = int(i["Tome"]) if i["Tome"].strip().isdigit() else None
                isbn = re.sub(r"[^0-9Xx]", "", i.get("ISBN / EAN") or "")
                exists = conn.execute(
                    "SELECT 1 FROM albums WHERE (isbn <> '' AND isbn = ?) "
                    "OR (series_id = ? AND tome IS ? AND title = ? COLLATE NOCASE)",
                    (isbn, sid, tome, title),
                ).fetchone()
                if exists:
                    skipped += 1
                    continue
                cover = "" if args.no_covers or not isbn else \
                    f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
                status = (i.get("Statut") or "").strip().lower()
                owned = 0 if status and not status.startswith("poss") else 1
                conn.execute(
                    "INSERT INTO albums (series_id, tome, title, year, isbn, cover, owned) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sid, tome, title, year_of(i["Parution"]), isbn, cover, owned),
                )
                added += 1

    print(f"{len(records)} lignes lues : {created_series} séries créées, "
          f"{added} albums ajoutés, {skipped} déjà présents.")


if __name__ == "__main__":
    main()
