#!/usr/bin/env python3
"""Download the dictionary data used to define and grade words.

Sources:
  * JMdict (English) from the Electronic Dictionary Research and Development
    Group (EDRDG), licensed CC BY-SA 4.0.
  * JLPT vocabulary lists (N5..N1) from jlpt-vocab-api, which serves the
    community-compiled lists by Jonathan Waller (unofficial; the JLPT has no
    official word list since 2010).

Files are cached in data/dict/. Existing files are not re-downloaded unless
--force is given, so re-runs reuse exactly the same bytes. URL, retrieval
time and sha256 of every file are written to data/dict/SOURCES.md.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DICT_DIR = ROOT / "data" / "dict"

JMDICT_URL = "https://www.edrdg.org/pub/Nihongo/JMdict_e.gz"
JLPT_URL = "https://jlpt-vocab-api.vercel.app/api/words/all?level={level}"
# Waller's lists with each word linked to its JMdict entry ID (exact matching).
WALLER_JMDICT_URL = "https://raw.githubusercontent.com/stephenmk/yomitan-jlpt-vocab/main/original_data/n{level}.csv"
# open-anki-jlpt-decks: curated tanos-based lists, some words tagged with several levels.
OPEN_ANKI_URL = "https://raw.githubusercontent.com/jamsinclair/open-anki-jlpt-decks/main/src/n{level}.csv"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "cody-japanese/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return resp.read()


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-download even if cached")
    args = ap.parse_args()
    DICT_DIR.mkdir(parents=True, exist_ok=True)

    targets = [(JMDICT_URL, DICT_DIR / "JMdict_e.gz")]
    for level in range(1, 6):
        targets.append((JLPT_URL.format(level=level), DICT_DIR / f"jlpt_n{level}.json"))
        targets.append((WALLER_JMDICT_URL.format(level=level), DICT_DIR / f"waller_jmdict_n{level}.csv"))
        targets.append((OPEN_ANKI_URL.format(level=level), DICT_DIR / f"openanki_n{level}.csv"))

    sources_md = DICT_DIR / "SOURCES.md"
    retrieved = {}
    if sources_md.exists():
        for line in sources_md.read_text(encoding="utf-8").splitlines():
            if line.startswith("| `"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                retrieved[cells[0].strip("`")] = cells[2]

    rows = []
    for url, path in targets:
        if args.force or not path.exists():
            data = fetch(url)
            if path.suffix == ".json":
                json.loads(data)  # fail loudly on a bad response
            path.write_bytes(data)
            retrieved[path.name] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            print(f"downloaded {path.name} ({path.stat().st_size:,} bytes)")
        else:
            print(f"cached     {path.name}")
        rows.append((path.name, url, retrieved.get(path.name, "unknown"), sha256(path)))

    lines = [
        "# Dictionary sources",
        "",
        "| File | URL | Retrieved (UTC) | sha256 |",
        "|---|---|---|---|",
    ]
    lines += [f"| `{n}` | {u} | {t} | `{h}` |" for n, u, t, h in rows]
    lines += [
        "",
        "JMdict © EDRDG, used under CC BY-SA 4.0: https://www.edrdg.org/edrdg/licence.html",
        "JLPT lists (all community lists; the JLPT has published no official word list since 2010):",
        "- `jlpt_n*.json`: jlpt-vocab-api (Jonathan Waller's lists, tanos.co.uk, CC BY).",
        "- `waller_jmdict_n*.csv`: stephenmk/yomitan-jlpt-vocab (Waller's lists linked to JMdict IDs).",
        "- `openanki_n*.csv`: jamsinclair/open-anki-jlpt-decks (curated tanos-based lists).",
        "",
    ]
    sources_md.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
