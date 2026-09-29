"""End-to-end checks on the committed pipeline outputs.

These re-run the deterministic stages and verify that:
  * outputs are byte-identical across runs;
  * every transcript segment was tokenised;
  * the deck has at least 1,000 notes, all graded N3/N2/N1;
  * every meaning is exactly the JMdict glosses of the tagged entry;
  * every example sentence is taken from the transcript it cites.
"""
import csv
import hashlib
import html
import json
import pathlib
import re
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import build_deck  # noqa: E402
from lemmatize import clean_segment  # noqa: E402

DECK_TSV = ROOT / "anki" / "cody_japanese_vocab.tsv"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(script):
    subprocess.run([sys.executable, str(TOOLS / script)], check=True, cwd=ROOT, capture_output=True)


def deck_rows():
    with DECK_TSV.open(encoding="utf-8") as fh:
        lines = [l for l in fh if not l.startswith("#")]
    return list(csv.reader(lines, delimiter="\t"))


def test_outputs_are_deterministic():
    files = [ROOT / "data" / "tokens.tsv", ROOT / "data" / "frequency.tsv", DECK_TSV,
             ROOT / "data" / "deck_words.tsv"]
    run("lemmatize.py"), run("frequency.py"), run("build_deck.py")
    first = [sha(f) for f in files]
    run("lemmatize.py"), run("frequency.py"), run("build_deck.py")
    assert [sha(f) for f in files] == first


def test_every_segment_was_tokenised():
    segs = {}
    for p in (ROOT / "data" / "transcripts").glob("*.json"):
        segs[p.stem] = len(json.loads(p.read_text(encoding="utf-8"))["transcript"])
    with (ROOT / "data" / "videos.tsv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    assert {r["video_id"]: int(r["segments"]) for r in rows} == segs
    stats = (ROOT / "data" / "corpus_stats.md").read_text(encoding="utf-8")
    assert f"{sum(segs.values()):,} (all of them)" in stats


def test_deck_size_and_levels():
    rows = deck_rows()
    assert len(rows) >= 1000
    assert {r[4] for r in rows} <= {"N3", "N2", "N1"}
    assert len({r[0] + "|" + r[1] for r in rows}) == len(rows)


@pytest.fixture(scope="module")
def jmdict():
    entries, *_ = build_deck.load_jmdict()
    return entries


def test_meanings_are_verbatim_jmdict(jmdict):
    for r in deck_rows():
        seq = int(re.search(r"jmdict_(\d+)", r[9]).group(1))
        senses = jmdict[seq]["senses"][:build_deck.MAX_SENSES]
        expected = "<br>".join(f"{i}. " + html.escape("; ".join(s["gloss"])) for i, s in enumerate(senses, 1))
        assert r[2] == expected, r[0]
        assert r[1] in {x for x, _ in jmdict[seq]["rebs"]}, r[0]


def test_examples_come_from_the_cited_transcript():
    transcripts = {p.stem: json.loads(p.read_text(encoding="utf-8"))
                   for p in (ROOT / "data" / "transcripts").glob("*.json")}
    for r in deck_rows():
        vid = re.search(r"youtu\.be/([\w-]{11})\?t=(\d+)", r[7])
        assert vid, r[0]
        sentence = html.unescape(re.sub(r"</?b>", "", r[6]))
        segs = [clean_segment(s["text"]) for s in transcripts[vid.group(1)]["transcript"]]
        assert sentence in segs, (r[0], sentence)


def test_apkg_contains_all_notes():
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(ROOT / "anki" / "cody_japanese_vocab.apkg") as z:
            z.extract("collection.anki2", tmp)
        con = sqlite3.connect(pathlib.Path(tmp) / "collection.anki2")
        n_notes = con.execute("select count(*) from notes").fetchone()[0]
        n_cards = con.execute("select count(*) from cards").fetchone()[0]
        con.close()
    assert n_notes == n_cards == len(deck_rows())
