"""Turning one command's output into one line Jarvis can say.

An action in `actions.toml` names a function here as `formatter = "formatters:name"`.
Without one the first non-empty line of stdout is spoken, which is right for
most scripts. A function belongs here only when a script prints something with
its own shape and you would rather not teach that shape to the script.

Signature: (stdout: str, argv: list[str]) -> str. Never raise - a formatter
that throws would swallow an answer the owner is waiting for.
"""

from __future__ import annotations


def _fields(out: str) -> dict[str, str]:
    return dict(line.split(": ", 1) for line in out.splitlines() if ": " in line)


def spotify(out: str, argv: list[str]) -> str:
    """`skills/spotify/spotify.sh` prints "поле: значение" lines.

    Volume ends up on the last line, a track on its own two. Anything else is
    a one-line status and goes out as it came.
    """
    if argv and argv[0] == "vol":
        return out.splitlines()[-1].replace("громкость:", "громкость")
    fields = _fields(out)
    if "трек" in fields:
        who = fields.get("исполнитель", "")
        return fields["трек"] + (f", {who}" if who else "")
    return out.splitlines()[0]
