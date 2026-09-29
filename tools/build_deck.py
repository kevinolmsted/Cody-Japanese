#!/usr/bin/env python3
"""Look up corpus words in JMdict, grade them with the JLPT lists, and build an Anki deck.

Everything here is rule-based and deterministic:
  * Definitions are copied from JMdict (EDRDG). Nothing is written by hand
    or generated; a word with no JMdict match is dropped.
  * Difficulty comes from the JLPT N5-N1 lists. N3, N2 and N1 words are
    intermediate-to-advanced; N5/N4 words are excluded.
  * Example sentences are copied from the transcript segments where the
    word occurred.

Inputs:  data/frequency.tsv, data/tokens.tsv, data/transcripts/, data/videos.tsv,
         data/dict/JMdict_e.gz, data/dict/jlpt_n{1..5}.json
Outputs: anki/cody_japanese_vocab.apkg   Anki package
         anki/cody_japanese_vocab.tsv    same notes as a plain-text import file
         data/deck_words.tsv             selection audit trail (all candidates)
         data/deck_stats.md              selection summary
"""
import argparse
import collections
import csv
import gzip
import html
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

import genanki

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lemmatize import clean_segment  # noqa: E402  (same text normalisation as the tokenizer)

ROOT = pathlib.Path(__file__).resolve().parent.parent
DICT = ROOT / "data" / "dict"

MIN_COUNT = 2          # noise filter against one-off ASR errors
MIN_CARDS = 1000       # the user asked for at least 1,000 words
TARGET_LEVELS = (3, 2, 1)
MAX_SENSES = 3

# Fixed IDs so re-imports update the same deck/notes instead of duplicating.
MODEL_ID = 1729384001
DECK_ID = 1729384002
BUILD_TIMESTAMP = 1790640000.0  # fixed package timestamp (2026-09-29 UTC)

STD_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
ENTITY_REF = re.compile(r"&([A-Za-z0-9-]+);")
ENTITY_DECL = re.compile(r'<!ENTITY\s+([A-Za-z0-9-]+)\s+"([^"]*)">')
KANA_ONLY = re.compile(r"^[぀-ヿー・]+$")

# UniDic POS -> JMdict POS code prefixes that are compatible with it.
POS_COMPAT = {
    "名詞": ("n", "vs"),
    "動詞": ("v",),
    "形容詞": ("adj-i", "adj-ix"),
    "形状詞": ("adj-na", "adj-no", "adj-t", "adj-f"),
    "副詞": ("adv",),
    "連体詞": ("adj-pn",),
}


def hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


# ---------------------------------------------------------------- JMdict

def load_jmdict():
    raw = gzip.decompress((DICT / "JMdict_e.gz").read_bytes()).decode("utf-8")
    labels = dict(ENTITY_DECL.findall(raw))
    # Keep entity codes (e.g. "v5k") instead of letting the parser expand them.
    raw = ENTITY_REF.sub(lambda m: m.group(0) if m.group(1) in STD_ENTITIES else m.group(1), raw)
    root = ET.fromstring(raw)
    entries = {}
    for e in root.iter("entry"):
        seq = int(e.findtext("ent_seq"))
        kebs = [(k.findtext("keb"), k.find("ke_pri") is not None) for k in e.findall("k_ele")]
        rebs = [(r.findtext("reb"), r.find("re_pri") is not None) for r in e.findall("r_ele")]
        senses, last_pos = [], []
        for s in e.findall("sense"):
            pos = [p.text for p in s.findall("pos")] or last_pos
            last_pos = pos
            glosses = [g.text for g in s.findall("gloss") if g.text]
            if glosses:
                senses.append({"pos": pos, "misc": [m.text for m in s.findall("misc")], "gloss": glosses})
        if senses:
            entries[seq] = {"seq": seq, "kebs": kebs, "rebs": rebs, "senses": senses,
                            "pri": any(p for _, p in kebs + rebs)}
    by_keb, by_reb = collections.defaultdict(set), collections.defaultdict(set)
    for seq, ent in entries.items():
        for k, _ in ent["kebs"]:
            by_keb[k].add(seq)
        for r, _ in ent["rebs"]:
            by_reb[hira(r)].add(seq)
    return entries, by_keb, by_reb, labels


