# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["tomli; python_version < '3.11'"]
# ///
"""The language Jarvis hears and speaks, kept out of the code.

Everything that is a language and not a mechanism lives in `locales/<lang>.toml`:
the wake word and the ways a recognizer mangles it, the words that mean "stop",
the sentences he says when nobody asked him anything yet, and the system prompt
that gives him his manner. The daemon holds none of it.

    JARVIS_LANG=en   (default)   locales/en.toml
    JARVIS_LANG=ru               locales/ru.toml

A locale also names the models that can handle that language - the wake-word
recognizer, the transcriber, the voice - because a Russian prompt read by an
English voice is not a setting anybody wants by accident.

Add a language by copying one file. Nothing else knows the difference.

Named lang.py and not locale.py on purpose: the standard library owns that
name, and a module here would shadow it for every package that imports it.
"""

from __future__ import annotations

import os
import pathlib
import re
from dataclasses import dataclass, field

try:
    import tomllib
except ModuleNotFoundError:                     # 3.10
    import tomli as tomllib                     # type: ignore


class LocaleError(Exception):
    """A locale file that cannot be used. Raised at start, never mid-phrase."""


DEFAULT_LANG = "en"

# Every field a locale must fill in. Listed here rather than checked one by one
# so a half-translated file is refused with the missing names, not with a
# KeyError three hundred phrases later.
REQUIRED = (
    "name", "letters", "acks", "stop_words", "reset_words",
    "listening", "not_heard", "stopped", "forgotten",
    "stranger_line", "persona", "owner_intro", "owner_intro_anon",
    "voice_ask", "relay_ask", "language_names", "language_chosen",
)

# The badge shows one word for what he is doing, and it is a word of this
# language like every other. Missing one would leave the capsule blank.
REQUIRED_BADGE = ("idle", "listening", "thinking", "speaking")

# Asked once, in English, before anybody has chosen a language - so only the
# default locale has to carry them. See `setup` below.
SETUP_ONLY = ("language_ask", "language_unclear", "language_fetching")

# The [say] table: what he says about a room or an action that brought no
# wording of its own. Every one of these has a call site in the daemon, so a
# locale missing one would answer with an empty sentence.
REQUIRED_SAY = (
    "action_done", "action_failed", "ask_ack", "tell_ack", "room_missing", "room_relay",
    "room_fallback", "no_such_room", "room_silent", "room_answering",
    "session_down", "no_answer", "key_only",
)


