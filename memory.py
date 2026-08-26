# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["tomli; python_version < '3.11'"]
# ///
"""Long-term memory: a hook for a vector store, and nothing more specific.

Jarvis' own session forgets everything ten minutes after the last question. A
vector database is how people give him something older than that - past
decisions, meeting notes, a wiki, a whole Obsidian vault.

This module does not talk to any database. It runs a command you name and puts
what the command printed in front of the question:

    you say         "what did we decide about the deploys"
    recall runs     memory.d/recall.sh "what did we decide about the deploys"
    it prints       Decided 12 Aug: deploys go out Tuesdays, one person on call.
    Jarvis gets     Some notes that may be relevant:
                    Decided 12 Aug: deploys go out Tuesdays...

                    The question: what did we decide about the deploys

A command and not a Python API on purpose. Chroma, Qdrant, LanceDB, sqlite-vec,
a grep over a folder of markdown - all of them can be a shell script, and none
of them becomes a dependency of this repository. It is the same reasoning as
actions: a fixed route cannot be talked into anything, and a store of personal
notes is the last thing to hand a model free rein over.

Turned off in config/memory.toml until you point it somewhere.

    python3 memory.py "a question"     what would be recalled, and how long it took
"""

from __future__ import annotations

import os
import pathlib
import shlex
import subprocess
import sys
from dataclasses import dataclass

try:
    import tomllib
except ModuleNotFoundError:                     # 3.10
    import tomli as tomllib                     # type: ignore


class MemoryError_(Exception):
    """A memory config that cannot be used."""


@dataclass(frozen=True)
class Memory:
    """One store, described by the two commands that read and write it."""

    enabled: bool = False

    # Gets the question as its one argument, prints context on stdout.
    # Printing nothing is a normal answer: it means there was nothing relevant.
    recall: str = ""

    # Gets the question and the answer as two arguments. Its output is ignored.
    # Runs detached, after Jarvis has already spoken - it must never be in the
    # path between a question and the first sound.
    remember: str = ""

    # Recall sits between the question and the first word of the answer, so this
    # is a hard budget, not a suggestion. Three seconds of silence after a
    # question already feels like he did not hear it.
    timeout_s: float = 3.0

    # What comes back is pasted into a prompt. A store having a bad day can
    # return a whole document, and a whole document costs seconds to read.
    max_chars: int = 1500

    # Wrapped around the recalled text. {facts} and {q} are filled in.
    template: str = ""

    source: str = ""

    def available(self) -> bool:
        return bool(self.enabled and self.recall)


DEFAULT_TEMPLATE = """Some notes that may be relevant to this question:
{facts}

The question: {q}"""


def load(root: pathlib.Path | None = None) -> Memory:
    """Read config/memory.toml, plus a JARVIS_MEMORY override."""
    root = root or pathlib.Path(__file__).resolve().parent
    # Path("") is Path(".") and Path(".") is truthy, so the empty case has to be
    # tested on the string, not on the Path.
    override = os.environ.get("JARVIS_MEMORY", "").strip()
    path = pathlib.Path(override) if override else root / "config" / "memory.toml"
    if not path.is_file():
        if override:
            raise MemoryError_(f"JARVIS_MEMORY points at {path}, which is not a file")
        return Memory(source=str(path))
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8")).get("memory", {})
    except tomllib.TOMLDecodeError as e:
        raise MemoryError_(f"{path.name}: {e}") from None
    if not isinstance(data, dict):
        raise MemoryError_(f"{path.name}: [memory] must be a table")

    mem = Memory(
        enabled=bool(data.get("enabled", False)),
        recall=str(data.get("recall", "")).strip(),
        remember=str(data.get("remember", "")).strip(),
        timeout_s=float(data.get("timeout_s", 3.0)),
        max_chars=int(data.get("max_chars", 1500)),
        template=str(data.get("template", "")).strip() or DEFAULT_TEMPLATE,
        source=path.name,
    )
    if mem.enabled and not mem.recall:
        raise MemoryError_(f"{path.name}: memory is enabled but recall is empty")
    for name, template in (("template", mem.template),):
        for token in ("{facts}", "{q}"):
            if token not in template:
                raise MemoryError_(f"{path.name}: {name} must contain {token}")
    if mem.timeout_s <= 0:
        raise MemoryError_(f"{path.name}: timeout_s must be above zero")
    return mem


def _argv(command: str, root: pathlib.Path, *args: str) -> list[str]:
    """A command line from the config, with the script resolved against the repo."""
    parts = shlex.split(command)
    if not parts:
        return []
    first = pathlib.Path(parts[0])
    if not first.is_absolute():
        local = root / first
        if local.exists():
            parts[0] = str(local)
    if parts[0].endswith(".sh"):
        parts = ["bash", *parts]
    elif parts[0].endswith(".py"):
        parts = ["uv", "run", "--quiet", *parts]
    return [*parts, *args]


def recall(mem: Memory, question: str, root: pathlib.Path,
           log=lambda _m: None) -> str:
    """Context for this question, or "" - never an exception.

    Everything here fails open. A store that is down, slow or shouting on stderr
    must cost the owner an ordinary answer, not an error read out loud.
    """
    if not mem.available():
        return ""
    argv = _argv(mem.recall, root, question)
    if not argv:
        return ""
    try:
        out = subprocess.run(argv, capture_output=True, text=True,
                             timeout=mem.timeout_s, cwd=root)
    except subprocess.TimeoutExpired:
        log(f"memory: recall gave up after {mem.timeout_s:.1f}s")
        return ""
    except OSError as e:
        log(f"memory: recall would not run ({e})")
        return ""
    if out.returncode != 0:
        log(f"memory: recall exited {out.returncode}: "
            f"{(out.stderr or '').strip()[:200]}")
        return ""
    text = (out.stdout or "").strip()
    if len(text) > mem.max_chars:
        text = text[:mem.max_chars].rsplit(" ", 1)[0] + " ..."
        log(f"memory: recall trimmed to {mem.max_chars} chars")
    return text


def wrap(mem: Memory, question: str, facts: str) -> str:
    """The question with its context in front, or the question unchanged."""
    if not facts:
        return question
    return mem.template.replace("{facts}", facts).replace("{q}", question)


def remember(mem: Memory, question: str, answer: str, root: pathlib.Path,
             log=lambda _m: None) -> None:
    """Hand the exchange to the store and do not wait for it.

    Detached on purpose: this runs after he has spoken, and nothing about the
    next question should depend on a write finishing.
    """
    if not (mem.enabled and mem.remember and question and answer):
        return
    argv = _argv(mem.remember, root, question, answer)
    if not argv:
        return
    try:
        subprocess.Popen(argv, cwd=root, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        log(f"memory: remember would not run ({e})")


if __name__ == "__main__":
    import time

    root = pathlib.Path(__file__).resolve().parent
    mem = load(root)
    print(f"memory ({mem.source}): "
          f"{'on' if mem.available() else 'off'}"
          f"  recall={mem.recall or '-'}  remember={mem.remember or '-'}"
          f"  timeout={mem.timeout_s}s  max={mem.max_chars} chars")
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        raise SystemExit(0)
    t0 = time.monotonic()
    facts = recall(mem, question, root, lambda m: print(f"  {m}"))
    took = time.monotonic() - t0
    print(f"\nrecall took {took:.2f}s, {len(facts)} chars"
          + ("" if facts else " (nothing came back)"))
    if facts:
        print("\n--- what Jarvis would be asked ---")
        print(wrap(mem, question, facts))
