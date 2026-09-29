#!/usr/bin/env python3
"""Deterministic frequency analysis of the lemmatised transcripts.

Input:  data/tokens.tsv (from lemmatize.py), data/videos.tsv
Output: data/frequency.tsv  one row per (lemma, reading), ranked
        data/corpus_stats.md summary of the corpus

Ranking is a stable sort on (count desc, video count desc, lemma asc,
reading asc), so ties always break the same way.
"""
import collections
import csv
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def most_common(counter: collections.Counter, prefer=None):
    # Highest count first, then the preferred value, then code-point order,
    # so ties are deterministic.
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0] != prefer, kv[0]))[0][0]


def main() -> None:
    counts = collections.Counter()
    videos = collections.defaultdict(set)
    written = collections.defaultdict(collections.Counter)
    surfaces = collections.defaultdict(collections.Counter)
    pos = collections.defaultdict(collections.Counter)
    total = 0
    with (ROOT / "data" / "tokens.tsv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            key = (row["lemma"], row["reading"])
            counts[key] += 1
            videos[key].add(row["video_id"])
            written[key][(row["written_base"], row["base_reading"])] += 1
            surfaces[key][row["surface"]] += 1
            pos[key][row["pos"]] += 1
            total += 1

    ranked = sorted(counts, key=lambda k: (-counts[k], -len(videos[k]), k[0], k[1]))
    with (ROOT / "data" / "frequency.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["rank", "lemma", "reading", "written", "pos", "count", "videos", "per_million",
                    "written_forms", "surfaces"])
        # written_forms: "form/reading:count" for every spelling seen, most common first.
        for rank, key in enumerate(ranked, 1):
            w.writerow([
                rank, key[0], key[1], most_common(written[key], (key[0], key[1]))[0], most_common(pos[key]),
                counts[key], len(videos[key]), f"{counts[key] * 1_000_000 / total:.1f}",
                "|".join(f"{f}/{r}:{n}" for (f, r), n in sorted(written[key].items(), key=lambda kv: (-kv[1], kv[0]))),
                "|".join(k for k, _ in sorted(surfaces[key].items(), key=lambda kv: (-kv[1], kv[0]))[:8]),
            ])

    with (ROOT / "data" / "videos.tsv").open(encoding="utf-8") as fh:
        vids = list(csv.DictReader(fh, delimiter="\t"))
    by_cat = collections.Counter(v["category"] or "?" for v in vids)
    lines = [
        "# Corpus statistics",
        "",
        f"- Videos with a Japanese transcript: **{len(vids)}** "
        f"({', '.join(f'{c}: {n}' for c, n in sorted(by_cat.items()))})",
        f"- Caption tracks: {sum(v['caption_kind'] == 'manual' for v in vids)} manual, "
        f"{sum(v['caption_kind'] == 'auto' for v in vids)} auto-generated",
        f"- Total video length: {sum(int(v['length_seconds'] or 0) for v in vids) / 3600:.1f} hours",
        f"- Transcript segments tokenised: {sum(int(v['segments']) for v in vids):,} (all of them)",
        f"- Transcript characters: {sum(int(v['characters']) for v in vids):,}",
        f"- Content-word tokens: {total:,}",
        f"- Distinct lemmas (lemma + reading): {len(counts):,}",
        f"- Lemmas seen at least twice: {sum(1 for k in counts if counts[k] >= 2):,}",
        f"- Lemmas seen in 2+ videos: {sum(1 for k in counts if len(videos[k]) >= 2):,}",
        "",
    ]
    (ROOT / "data" / "corpus_stats.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
