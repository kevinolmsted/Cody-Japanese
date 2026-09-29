#!/usr/bin/env python3
"""Deterministic base-form (lemma) extraction for every transcript segment.

Tokenizer: MeCab via fugashi with the UniDic-lite dictionary (versions pinned
in requirements.txt). The same input text and dictionary always produce the
same tokens, so the output is fully reproducible.

Every segment of every transcript in data/transcripts/ is processed; nothing
is sampled or skipped. Only ASR sound tags such as ``[音楽]`` / ``[笑い]`` /
``[拍手]`` are removed before tokenising, because they are not speech.

Output: data/tokens.tsv, one row per kept content-word token:
  video_id  segment  offset  surface  lemma  written_base  reading  base_reading  pos
where ``offset`` is the token's character position in the cleaned segment
text (see clean_segment), ``reading`` is the lemma's reading (UniDic lForm)
and ``base_reading`` is the reading of the written base form (UniDic kanaBase).
"""
import csv
import json
import pathlib
import re
import unicodedata

import fugashi

ROOT = pathlib.Path(__file__).resolve().parent.parent

SOUND_TAG = re.compile(r"\[[^\]]*\]")
HAS_JAPANESE = re.compile(r"[぀-ヿ㐀-鿿]")
KANA_ONLY = re.compile(r"^[぀-ヿー]+$")

# (pos1, pos2) pairs that count as vocabulary. Everything else — particles,
# auxiliaries, proper nouns, numerals, pronouns, suffixes, symbols,
# interjections, and "non-independent" function verbs/adjectives such as
# する・いる・ない — is dropped.
KEEP_POS = {
    ("名詞", "普通名詞"),
    ("動詞", "一般"),
    ("形容詞", "一般"),
    ("形状詞", "一般"),
    ("形状詞", "タリ"),
    ("副詞", "*"),
    ("連体詞", "*"),
}


def kata_to_hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def clean_segment(text: str) -> str:
    """NFKC-normalise and blank out ASR sound tags; shared with build_deck.py."""
    return unicodedata.normalize("NFKC", SOUND_TAG.sub(" ", text)).strip()


def token_rows(tagger, text):
    offset = 0
    for w in tagger(text):
        offset += len(w.white_space)
        start = offset
        offset += len(w.surface)
        f = w.feature
        pos = (f.pos1, f.pos2 if f.pos1 not in ("副詞", "連体詞") else "*")
        if pos not in KEEP_POS or not f.lemma or not f.lForm:
            continue
        lemma = f.lemma.split("-")[0]  # "ゲーム-game" -> "ゲーム"
        base = f.orthBase or lemma
        if not HAS_JAPANESE.search(lemma) or not HAS_JAPANESE.search(w.surface):
            continue
        if KANA_ONLY.match(lemma) and len(lemma) < 2:
            continue
        base_reading = kata_to_hira(f.kanaBase or f.lForm)
        yield start, w.surface, lemma, base, kata_to_hira(f.lForm), base_reading, f"{f.pos1}-{f.pos2}"


def main() -> None:
    tagger = fugashi.Tagger()
    files = sorted((ROOT / "data" / "transcripts").glob("*.json"))
    n_seg = n_tok = 0
    with (ROOT / "data" / "tokens.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["video_id", "segment", "offset", "surface", "lemma", "written_base", "reading",
                    "base_reading", "pos"])
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            for i, seg in enumerate(data["transcript"]):
                n_seg += 1
                for row in token_rows(tagger, clean_segment(seg["text"])):
                    w.writerow([data["video_id"], i, *row])
                    n_tok += 1
    print(f"videos: {len(files)}  segments: {n_seg}  content tokens: {n_tok}")


if __name__ == "__main__":
    main()
