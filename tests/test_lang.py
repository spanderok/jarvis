# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["pytest", "tomli; python_version < '3.11'"]
# ///
"""Tests for the locale pack.

Run: uv run --with pytest pytest tests/ -q

A locale fails the way routing does - quietly. A missing line does not crash
anything; he just says nothing where a sentence belonged, and the owner hears a
pause and assumes the microphone missed him. So every shipped locale is checked
for completeness here, and a half-translated one is made loud.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import lang  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
LANGS = lang.available(REPO)


def write(tmp_path: pathlib.Path, body: str, name: str = "xx") -> pathlib.Path:
    """A whole locale in a temp folder, so a test never reads a shipped one."""
    (tmp_path / "locales").mkdir(exist_ok=True)
    (tmp_path / "locales" / f"{name}.toml").write_text(body, encoding="utf-8")
    return tmp_path


COMPLETE = '''
name = "Test"
letters = "a-z"
acks = ["Right."]
stop_words = ["stop"]
reset_words = ["start over"]
listening = "Listening."
not_heard = "Say again."
stopped = "Standing down."
forgotten = "Forgotten."
stranger_line = "Only the owner."
owner_intro = "you serve {owner}. "
owner_intro_anon = "you serve the owner. "
persona = "You are a butler, {owner_intro}Be brief."
voice_ask = "(spoken) {q}"
relay_ask = "ask {peer}: {q}"
[say]
action_failed = "No."
ask_ack = "Asked {label}."
tell_ack = "Told {label}."
room_missing = "No {label}."
room_relay = "{label} via {relay}."
room_fallback = "{label} -> {fallback}."
no_such_room = "No such room."
room_silent = "{label} is silent."
room_answering = "{label} answers."
session_down = "Session down."
no_answer = "Nothing came back."
key_only = "Key only."
'''


# --------------------------------------------------------------------------
# the locales that ship
# --------------------------------------------------------------------------

def test_the_repo_ships_at_least_english():
    assert "en" in LANGS
    assert lang.DEFAULT_LANG in LANGS, "the default locale must exist"


@pytest.mark.parametrize("code", LANGS)
def test_every_shipped_locale_loads(code):
    assert lang.load(code, REPO).name


@pytest.mark.parametrize("code", LANGS)
def test_every_shipped_locale_fills_in_every_say_line(code):
    """One missing line is one place he answers with silence."""
    loc = lang.load(code, REPO)
    for key in lang.REQUIRED_SAY:
        assert loc.say(key, label="x", relay="y", fallback="z").strip()


@pytest.mark.parametrize("code", LANGS)
def test_every_shipped_locale_names_its_models(code):
    """A locale with no voice and no recognizer cannot actually be run."""
    loc = lang.load(code, REPO)
    for field_name in ("asr_model", "wake_model", "wake_model_url",
                       "tts_backend", "system_voice"):
        assert getattr(loc, field_name), f"{code}: {field_name} is empty"


@pytest.mark.parametrize("code", LANGS)
def test_the_wake_word_is_among_its_own_variants(code):
    """Otherwise a clean transcription of the name does not wake him."""
    loc = lang.load(code, REPO)
    assert loc.name.lower() in loc.wake_variants


@pytest.mark.parametrize("code", LANGS)
def test_stop_and_reset_words_never_overlap(code):
    """The stop check runs first, so a shared word makes reset unreachable."""
    loc = lang.load(code, REPO)
    assert not (loc.stop_words & loc.reset_words)


@pytest.mark.parametrize("code", LANGS)
def test_a_stop_word_survives_normalisation(code):
    """They are matched against a phrase stripped to letters and spaces."""
    loc = lang.load(code, REPO)
    for word in loc.stop_words | loc.reset_words:
        assert loc.strip_re.sub("", word) == word, (
            f"{word!r} has characters that normalisation removes")


@pytest.mark.parametrize("code", LANGS)
def test_the_persona_says_what_language_to_answer_in(code):
    """A Russian voice reading an English answer is the failure this catches."""
    loc = lang.load(code, REPO)
    assert "{owner_intro}" not in loc.system_prompt("Ada")
    assert "Ada" in loc.system_prompt("Ada")
    assert "Ada" not in loc.system_prompt("")


# --------------------------------------------------------------------------
# what the two shipped locales must agree on
# --------------------------------------------------------------------------

def test_english_and_russian_offer_the_same_lines():
    """A locale added later must not quietly drop half the sentences."""
    en, ru = lang.load("en", REPO), lang.load("ru", REPO)
    assert set(en.fallbacks) == set(ru.fallbacks)


def test_russian_still_counts_latin_as_word_characters():
    """Half the technical vocabulary arrives spelled in it."""
    ru = lang.load("ru", REPO)
    assert ru.word_re.findall("посмотри gitlab") == ["посмотри", "gitlab"]
    assert not ru.script_re.search("just english here")
    assert ru.script_re.search("немного текста")


# --------------------------------------------------------------------------
# a broken locale stops him rather than starting half-translated
# --------------------------------------------------------------------------

def test_an_unknown_language_names_the_ones_that_exist(tmp_path):
    write(tmp_path, COMPLETE)
    with pytest.raises(lang.LocaleError, match="xx"):
        lang.load("nope", tmp_path)


@pytest.mark.parametrize("drop", ["persona", "acks", "stop_words", "listening"])
def test_a_missing_field_is_refused_by_name(tmp_path, drop):
    body = "\n".join(line for line in COMPLETE.splitlines()
                     if not line.startswith(f"{drop} "))
    write(tmp_path, body)
    with pytest.raises(lang.LocaleError, match=drop):
        lang.load("xx", tmp_path)


def test_a_missing_say_line_is_refused_by_name(tmp_path):
    body = COMPLETE.replace('tell_ack = "Told {label}."\n', "")
    write(tmp_path, body)
    with pytest.raises(lang.LocaleError, match="tell_ack"):
        lang.load("xx", tmp_path)


@pytest.mark.parametrize("field,token", [
    ("persona", "{owner_intro}"),
    ("owner_intro", "{owner}"),
    ("voice_ask", "{q}"),
])
def test_a_placeholder_that_was_translated_away_is_refused(tmp_path, field, token):
    """Translating the braces is the easy mistake, and it loses the question."""
    body = re.sub(rf"^{field} = .*$", f'{field} = "nothing here"',
                  COMPLETE, flags=re.M)
    write(tmp_path, body)
    with pytest.raises(lang.LocaleError, match=re.escape(token)):
        lang.load("xx", tmp_path)


def test_letters_that_are_not_a_character_class_are_refused(tmp_path):
    write(tmp_path, COMPLETE.replace('letters = "a-z"', 'letters = "z-a"'))
    with pytest.raises(lang.LocaleError, match="character class"):
        lang.load("xx", tmp_path)


def test_asking_for_a_say_line_that_does_not_exist_is_loud(tmp_path):
    write(tmp_path, COMPLETE)
    with pytest.raises(lang.LocaleError, match="typo"):
        lang.load("xx", tmp_path).say("typo")


# --------------------------------------------------------------------------
# the lines are said out loud, so filling them can never raise
# --------------------------------------------------------------------------

def test_an_unknown_placeholder_survives_instead_of_raising(tmp_path):
    write(tmp_path, COMPLETE.replace('room_missing = "No {label}."',
                                     'room_missing = "No {labl}."'))
    assert lang.load("xx", tmp_path).say("room_missing", label="x") == "No {labl}."


def test_a_stray_brace_leaves_the_sentence_sayable(tmp_path):
    write(tmp_path, COMPLETE.replace('no_such_room = "No such room."',
                                     'no_such_room = "No such } room."'))
    assert "room" in lang.load("xx", tmp_path).say("no_such_room")


# --------------------------------------------------------------------------
# the environment overrides a few personal choices, not the translation
# --------------------------------------------------------------------------

def test_the_environment_can_swap_the_voice(tmp_path, monkeypatch):
    write(tmp_path, COMPLETE)
    monkeypatch.setenv("JARVIS_LANG", "xx")
    monkeypatch.setenv("JARVIS_VOICE", "en_US-lessac-medium")
    assert lang.current(tmp_path).tts_voice == "en_US-lessac-medium"


def test_the_environment_picks_the_language(tmp_path, monkeypatch):
    write(tmp_path, COMPLETE, name="zz")
    monkeypatch.setenv("JARVIS_LANG", "zz")
    assert lang.current(tmp_path).lang == "zz"
