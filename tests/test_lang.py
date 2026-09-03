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
language_names = ["testish", "тестовый"]
language_chosen = "Testish it is."
[badge]
idle = "asleep"
listening = "listening"
thinking = "thinking"
speaking = "talking"
[say]
action_done = "Done."
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


def test_no_spoken_line_is_hardcoded_in_the_router(tmp_path):
    """plugins.py held a Russian "Готово." as an action's default, and said it.

    Anything spoken belongs to a locale. This catches the next one that does
    not, by reading the router itself rather than by trusting a review.
    """
    router = (REPO / "plugins.py").read_text(encoding="utf-8")
    cyrillic = [line for line in router.splitlines()
                if re.search(r"[Ѐ-ӿ]", line)
                and not line.lstrip().startswith("#")]
    assert not cyrillic, f"spoken text left in plugins.py: {cyrillic}"


# --------------------------------------------------------------------------
# choosing a language out loud, and the word on the badge
# --------------------------------------------------------------------------

@pytest.mark.parametrize("code", LANGS)
def test_every_shipped_locale_names_its_own_badge_words(code):
    """A missing word leaves the capsule blank in that state."""
    loc = lang.load(code, REPO)
    for state in lang.REQUIRED_BADGE:
        assert loc.badge[state].strip()


@pytest.mark.parametrize("code", LANGS)
def test_every_shipped_locale_can_be_chosen_out_loud(code):
    """The first-wake question is useless for a language nobody can name."""
    loc = lang.load(code, REPO)
    assert loc.language_names, "no way to say which language this is"
    assert loc.language_chosen.strip(), "nothing to confirm the choice with"
    assert loc.english_name().isascii(), "the English question needs an English name"


def test_the_default_locale_carries_the_setup_question():
    """It is asked before anybody has chosen, so only this one has to hold it."""
    loc = lang.load(lang.DEFAULT_LANG, REPO)
    for key in lang.SETUP_ONLY:
        assert getattr(loc, key).strip(), f"{key} is what a first run says"
    assert "{language}" in loc.language_fetching
    # The retry line names the locales on disk, so adding one cannot make it lie.
    assert "{languages}" in loc.language_unclear


@pytest.mark.parametrize("said, want", [
    ("давай по-русски", "ru"),
    ("Russian, please", "ru"),
    ("english", "en"),
    ("let us speak English then", "en"),
    ("по русски", "ru"),
    ("what time is it", None),
    ("", None),
])
def test_an_answer_about_language_picks_a_locale(said, want):
    assert lang.match_language(said, REPO) == want


def test_the_longest_name_wins(tmp_path):
    """A language whose name contains another must not lose to it."""
    write(tmp_path, COMPLETE, "xx")
    write(tmp_path, COMPLETE.replace(
        'language_names = ["testish", "тестовый"]',
        'language_names = ["old testish"]'), "yy")
    assert lang.match_language("speak old testish", tmp_path) == "yy"


def test_a_locale_without_badge_words_is_refused(tmp_path):
    body = COMPLETE.replace('listening = "listening"\n', "")
    write(tmp_path, body, "xx")
    with pytest.raises(lang.LocaleError, match="listening"):
        lang.load("xx", tmp_path)


def test_a_locale_nobody_can_ask_for_is_refused(tmp_path):
    body = COMPLETE.replace('language_names = ["testish", "тестовый"]\n', "")
    write(tmp_path, body, "xx")
    with pytest.raises(lang.LocaleError, match="language_names"):
        lang.load("xx", tmp_path)