def pos_compatible(entry, unidic_pos: str) -> bool:
    prefixes = POS_COMPAT.get(unidic_pos.split("-")[0], ())
    return any(p.startswith(prefixes) for s in entry["senses"] for p in s["pos"])


def spellings(row):
    """[(written form, its reading, count)] as seen in the transcripts, most common first."""
    out = []
    for part in row["written_forms"].split("|"):
        form_reading, count = part.rsplit(":", 1)
        form, reading = form_reading.split("/", 1)
        out.append((form, reading, int(count)))
    return out


KANJI = re.compile(r"[㐀-鿿々]")


def spelled_by(entry, form: str) -> bool:
    """True if a transcript spelling is consistent with a JMdict entry.

    Either the entry lists the spelling (kanji headword or reading), or the
    spelling shares a kanji with a headword (inflected bases such as the
    potential 救える of 救う).
    """
    kebs = {k for k, _ in entry["kebs"]}
    if form in kebs or hira(form) in {hira(r) for r, _ in entry["rebs"]}:
        return True
    return bool(set(KANJI.findall(form)) & set(KANJI.findall("".join(kebs))))


def lookup(row, entries, by_keb, by_reb):
    """Return (JMdict entry or None, reason) for a frequency row.

    Candidate sets are tried in this order:
      1. each kanji spelling seen in the transcripts (most common first),
         matched on headword AND reading;
      2. the UniDic lemma with its reading, matched on headword AND reading;
      3. each kana spelling, matched on reading, but only against entries that
         have no kanji headword or are marked "usually written in kana" (so a
         kana form never picks an arbitrary homophone).
    Within a set: the entry consistent with the most occurrences, then
    POS-compatible entries, then entries with frequency-priority tags, then
    the lowest JMdict sequence number.
    A match is accepted only if at least half of the word's occurrences are
    spelled consistently with it (this rejects tokenizer mis-analyses);
    otherwise the next candidate set is tried.
    """
    sp = spellings(row)
    total = sum(n for _, _, n in sp)

    def agreement(entry):
        return sum(n for f, _, n in sp if spelled_by(entry, f))

    def usually_kana(entry):
        return not entry["kebs"] or any("uk" in x["misc"] for x in entry["senses"])

    candidate_sets = []
    for form, reading in [(f, r) for f, r, _ in sp if not KANA_ONLY.match(f)] + [(row["lemma"], row["reading"])]:
        candidate_sets.append({s for s in by_keb.get(form, ())
                               if any(hira(r) == reading for r, _ in entries[s]["rebs"])})
    for form, reading, _ in sp:
        if KANA_ONLY.match(form) and hira(form) == reading:
            candidate_sets.append({s for s in by_reb.get(reading, ()) if usually_kana(entries[s])})

    rejected = None
    for cands in candidate_sets:
        if not cands:
            continue
        best = entries[min(cands, key=lambda s: (-agreement(entries[s]),
                                                not pos_compatible(entries[s], row["pos"]),
                                                not entries[s]["pri"], s))]
        agree = agreement(best)
        if agree * 2 >= total:
            return best, ""
        rejected = rejected or f"spelling mismatch with JMdict {best['seq']} ({agree}/{total})"
    return None, rejected or "no JMdict entry"


def headword(row, entry) -> str:
    """Word shown on the card front.

    The most common written form in the transcripts is used, unless it is kana
    while JMdict does not mark the word as usually written in kana; then the
    JMdict kanji headword is used (e.g. transcripts' いう -> 言う).
    """
    written = row["written"]
    kebs = [k for k, _ in entry["kebs"]]
    if not kebs or not KANA_ONLY.match(written):
        return written
    if "uk" in entry["senses"][0]["misc"]:
        return written
    if row["lemma"] in kebs:
        return row["lemma"]
    prio = [k for k, p in entry["kebs"] if p]
    return (prio or kebs)[0]


def format_meaning(entry, labels):
    parts, pos_seen = [], []
    for i, s in enumerate(entry["senses"][:MAX_SENSES], 1):
        parts.append(f"{i}. " + html.escape("; ".join(s["gloss"])))
        for p in s["pos"]:
            label = labels.get(p, p)
            if label not in pos_seen:
                pos_seen.append(label)
    return "<br>".join(parts), "; ".join(pos_seen)


