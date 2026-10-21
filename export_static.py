#!/usr/bin/env python3
"""Exporte la collection pour la vitrine en ligne (lecture seule).

    python3 export_static.py

Écrit public/collection.json et copie les couvertures envoyées dans public/covers/.
Le dossier public/ peut alors être publié tel quel (Vercel, Netlify, GitHub Pages).
Les notes personnelles des albums ne sont pas exportées.
"""
import json
import shutil
from datetime import datetime

from server import COVERS, PUBLIC, api_series_get, api_series_list, api_stats, db, init_db

OUT_JSON = PUBLIC / "collection.json"
OUT_COVERS = PUBLIC / "covers"


def public_album(album):
    album = {k: v for k, v in album.items() if k != "notes"}
    # chemins relatifs : le site peut être servi depuis un sous-dossier (GitHub Pages)
    if album.get("cover", "").startswith("/covers/"):
        album["cover"] = album["cover"][1:]
    return album


def main():
    init_db()
    with db() as conn:
        stats = api_stats(conn, {}, {})
        series = api_series_list(conn, {}, {})
        for s in series:
            detail = api_series_get(conn, {}, {}, s["id"])
            s["albums"] = [public_album(a) for a in detail["albums"]]
            s["missing_tomes"] = detail["missing_tomes"]
            if (s.get("cover") or "").startswith("/covers/"):
                s["cover"] = s["cover"][1:]
    stats["recent"] = [public_album(a) for a in stats["recent"]]

    # Couvertures : on remplace le dossier pour ne pas publier d'images supprimées
    used = {a["cover"].split("/")[-1] for s in series for a in s["albums"] if a["cover"].startswith("covers/")}
    shutil.rmtree(OUT_COVERS, ignore_errors=True)
    OUT_COVERS.mkdir(parents=True)
    for name in used:
        if (COVERS / name).is_file():
            shutil.copy2(COVERS / name, OUT_COVERS / name)

    payload = {"generated_at": datetime.now().isoformat(timespec="minutes"), "stats": stats, "series": series}
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    albums = sum(len(s["albums"]) for s in series)
    print(f"Export terminé : {len(series)} séries, {albums} albums, {len(used)} couvertures → {OUT_JSON.relative_to(PUBLIC.parent)}")


if __name__ == "__main__":
    main()