@dataclass(frozen=True)
class Locale:
    """One language, complete enough to run him."""

    lang: str

    # --- the name he answers to ------------------------------------------
    name: str = "Jarvis"
    # Whatever the small wake-word model turns the name into. The exact test
    # runs first and is cheap; similarity below catches the rest.
    wake_variants: tuple[str, ...] = ()
    # Only words the model can actually pronounce. A word it cannot is dropped
    # with a warning on every recognizer build, and one is built per phrase.
    wake_grammar_words: tuple[str, ...] = ()
    # Openings that are politeness, not content: "hey jarvis", "эй джарвис".
    greetings: tuple[str, ...] = ()

    # --- what counts as a word in this language --------------------------
    # Character classes, written without the brackets.
    # `letters` is everything that can appear inside a word - for Russian that
    # includes the latin range, because half the technical vocabulary arrives
    # spelled in it. `script` is narrower: the characters that only this
    # language has, used to notice that the transcriber returned another one.
    letters: str = "a-z"
    script: str = "a-z"

    # --- short things he says --------------------------------------------
    acks: tuple[str, ...] = ()          # while a long question is transcribed
    listening: str = ""                 # after a bare wake word
    not_heard: str = ""                 # the recording held no words
    stopped: str = ""                   # a stop word was said
    forgotten: str = ""                 # the session was reset
    reset_elsewhere: str = ""           # reset asked for in a room's window
    stranger_line: str = ""             # the voice lock refused a stranger

    # --- words he listens for --------------------------------------------
    stop_words: frozenset[str] = frozenset()
    reset_words: frozenset[str] = frozenset()

    # --- choosing this language, and showing it on the badge -------------
    # What you say to pick this language when he asks on the very first wake:
    # its own name in its own words, plus the English one, because half the
    # people answering will say "Russian" and half "русский".
    language_names: tuple[str, ...] = ()
    language_chosen: str = ""           # confirmation, in this language
    # The setup dialogue happens before anybody has chosen, so it is English
    # and only locales/en.toml fills these in.
    language_ask: str = ""
    language_unclear: str = ""
    language_fetching: str = ""         # holds {language}
    # One word per state for the floating badge - "listening", "слушаю".
    badge: dict[str, str] = field(default_factory=dict, repr=False)

    # --- how he answers ---------------------------------------------------
    persona: str = ""                   # system prompt, holds {owner_intro}
    owner_intro: str = ""               # holds {owner}
    owner_intro_anon: str = ""
    voice_ask: str = ""                 # holds {q}
    relay_ask: str = ""                 # holds {peer} and {q}

    # --- models that speak this language ---------------------------------
    asr_model: str = ""                 # parakeet-mlx repo id
    wake_model: str = ""                # vosk recognizer folder name
    wake_model_url: str = ""            # where install.sh gets it
    tts_backend: str = ""               # piper | vosk
    tts_voice: str = ""                 # voice name inside that backend
    tts_voice_url: str = ""
    edge_voice: str = ""                # network fallback
    system_voice: str = ""              # macOS `say -v`, the last resort
    # Two more macOS voices of this language, used as the "not him" crowd
    # when a voice print is enrolled. Free, and already on every Mac.
    cohort_voices: tuple[str, ...] = ()

    # Symbols a voice reads badly, and the words that replace them, applied to
    # text scraped off another session's screen. Which symbol needs a word is a
    # language matter: "°C" is read fine in English and not in Russian.
    spoken_swaps: tuple[tuple[str, str], ...] = ()

    # Conditions enroll_voice.py records, as (id, how to say it, what to say).
    enroll: tuple[tuple[str, str, str], ...] = ()

    # What he says about a room or an action that carries no wording of its
    # own. Read through `say()` so a missing key is a loud KeyError at start,
    # not a silent empty sentence three hundred phrases in.
    fallbacks: dict[str, str] = field(default_factory=dict, repr=False)

    source: str = ""

    # Cached regexes, built once per locale rather than per phrase.
    word_re: re.Pattern = field(default=re.compile(r"[a-z]+"), repr=False)
    strip_re: re.Pattern = field(default=re.compile(r"[^a-z ]"), repr=False)
    script_re: re.Pattern = field(default=re.compile(r"[a-z]", re.I), repr=False)

    def english_name(self) -> str:
        """What to call this language in the English setup question.

        Taken from `language_names` rather than a field of its own: every
        locale already lists the English name there, because half the people
        answering "which language?" answer in English.
        """
        for alias in self.language_names:
            if alias.isascii() and alias.strip():
                return alias.strip().capitalize()
        return self.lang.upper()

    def owner_line(self, owner: str) -> str:
        """The one sentence that names whose computer this is."""
        return (self.owner_intro.format(owner=owner) if owner
                else self.owner_intro_anon)

    def system_prompt(self, owner: str, hints: str = "") -> str:
        return self.persona.format(owner_intro=self.owner_line(owner)) + hints

    def say(self, key: str, **names) -> str:
        """One of the [say] lines, with {label} and friends filled in."""
        try:
            template = self.fallbacks[key]
        except KeyError:
            raise LocaleError(f"{self.source}: [say] has no {key!r}") from None
        return fill(template, **names)


