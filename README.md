# Cody-Japanese: a YouTube-corpus vocabulary deck

An Anki deck of **1,850 intermediate-to-advanced Japanese words (JLPT N3 / N2 / N1)**. The words come from what people actually say in popular Japanese YouTube videos: game let's-plays, vlogs, and variety/prank shows. Every step after the transcripts were downloaded is a deterministic script. Each definition is copied from JMdict, and each example sentence is copied from the video transcript it cites.

**Import this:** `anki/cody_japanese_vocab.apkg` (in Anki: *File → Import*).
`anki/cody_japanese_vocab.tsv` has the same notes as a plain-text import file.

## What's in the deck

| | |
|---|---|
| Videos analysed | 45 (gaming 18, variety 20, vlog 7), 20.9 hours |
| Transcript segments tokenised | 23,186, every one |
| Content-word tokens | 62,811 |
| Distinct lemmas | 7,788 |
| Cards | **1,850**: N3 991, N2 356, N1 503 |

Each card has:

- **Front:** the word.
- **Back:**
  - reading;
  - up to three JMdict senses;
  - part of speech;
  - JLPT level;
  - corpus frequency ("348× in 31 videos");
  - a real sentence from a transcript with the word in bold;
  - a timestamped link to that moment in the video.

Cards are ordered by how often the word occurs in the corpus, so the words you'll hear most come first. Note GUIDs are fixed, so re-importing an updated deck updates the existing cards instead of duplicating them.

## Pipeline

```
TubeAlfred (MCP) ──► session logs ──► harvest ──► lemmatize ──► frequency ──► build_deck ──► .apkg / .tsv
                                                                                  ▲
                                          fetch_dictionaries (JMdict + JLPT lists)┘
```

| Step | Script | Output |
|---|---|---|
| 1. Collect | TubeAlfred `youtube_search_query` and `youtube_video_transcript` (`language=ja`) | Raw tool results in the Claude Code session log |
| 2. Harvest | `tools/harvest_transcripts.py` reads the raw tool results straight from the session logs, so no transcript text is re-typed | `data/transcripts/*.json`, `data/videos.tsv`, `data/unavailable.tsv` |
| 3. Base forms | `tools/lemmatize.py`: MeCab (fugashi) + UniDic-lite. Tokenises every segment and keeps content words (nouns, verbs, adjectives, adverbs, adnominals) in dictionary form, e.g. 食べちゃった → 食べる | `data/tokens.tsv` |
| 4. Frequency | `tools/frequency.py`: counts per (lemma, reading), number of videos, per-million rate, and spellings seen. Stable sort | `data/frequency.tsv`, `data/corpus_stats.md` |
| 5. Dictionaries | `tools/fetch_dictionaries.py`: JMdict (EDRDG) and three JLPT lists. Records URLs and sha256 | `data/dict/`, `data/dict/SOURCES.md` |
| 6. Deck | `tools/build_deck.py`: JMdict lookup, JLPT grading, selection, examples, Anki export | `anki/*`, `data/deck_words.tsv`, `data/deck_stats.md` |

Run it all:

```bash
pip install -r requirements.txt
./run_pipeline.sh            # rebuild from the committed transcripts
pytest -q                    # determinism + integrity checks
```

### Rules the deck builder follows

- **Dictionary match (JMdict):**
  - The spellings seen in the transcripts are tried first, then the UniDic lemma, each matched on headword *and* reading.
  - A kana-only spelling can only match entries that are kana words or marked "usually written in kana", so an ambiguous kana form never picks a random homophone.
  - A match is kept only if at least half the word's occurrences are spelled consistently with the entry. This catches tokenizer mistakes such as the interjection おっ being tagged as 伯父.
  - Words with no match are dropped. The reason is recorded in `data/deck_words.tsv`.
- **Difficulty:** three community JLPT lists are combined, and each word takes the *easiest* level any list gives it, which errs toward excluding basic words:
  - jlpt-vocab-api;
  - Waller's lists linked to JMdict IDs;
  - open-anki-jlpt-decks.

  An N5/N4 item that is a word plus adverbial に (e.g. すぐに) also grades the bare word. Only N3, N2 and N1 words are used.
- **Noise filter:** a word must occur at least twice, to screen out one-off speech-recognition errors.
- **Fallbacks:** if fewer than 1,000 words qualified, the builder would add JLPT-unlisted words, then N3–N1 words seen once. This wasn't needed: 1,850 words met the main rule.
- **Examples:** a sentence-length segment is preferred, then a human-made (manual) caption. The bolded word is located by the tokenizer's character offset. The sentence is the caption text after Unicode NFKC normalisation, with `[音楽]`-style sound tags removed.

## Limitations

- **45 videos, not 100.** The TubeAlfred account had 48 credits. Each search and each transcript costs one, and the user chose to stay within them. Six more candidates had no Japanese caption track, and those lookups weren't charged; they're listed in `data/unavailable.tsv`.
- **27 of the 45 transcripts are YouTube auto-captions** and contain recognition errors. The count ≥ 2 rule and the spelling check remove most of the noise, but not all of it. Examples prefer manual captions: 1,488 of the 1,850 example sentences come from manual-caption videos and 362 from auto-captions.
- **JLPT lists are unofficial.** No official list has existed since 2010. The community lists put a few very common counters or nouns (回, 時, 分, 階) at N3/N1, and they keep those grades here.
- **UniDic's lemma is sometimes a literary variant.** For example, 感じ is analysed as 感ずる, so the card shows 感ずる rather than 感じる.

## Sources and licences

- **Transcripts:** YouTube captions, retrieved via the TubeAlfred MCP connector. They're used here for vocabulary study; video IDs and links are included.
- **JMdict:** © Electronic Dictionary Research and Development Group, [CC BY-SA 4.0](https://www.edrdg.org/edrdg/licence.html).
- **JLPT lists:**
  - Jonathan Waller ([tanos.co.uk](http://www.tanos.co.uk/jlpt/), CC BY), via jlpt-vocab-api (`jlpt-vocab-api.vercel.app`) and [stephenmk/yomitan-jlpt-vocab](https://github.com/stephenmk/yomitan-jlpt-vocab);
  - [jamsinclair/open-anki-jlpt-decks](https://github.com/jamsinclair/open-anki-jlpt-decks).
- **Tokenizer:** MeCab via [fugashi](https://github.com/polm/fugashi), with [unidic-lite](https://github.com/polm/unidic-lite).
