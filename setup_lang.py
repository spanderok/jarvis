# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["tomli; python_version < '3.11'"]
# ///
"""Choosing the language he speaks, from a sentence a person said or typed.

The daemon asks out loud on the first wake and uses this to act on the answer.
An agent session that took the microphone with /assist asks in its own chat
instead - the owner is looking at a text window there, and a question only
spoken into an empty room is a question nobody answers. Both end up here, so
the choice is saved and the models are fetched the same way either way.

  uv run setup_lang.py                 which language is set, if any
  uv run setup_lang.py "по-русски"     match it, save it, fetch what it needs
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lang as lang_mod  # noqa: E402

JARVIS_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.expanduser(
    os.environ.get("JARVIS_ENV") or os.path.join(JARVIS_DIR, "jarvis.env"))


def chosen() -> str:
    """The language somebody has already settled on, or "" on a first run."""
    if os.environ.get("JARVIS_LANG", "").strip():
        return os.environ["JARVIS_LANG"].strip()
    try:
        text = pathlib.Path(ENV_FILE).read_text(encoding="utf-8")
    except OSError:
        return ""
    value = ""
    for line in text.splitlines():                # the last one wins, as in sh
        line = line.strip()
        if line.startswith("JARVIS_LANG="):
            value = line.partition("=")[2].strip().strip('"').strip("'")
    return value


def save(code: str, note: str = "Chosen on the first run.") -> None:
    """Write the choice into jarvis.env, creating the file if it is not there.

    An existing line is replaced rather than a second one appended. The shell
    would take the last of two and behave correctly, but a file that says both
    ru and en is a file nobody can read, and changing the language a second
    time is exactly when somebody opens it.
    """
    path = pathlib.Path(ENV_FILE)
    if not path.exists():
        example = pathlib.Path(JARVIS_DIR, "jarvis.env.example")
        try:
            path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    kept, replaced = [], False
    for line in lines:
        if line.strip().startswith("JARVIS_LANG="):
            if not replaced:
                kept.append(f"JARVIS_LANG={code}")
                replaced = True
            continue                              # drop any further duplicates
        kept.append(line)
    if not replaced:
        kept += ["", f"# {note}", f"JARVIS_LANG={code}"]
    path.write_text("\n".join(kept).rstrip("\n") + "\n", encoding="utf-8")


def fetch(code: str, log=None, timeout: float = 1800) -> None:
    """Whatever this language needs and the disk does not have.

    install.sh skips what is already there, so this is a second when the models
    happen to be the ones already downloaded, and a few minutes when they are
    not. Only the lines about what changed on disk are worth passing on - the
    installer also prints its whole "what to do next" block.
    """
    interesting = ("downloading", "already there", "building", "words written",
                   "voice runtime", "warning", "failed", "not found")
    try:
        r = subprocess.run(["bash", os.path.join(JARVIS_DIR, "install.sh"), "models"],
                           env=dict(os.environ, JARVIS_LANG=code),
                           capture_output=True, text=True, timeout=timeout)
        for line in ((r.stdout or "") + "\n" + (r.stderr or "")).splitlines():
            line = line.strip()
            if line and any(word in line.lower() for word in interesting):
                (log or print)(line[:120])
    except (OSError, subprocess.TimeoutExpired) as e:
        (log or print)(f"fetching the {code} models failed ({e})")


if __name__ == "__main__":
    answer = " ".join(sys.argv[1:]).strip()
    if not answer:
        have = chosen()
        print(f"language: {have}" if have else
              "language: not chosen yet - he will ask on the first wake")
        raise SystemExit(0 if have else 1)

    code = lang_mod.match_language(answer)
    if not code:
        names = []
        for one in lang_mod.available():
            try:
                names.append(lang_mod.load(one).english_name())
            except lang_mod.LocaleError:
                continue
        print(f"no language in {answer!r}. Installed: {', '.join(names)}")
        raise SystemExit(2)

    loc = lang_mod.load(code)
    save(code, "Chosen in the chat of the session holding the microphone.")
    print(f"language set to {code} ({loc.english_name()}), wake word {loc.name!r}")
    fetch(code)
    print("saved in jarvis.env. Restart the listener or the daemon to speak it.")
