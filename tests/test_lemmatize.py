import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

import fugashi  # noqa: E402

from lemmatize import clean_segment, token_rows  # noqa: E402

TAGGER = fugashi.Tagger()


def lemmas(text):
    return [row[2] for row in token_rows(TAGGER, clean_segment(text))]


def test_inflected_verbs_reduce_to_dictionary_form():
    assert "食べる" in lemmas("もう全部食べちゃった")
    assert "頑張る" in lemmas("毎日頑張ってるんだ")
    assert "気付く" in lemmas("全然気づかなかった") or "気づく" in lemmas("全然気づかなかった")


def test_adjectives_reduce_to_dictionary_form():
    assert "凄い" in lemmas("すごく面白かった")
    assert "面白い" in lemmas("すごく面白かった")


def test_function_words_and_sound_tags_are_dropped():
    out = lemmas("[音楽] [拍手] これはペンです")
    assert "音楽" not in out and "拍手" not in out
    assert "です" not in out and "は" not in out
    assert out == ["ペン"]


def test_offsets_point_at_the_token():
    text = clean_segment("漫画を読んだり、積読を消化して")
    for start, surface, *_ in token_rows(TAGGER, text):
        assert text[start:start + len(surface)] == surface


def test_tokenisation_is_deterministic():
    text = "今回は1人でプレイしていきたいと思いますちょっとホラーチックな謎解き"
    assert list(token_rows(TAGGER, text)) == list(token_rows(fugashi.Tagger(), text))
