#!/usr/bin/env bash
# Rebuild everything from the committed transcripts.
# Pass --harvest to re-extract transcripts from the Claude Code session logs first.
set -euo pipefail
cd "$(dirname "$0")"
if [[ "${1:-}" == "--harvest" ]]; then
  python3 tools/harvest_transcripts.py
fi
python3 tools/fetch_dictionaries.py   # cached after the first run
python3 tools/lemmatize.py
python3 tools/frequency.py
python3 tools/build_deck.py