# ---------------------------------------------------------------- JLPT

def load_jlpt():
    """Index three community JLPT lists.

    Returns (by_form, by_seq):
      by_form: written form -> [(reading, level)]  from jlpt-vocab-api and open-anki-jlpt-decks
      by_seq:  JMdict ent_seq -> {levels}          from Waller's lists linked to JMdict IDs
    An N5/N4 item that is a word plus adverbial に (e.g. すぐに) also grades the
    bare word (すぐ) at that level.
    """
    by_form = collections.defaultdict(list)
    by_seq = collections.defaultdict(set)

    def add(word, furi, level):
        word, furi = word.strip(), hira(furi.strip())
        if not word:
            return
        variants = {word, word.strip("～〜~"), re.sub(r"[（(][^）)]*[）)]", "", word),
                    re.sub(r"[（()）]", "", word)}
        for v in variants:
            if v:
                by_form[v].append((furi if KANA_ONLY.match(furi or "x") else "", level))
                if level >= 4 and len(v) > 2 and v.endswith("に"):
                    by_form[v[:-1]].append((furi[:-1] if furi.endswith("に") else "", level))

    for level in range(1, 6):
        for item in json.loads((DICT / f"jlpt_n{level}.json").read_text(encoding="utf-8")):
            add(item.get("word") or "", item.get("furigana") or "", level)
        with (DICT / f"openanki_n{level}.csv").open(encoding="utf-8") as fh:
            for item in csv.DictReader(fh):
                levels = {int(x) for x in re.findall(r"\bJLPT_N?(\d)\b", item.get("tags") or "")} or {level}
                for lv in levels:
                    add(item["expression"], item["reading"], lv)
        with (DICT / f"waller_jmdict_n{level}.csv").open(encoding="utf-8") as fh:
            for item in csv.DictReader(fh):
                if item["jmdict_seq"].strip():
                    by_seq[int(item["jmdict_seq"])].add(level)
                add(item["kanji"] or item["kana"], item["kana"], level)
    return by_form, by_seq


def jlpt_level(entry, row, jlpt_index):
    """Easiest (highest-numbered) JLPT level any of the lists gives this word."""
    by_form, by_seq = jlpt_index
    readings = {hira(r) for r, _ in entry["rebs"]}
    forms = {k for k, _ in entry["kebs"]} | {r for r, _ in entry["rebs"]} | {row["lemma"], row["written"]}
    levels = set(by_seq.get(entry["seq"], ()))
    for f in forms:
        for furi, level in by_form.get(f, ()):
            if (furi and furi in readings) or (not furi and hira(f) in readings):
                levels.add(level)
    return max(levels) if levels else None


# ---------------------------------------------------------------- examples

