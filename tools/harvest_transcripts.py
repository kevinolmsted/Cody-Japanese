#!/usr/bin/env python3
"""Extract TubeAlfred transcripts and search results from Claude Code session logs.

The transcripts were fetched with the TubeAlfred MCP connector
(`youtube_video_transcript`). Claude Code writes every raw tool result to the
session log (``<project>/<session>.jsonl``); results that are too large for
the conversation are written verbatim to ``<project>/<session>/tool-results``.
This script reads those files directly, so no transcript text is ever
re-typed by a model.

Outputs (all sorted, so re-runs produce identical files):
  data/transcripts/<video_id>.json   the raw TubeAlfred ``data`` object
  data/search_results.json           every video seen in TubeAlfred searches
  data/videos.tsv                    one row per harvested transcript
  data/unavailable.tsv               videos that had no Japanese track
"""
import argparse
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = pathlib.Path.home() / ".claude" / "projects" / "-home-user-Cody-Japanese"


def iter_payload_texts(log_dir: pathlib.Path):
    """Yield raw tool-result strings from session logs and persisted results."""
    for jsonl in sorted(log_dir.rglob("*.jsonl")):
        with jsonl.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = rec.get("message")
                if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
                    continue
                for block in msg["content"]:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    content = block.get("content")
                    if isinstance(content, str):
                        yield content
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                yield part["text"]
    for txt in sorted(log_dir.rglob("tool-results/*.txt")):
        yield txt.read_text(encoding="utf-8")


def parse_payload(text: str):
    text = text.strip()
    if not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) and isinstance(obj.get("data"), dict) else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", type=pathlib.Path, default=DEFAULT_LOG_DIR)
    args = ap.parse_args()
    if not args.log_dir.exists():
        sys.exit(f"log dir not found: {args.log_dir}")

    transcripts, search_videos, unavailable = {}, {}, {}
    for text in iter_payload_texts(args.log_dir):
        obj = parse_payload(text)
        if obj is None:
            continue
        data = obj["data"]
        if obj.get("status") == 200 and "transcript" in data and data.get("video_id"):
            if data.get("language_code") == "ja" and data["transcript"]:
                transcripts[data["video_id"]] = data
        elif isinstance(data.get("videos"), list):
            for v in data["videos"]:
                if isinstance(v, dict) and v.get("id"):
                    search_videos[v["id"]] = v
        elif data.get("code") == "YOUTUBE_TRANSCRIPT_UNAVAILABLE":
            det = data.get("details", {})
            vid = det.get("videoId")
            if vid:
                tracks = ",".join(sorted({t["languageCode"] for t in det.get("availableTracks", [])}))
                unavailable[vid] = tracks

    out_dir = ROOT / "data" / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.json"):
        if old.stem not in transcripts:
            old.unlink()
    for vid, data in sorted(transcripts.items()):
        (out_dir / f"{vid}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )

    (ROOT / "data" / "search_results.json").write_text(
        json.dumps([search_videos[k] for k in sorted(search_videos)], ensure_ascii=False, indent=1, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    categories = {}
    sel = ROOT / "data" / "selection.tsv"
    if sel.exists():
        with sel.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                categories[row["video_id"]] = row["category"]

    with (ROOT / "data" / "videos.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["video_id", "category", "title", "channel", "length_seconds", "view_count",
                    "caption_kind", "segments", "characters"])
        for vid, data in sorted(transcripts.items()):
            meta = search_videos.get(vid, {})
            segs = data["transcript"]
            w.writerow([
                vid,
                categories.get(vid, ""),
                meta.get("title", ""),
                (meta.get("channel") or {}).get("title", ""),
                meta.get("length_seconds", ""),
                meta.get("view_count_int", ""),
                "auto" if data.get("is_auto_generated") else "manual",
                len(segs),
                sum(len(s["text"]) for s in segs),
            ])

    with (ROOT / "data" / "unavailable.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["video_id", "available_tracks"])
        for vid in sorted(unavailable):
            if vid not in transcripts:
                w.writerow([vid, unavailable[vid]])

    print(f"transcripts: {len(transcripts)}  search results: {len(search_videos)}  "
          f"no-ja: {len(set(unavailable) - set(transcripts))}")


if __name__ == "__main__":
    main()