def fill(template: str, **names) -> str:
    """format() that survives a brace nobody defined.

    These strings are read out loud. A typo in a placeholder must leave the
    sentence sayable, not raise in the middle of an answer.
    """
    class _Blanks(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    try:
        return template.format_map(_Blanks(names))
    except (ValueError, IndexError):
        return template


def _as_tuple(value, name: str, src: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise LocaleError(f"{src}: {name} must be a string or a list of strings")


def path_for(lang: str, root: pathlib.Path | None = None) -> pathlib.Path:
    root = root or pathlib.Path(__file__).resolve().parent
    return root / "locales" / f"{lang}.toml"


def available(root: pathlib.Path | None = None) -> list[str]:
    root = root or pathlib.Path(__file__).resolve().parent
    folder = root / "locales"
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.toml"))


def match_language(said: str, root: pathlib.Path | None = None) -> str | None:
    """Which locale an answer to "which language should I speak?" picks.

    The answer arrives from the transcriber, so it is a whole sentence in
    whatever language the person felt like: "russian please", "давай по-русски".
    Every locale lists the words that pick it, in its own language and in
    English, and the longest match wins - so a name that contains another
    ("british english") cannot be stolen by the shorter one.
    """
    said = " " + re.sub(r"[^\w ]+", " ", said.lower(), flags=re.UNICODE) + " "
    said = re.sub(r"\s+", " ", said)
    best: tuple[int, str] | None = None
    for code in available(root):
        try:
            loc = load(code, root)
        except LocaleError:
            continue                     # a broken locale must not block setup
        for alias in loc.language_names:
            alias = alias.strip().lower()
            if alias and f" {alias} " in said and (best is None or len(alias) > best[0]):
                best = (len(alias), code)
    return best[1] if best else None


def setup_lines(root: pathlib.Path | None = None) -> Locale:
    """The locale that speaks the first-run question - always the default one.

    Nobody has chosen a language at that point, and the models on disk after a
    plain `install.sh` are the default language's, so the question is asked in
    it and in no other.
    """
    return load(DEFAULT_LANG, root)


def load(lang: str | None = None, root: pathlib.Path | None = None) -> Locale:
    """Read one locale, or fail loudly enough to fix before he is started."""
    lang = (lang or os.environ.get("JARVIS_LANG") or DEFAULT_LANG).strip().lower()
    path = path_for(lang, root)
    if not path.is_file():
        have = ", ".join(available(root)) or "none"
        raise LocaleError(
            f"no locale {lang!r}: {path} does not exist (have: {have})")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise LocaleError(f"{path.name}: {e}") from None

    src = path.name
    missing = [k for k in REQUIRED if not data.get(k)]
    if missing:
        raise LocaleError(f"{src}: nothing filled in for {', '.join(missing)}")

    fallbacks = data.get("say", {})
    if not isinstance(fallbacks, dict):
        raise LocaleError(f"{src}: [say] must be a table")
    missing = [k for k in REQUIRED_SAY if not str(fallbacks.get(k, "")).strip()]
    if missing:
        raise LocaleError(f"{src}: [say] is missing {', '.join(missing)}")
    fallbacks = {k: str(v) for k, v in fallbacks.items()}

    badge = data.get("badge", {})
    if not isinstance(badge, dict):
        raise LocaleError(f"{src}: [badge] must be a table")
    missing = [k for k in REQUIRED_BADGE if not str(badge.get(k, "")).strip()]
    if missing:
        raise LocaleError(f"{src}: [badge] is missing {', '.join(missing)}")
    badge = {k: str(v) for k, v in badge.items()}

    letters = str(data["letters"])
    script = str(data.get("script") or letters)
    try:
        word_re = re.compile(f"[{letters}]+")
        strip_re = re.compile(f"[^{letters} ]")
        script_re = re.compile(f"[{script}]", re.IGNORECASE)
    except re.error as e:
        raise LocaleError(f"{src}: letters/script {letters!r}/{script!r} "
                          f"is not a character class ({e})")

    text = {k: str(data.get(k, "")) for k in (
        "name", "listening", "not_heard", "stopped", "forgotten",
        "reset_elsewhere", "stranger_line", "persona", "owner_intro",
        "owner_intro_anon", "voice_ask", "relay_ask", "language_chosen",
        "language_ask", "language_unclear", "language_fetching", "asr_model",
        "wake_model", "wake_model_url", "tts_backend", "tts_voice",
        "tts_voice_url", "edge_voice", "system_voice")}

    for name, must_hold in (("persona", "{owner_intro}"),
                            ("owner_intro", "{owner}"),
                            ("voice_ask", "{q}")):
        if must_hold not in text[name]:
            raise LocaleError(f"{src}: {name} must contain {must_hold}")
    if text["relay_ask"] and not ("{q}" in text["relay_ask"]
                                  and "{peer}" in text["relay_ask"]):
        raise LocaleError(f"{src}: relay_ask must contain {{peer}} and {{q}}")

    takes = data.get("enroll", [])
    if not isinstance(takes, list) or any(
            not isinstance(t, dict) or not (t.get("id") and t.get("say"))
            for t in takes):
        raise LocaleError(f"{src}: every [[enroll]] needs an id and a say line")

    swaps = data.get("spoken_swaps", [])
    if not isinstance(swaps, list) or any(
            not (isinstance(pair, list) and len(pair) == 2) for pair in swaps):
        raise LocaleError(f"{src}: spoken_swaps must be a list of [symbol, word] pairs")

    lower = lambda seq: frozenset(w.lower() for w in seq)   # noqa: E731
    return Locale(
        lang=lang,
        wake_variants=tuple(w.lower() for w in
                            _as_tuple(data.get("wake_variants", []), "wake_variants", src)),
        wake_grammar_words=_as_tuple(data.get("wake_grammar_words", [text["name"].lower()]),
                                     "wake_grammar_words", src),
        greetings=tuple(w.lower() for w in
                        _as_tuple(data.get("greetings", []), "greetings", src)),
        letters=letters, script=script,
        acks=_as_tuple(data["acks"], "acks", src),
        stop_words=lower(_as_tuple(data["stop_words"], "stop_words", src)),
        reset_words=lower(_as_tuple(data["reset_words"], "reset_words", src)),
        word_re=word_re, strip_re=strip_re, script_re=script_re,
        spoken_swaps=tuple((str(a), str(b)) for a, b in swaps),
        cohort_voices=_as_tuple(data.get("cohort_voices", []), "cohort_voices", src),
        language_names=tuple(w.lower() for w in
                             _as_tuple(data["language_names"], "language_names", src)),
        badge=badge,
        enroll=tuple((str(t["id"]), str(t.get("how", "")), str(t["say"]))
                     for t in takes),
        fallbacks=fallbacks, source=src, **text)


def _env_overrides(loc: Locale) -> Locale:
    """A handful of fields people retune without editing a file.

    Kept to the ones that are a personal preference rather than a translation:
    the models, and the sentence said to a stranger.
    """
    from dataclasses import replace
    over = {}
    for field_name, var in (("asr_model", "JARVIS_ASR_MODEL"),
                            ("tts_backend", "JARVIS_BACKEND"),
                            ("tts_voice", "JARVIS_VOICE"),
                            ("edge_voice", "JARVIS_EDGE_VOICE"),
                            ("stranger_line", "JARVIS_STRANGER_LINE")):
        value = os.environ.get(var, "").strip()
        if value:
            over[field_name] = value
    return replace(loc, **over) if over else loc


def current(root: pathlib.Path | None = None) -> Locale:
    return _env_overrides(load(root=root))


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "list":
        print("\n".join(available()))
        raise SystemExit(0)

    # `lang.py match "давай по-русски"` -> ru. What the first-run question does
    # with the answer, exposed so it can be tried without talking to him.
    if len(sys.argv) > 2 and sys.argv[1] == "match":
        code = match_language(" ".join(sys.argv[2:]))
        print(code or "")
        raise SystemExit(0 if code else 1)

    # One field, unquoted, for the shell scripts: `lang.py get tts_voice`.
    if len(sys.argv) > 2 and sys.argv[1] == "get":
        try:
            value = getattr(current(), sys.argv[2])
        except (AttributeError, LocaleError) as e:
            print(e if isinstance(e, LocaleError) else
                  f"no such locale field: {sys.argv[2]}", file=sys.stderr)
            raise SystemExit(2)
        print("\n".join(value) if isinstance(value, (list, tuple)) else value)
        raise SystemExit(0)

    loc = current()
    print(f"locale {loc.lang} ({loc.source})")
    print(f"  wake word     {loc.name}   variants: {', '.join(loc.wake_variants)}")
    print(f"  stop words    {', '.join(sorted(loc.stop_words))}")
    print(f"  asr           {loc.asr_model}")
    print(f"  wake model    {loc.wake_model}")
    print(f"  voice         {loc.tts_backend}:{loc.tts_voice}"
          f"  (network: {loc.edge_voice}, system: {loc.system_voice})")
    print()
    print(loc.system_prompt(os.environ.get("JARVIS_OWNER", "")))