def load_examples(wanted_keys):
    videos = {}
    with (ROOT / "data" / "videos.tsv").open(encoding="utf-8") as fh:
        for v in csv.DictReader(fh, delimiter="\t"):
            videos[v["video_id"]] = v
    transcripts = {p.stem: json.loads(p.read_text(encoding="utf-8"))
                   for p in sorted((ROOT / "data" / "transcripts").glob("*.json"))}
    occ = collections.defaultdict(list)
    with (ROOT / "data" / "tokens.tsv").open(encoding="utf-8") as fh:
        for t in csv.DictReader(fh, delimiter="\t"):
            key = (t["lemma"], t["reading"])
            if key in wanted_keys:
                occ[key].append((t["video_id"], int(t["segment"]), int(t["offset"]), t["surface"]))
    examples = {}
    for key, hits in occ.items():
        # Prefer a sentence-length segment, then human-made (manual) captions,
        # then the earliest occurrence by video ID and position.
        def rank(h):
            data = transcripts[h[0]]
            text = clean_segment(data["transcript"][h[1]]["text"])
            return (not (8 <= len(text) <= 70), bool(data.get("is_auto_generated")), h[0], h[1], h[2])
        vid, seg_i, off, surface = min(hits, key=rank)
        seg = transcripts[vid]["transcript"][seg_i]
        # Same text the tokenizer saw, so the offset points at this exact token.
        text = clean_segment(seg["text"])
        assert text[off:off + len(surface)] == surface, (vid, seg_i, surface)
        ex = (html.escape(text[:off]) + f"<b>{html.escape(surface)}</b>"
              + html.escape(text[off + len(surface):]))
        start = int(float(seg["start_ms"])) // 1000
        title = html.escape(videos.get(vid, {}).get("title") or vid)
        source = f'<a href="https://youtu.be/{vid}?t={start}">{title}</a> ({start // 60}:{start % 60:02d})'
        examples[key] = (ex, source, text)
    return examples


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-count", type=int, default=MIN_COUNT)
    args = ap.parse_args()

    entries, by_keb, by_reb, labels = load_jmdict()
    jlpt_index = load_jlpt()
    with (ROOT / "data" / "frequency.tsv").open(encoding="utf-8") as fh:
        freq = list(csv.DictReader(fh, delimiter="\t"))

    audit, stats = [], collections.Counter()
    for row in freq:
        entry, reason = lookup(row, entries, by_keb, by_reb)
        if entry is None:
            stats["no_jmdict"] += 1
            stats["mismatch" if reason.startswith("spelling") else "unknown"] += 1
            audit.append((row, None, None, reason))
            continue
        level = jlpt_level(entry, row, jlpt_index)
        audit.append((row, entry, level, ""))

    def pick(rows, levels, min_count):
        return [a for a in rows if a[1] is not None and a[2] in levels and int(a[0]["count"]) >= min_count]

    # Tier A: JLPT N3-N1 words seen at least MIN_COUNT times.
    chosen = pick(audit, TARGET_LEVELS, args.min_count)
    tiers = {id(a): "A" for a in chosen}
    # Tier B (only if still short): JMdict words in no JLPT list, same count rule.
    if len(chosen) < MIN_CARDS:
        extra = pick(audit, (None,), args.min_count)
        for a in extra:
            tiers[id(a)] = "B"
        chosen += extra
    # Tier C (only if still short): relax the count rule for N3-N1 words.
    if len(chosen) < MIN_CARDS:
        extra = [a for a in pick(audit, TARGET_LEVELS, 1) if id(a) not in tiers]
        for a in extra:
            tiers[id(a)] = "C"
        chosen += extra

    # Two lemmas can map to one JMdict entry (e.g. spelling variants): keep the more frequent.
    seen, deck = set(), []
    for a in sorted(chosen, key=lambda a: (tiers[id(a)], int(a[0]["rank"]))):
        if a[1]["seq"] in seen:
            stats["dup_entry"] += 1
            continue
        seen.add(a[1]["seq"])
        deck.append(a)

    examples = load_examples({(a[0]["lemma"], a[0]["reading"]) for a in deck})

    model = genanki.Model(
        MODEL_ID,
        "Cody Japanese Vocab (YouTube corpus)",
        fields=[{"name": n} for n in ("Word", "Reading", "Meaning", "PartOfSpeech", "JLPT",
                                      "CorpusFrequency", "Example", "Source", "Rank")],
        templates=[{
            "name": "Recognition",
            "qfmt": '<div class="word">{{Word}}</div>',
            "afmt": ('{{FrontSide}}<hr id="answer">'
                     '<div class="reading">{{Reading}}</div>'
                     '<div class="meaning">{{Meaning}}</div>'
                     '<div class="meta">{{PartOfSpeech}}<br>JLPT {{JLPT}} · {{CorpusFrequency}}</div>'
                     '<div class="example">{{Example}}</div>'
                     '<div class="source">{{Source}}</div>'),
        }],
        css=(".card{font-family:'Hiragino Sans','Noto Sans JP',sans-serif;text-align:center;font-size:20px}"
             ".word{font-size:48px}.reading{font-size:26px;color:#555}.meaning{margin:12px 0;text-align:left}"
             ".meta{font-size:14px;color:#777}.example{margin-top:14px;font-size:22px}"
             ".source{font-size:12px;color:#999;margin-top:8px}"),
        sort_field_index=8,
    )
    gdeck = genanki.Deck(
        DECK_ID,
        "Japanese::YouTube Intermediate-Advanced Vocab",
        description=("Intermediate-advanced (JLPT N3-N1) vocabulary ranked by frequency in "
                     f"{len(list((ROOT / 'data' / 'transcripts').glob('*.json')))} Japanese YouTube videos. "
                     "Definitions from JMdict (EDRDG, CC BY-SA 4.0)."),
    )

    tsv_rows, level_counts, tier_counts = [], collections.Counter(), collections.Counter()
    for deck_rank, (row, entry, level, _) in enumerate(deck, 1):
        key = (row["lemma"], row["reading"])
        word = headword(row, entry)
        rebs = [r for r, _ in entry["rebs"]]
        # A kana headword is its own reading (e.g. やっぱ, not the lemma reading やはり).
        reading = next((r for r in rebs if KANA_ONLY.match(word) and hira(r) == hira(word)), None) \
            or next((r for r in rebs if hira(r) == row["reading"]), rebs[0])
        meaning, pos_label = format_meaning(entry, labels)
        ex, source, _ = examples.get(key, ("", "", ""))
        jlpt = f"N{level}" if level else "unlisted"
        freq_text = f"{row['count']}× in {row['videos']} videos (corpus rank {row['rank']})"
        fields = [word, reading, meaning, html.escape(pos_label), jlpt, freq_text, ex, source,
                  f"{deck_rank:04d}"]
        tags = [f"JLPT_{jlpt}", "youtube_corpus", f"jmdict_{entry['seq']}"]
        gdeck.add_note(genanki.Note(model=model, fields=fields, tags=tags,
                                    guid=genanki.guid_for(row["lemma"], row["reading"])))
        tsv_rows.append(fields + [" ".join(tags)])
        level_counts[jlpt] += 1

    for a in deck:
        tier_counts[tiers[id(a)]] += 1

    out = ROOT / "anki"
    out.mkdir(exist_ok=True)
    genanki.Package(gdeck).write_to_file(str(out / "cody_japanese_vocab.apkg"), timestamp=BUILD_TIMESTAMP)
    with (out / "cody_japanese_vocab.tsv").open("w", encoding="utf-8", newline="") as fh:
        fh.write("#separator:tab\n#html:true\n#tags column:10\n")
        fh.write("#columns:" + "\t".join(["Word", "Reading", "Meaning", "PartOfSpeech", "JLPT",
                                          "CorpusFrequency", "Example", "Source", "Rank", "Tags"]) + "\n")
        w = csv.writer(fh, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        for r in tsv_rows:
            w.writerow(r)

    with (ROOT / "data" / "deck_words.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["rank", "lemma", "reading", "count", "videos", "jmdict_seq", "jlpt", "in_deck", "note"])
        in_deck = {id(a[0]) for a in deck}
        for row, entry, level, note in audit:
            w.writerow([row["rank"], row["lemma"], row["reading"], row["count"], row["videos"],
                        entry["seq"] if entry else "", f"N{level}" if level else ("unlisted" if entry else ""),
                        "yes" if id(row) in in_deck else "", note])

    all_levels = collections.Counter(f"N{a[2]}" if a[2] else "unlisted" for a in audit if a[1])
    lines = [
        "# Deck statistics",
        "",
        f"- Corpus lemmas considered: {len(audit):,}",
        f"- Matched to a JMdict entry: {len(audit) - stats['no_jmdict']:,} "
        f"(dropped: {stats['unknown']:,} not in JMdict, {stats['mismatch']:,} failed the spelling check)",
        "- JLPT level of matched lemmas: " + ", ".join(f"{k}: {all_levels[k]:,}"
                                                       for k in ("N5", "N4", "N3", "N2", "N1", "unlisted")),
        f"- Selection rule: JLPT N3/N2/N1 and seen at least {args.min_count} times in the corpus "
        f"(tier A); fallbacks tier B (JLPT-unlisted, same count rule) and tier C (N3-N1 seen once) "
        f"are used only if tier A has fewer than {MIN_CARDS} words.",
        "- Tiers used: " + ", ".join(f"{t}: {tier_counts[t]}" for t in sorted(tier_counts) if t),
        f"- Duplicate JMdict entries merged: {stats['dup_entry']}",
        f"- **Cards in deck: {len(deck):,}** — " + ", ".join(f"{k}: {level_counts[k]}"
                                                            for k in ("N3", "N2", "N1", "unlisted") if level_counts[k]),
        "",
    ]
    (ROOT / "data" / "deck_stats.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
