# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["tomli; python_version < '3.11'"]
# ///
"""Rooms and actions - the two places you extend Jarvis without touching him.

A ROOM is somewhere a heard phrase can be sent: a Claude session living in a
Terminal window, and in principle anything else that takes a line of text.
Jarvis ships with two, "chief" and "chat", and neither is special - both are
rows in a TOML file.

An ACTION is handled here on the machine instead of being sent anywhere. It
runs a command and either speaks the output or lets Jarvis retell it.

Nothing in this file knows a Russian word or an agent name. The vocabulary is
in the TOML, so a different language is a different config, not a fork.

Load order, later file wins on a repeated id:
    config/rooms.toml, config/actions.toml   shipped defaults
    rooms.d/*.toml, actions.d/*.toml         your drop-ins, in file-name order
    $JARVIS_ROOMS, $JARVIS_ACTIONS           one more file or directory

A room or action with `enabled = false` is dropped after the merge, which is how
you switch off something the defaults gave you without editing their file.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass, field

try:
    import tomllib
except ModuleNotFoundError:  # python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

HERE = pathlib.Path(__file__).resolve().parent


class ConfigError(Exception):
    """A room or action is written wrong. Raised with the file name in it."""


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def _files(default: pathlib.Path, drop_dir: pathlib.Path,
           extra: str) -> list[pathlib.Path]:
    """Every TOML that takes part, in the order they are allowed to override."""
    out: list[pathlib.Path] = []
    if default.is_file():
        out.append(default)
    # `*.example.toml` is documentation that happens to be valid TOML. Loading
    # it would give everyone the sample room whether they wanted it or not.
    live = lambda d: sorted(p for p in d.glob("*.toml")
                            if not p.name.endswith(".example.toml"))
    if drop_dir.is_dir():
        out.extend(live(drop_dir))
    if extra:
        p = pathlib.Path(os.path.expanduser(extra))
        if p.is_dir():
            out.extend(live(p))
        elif p.is_file():
            out.append(p)
    return out


def _merge(files: list[pathlib.Path], table: str) -> tuple[list[dict], dict]:
    """Read one array-of-tables out of every file, later entries overriding.

    Returns the merged entries in first-seen order plus the merged [defaults].
    Keeping first-seen order matters: routing walks the list top to bottom, so
    a drop-in that overrides "chat" must not silently jump the queue.
    """
    order: list[str] = []
    by_id: dict[str, dict] = {}
    defaults: dict = {}
    for path in files:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise ConfigError(f"{path}: {e}") from e
        defaults.update(data.get("defaults", {}))
        for entry in data.get(table, []):
            rid = str(entry.get("id", "")).strip()
            if not rid:
                raise ConfigError(f"{path}: a [[{table}]] without an id")
            if rid not in by_id:
                order.append(rid)
                by_id[rid] = {}
            by_id[rid].update(entry)
            by_id[rid]["_from"] = str(path)
    return [by_id[r] for r in order if by_id[r].get("enabled", True)], defaults


def _compile(patterns, where: str) -> tuple[re.Pattern, ...]:
    out = []
    for p in patterns or ():
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error as e:
            raise ConfigError(f"{where}: bad regex {p!r}: {e}") from e
    return tuple(out)


# --------------------------------------------------------------------------
# rooms
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Room:
    """One destination. `Orchestrator` in the daemon does the actual typing."""

    id: str
    label: str                      # what Jarvis calls it out loud
    session: str = ""               # Claude session name, the window is found by it
    work_dir: str = ""              # fallback: find the window by its folder
    ask: tuple[str, ...] = ()       # "ask the chief ..." - wait for the answer
    tell: tuple[str, ...] = ()      # "tell the chief ..." - do not wait
    bare: tuple[str, ...] = ()      # "chief, ..." - only at the very start
    bare_kind: str = "tell"         # what a bare name means: ask or tell
    mention: tuple[re.Pattern, ...] = ()   # route here on this word anywhere
    launch: str = ""                # script that raises the room
    role_file: str = ""             # extra system prompt for the session in it
    tint: str = ""                  # window background colour, to tell rooms apart
    remote_control: str = ""        # `claude --remote-control <name>`, "" for none
    hint: str = ""                  # the sentence Jarvis is told about this room
    relay_via: str = ""             # room that can reach it by cross-session message
    fallback: str = ""              # room to ask instead when this one is down
    ack_tell: str = ""
    ack_ask: str = ""
    ack_relay: str = ""
    ack_fallback: str = ""
    ack_missing: str = ""
    examples: tuple[tuple[str, str], ...] = ()   # (phrase, expected route) for --selfcheck
    source: str = ""

    def env_session(self) -> str:
        """The session name, with $JARVIS_ROOM_<ID>_SESSION winning over the file."""
        return os.environ.get(self._var("SESSION"), self.session).strip()

    def env_dir(self) -> str:
        return os.path.expanduser(
            os.environ.get(self._var("DIR"), self.work_dir).strip())

    def _var(self, suffix: str) -> str:
        return "JARVIS_ROOM_" + re.sub(r"\W", "_", self.id).upper() + "_" + suffix


def _room(entry: dict) -> Room:
    where = f"{entry.get('_from', '?')} room {entry['id']!r}"
    ex = tuple((str(e["phrase"]), str(e.get("route", "")))
               for e in entry.get("example", []))
    return Room(
        id=str(entry["id"]),
        label=str(entry.get("label", entry["id"])),
        session=str(entry.get("session", "")),
        work_dir=str(entry.get("work_dir", "")),
        ask=tuple(entry.get("ask", ())),
        tell=tuple(entry.get("tell", ())),
        bare=tuple(entry.get("bare", ())),
        bare_kind=str(entry.get("bare_kind", "tell")),
        mention=_compile(entry.get("mention_patterns"), where),
        launch=str(entry.get("launch", "")),
        role_file=str(entry.get("role_file", "")),
        tint=str(entry.get("tint", "")),
        remote_control=str(entry.get("remote_control", "")),
        hint=str(entry.get("hint", "")),
        relay_via=str(entry.get("relay_via", "")),
        fallback=str(entry.get("fallback", "")),
        ack_tell=str(entry.get("ack_tell", "")),
        ack_ask=str(entry.get("ack_ask", "")),
        ack_relay=str(entry.get("ack_relay", "")),
        ack_fallback=str(entry.get("ack_fallback", "")),
        ack_missing=str(entry.get("ack_missing", "")),
        examples=ex,
        source=str(entry.get("_from", "")),
    )


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ArgRule:
    """Sub-pattern of an action: which arguments this wording means."""

    patterns: tuple[re.Pattern, ...]
    args: tuple[str, ...]


@dataclass(frozen=True)
class WordRule:
    """Pass words out of the phrase itself as arguments.

    Used by an action whose script needs to know what the question was about -
    which agent, which project - without the script learning the language.
    Stopwords are the words that say nothing about the subject.
    """

    limit: int = 3
    min_len: int = 3
    stopwords: frozenset[str] = frozenset()

    def pick(self, phrase: str) -> list[str]:
        words = [w for w in re.findall(r"[^\W\d_]{%d,}" % self.min_len,
                                       phrase.lower())
                 if w not in self.stopwords]
        return words[:self.limit]


@dataclass(frozen=True)
class Action:
    """Something answered on this machine, without reaching any room."""

    id: str
    patterns: tuple[re.Pattern, ...]
    run: str = ""                   # shell command; {name} takes named groups
    args: tuple[ArgRule, ...] = ()  # optional wording -> argv rules
    default_args: tuple[str, ...] = ()
    words: WordRule | None = None   # append content words from the phrase
    speak: str = "stdout"           # stdout | retell | none
    prompt: str = ""                # speak="retell": template with {q} and {facts}
    formatter: str = ""             # module:function turning stdout into one line
    hint: str = ""                  # the sentence Jarvis is told about this action
    fail_say: str = ""              # said when the command could not run
    # Said when the command ran but printed nothing. Empty means "use the
    # locale's line" - a default written here would be in one language for
    # everybody, and this string is spoken out loud.
    ok_say: str = ""
    timeout_s: float = 20.0
    examples: tuple[tuple[str, str], ...] = ()
    source: str = ""

    def match(self, phrase: str) -> re.Match | None:
        for rx in self.patterns:
            m = rx.search(phrase)
            if m:
                return m
        return None

    def argv(self, phrase: str) -> list[str]:
        """Arguments for this phrase: the first matching rule, else the default.

        Words picked out of the phrase come after the fixed ones, so a script
        reads its switches first and its subject last.
        """
        out = list(self.default_args)
        for rule in self.args:
            if any(rx.search(phrase) for rx in rule.patterns):
                out = list(rule.args)
                break
        if self.words:
            out += self.words.pick(phrase)
        return out


def _action(entry: dict) -> Action:
    where = f"{entry.get('_from', '?')} action {entry['id']!r}"
    speak = str(entry.get("speak", "stdout"))
    if speak not in ("stdout", "retell", "none"):
        raise ConfigError(f"{where}: speak must be stdout, retell or none")
    if speak == "retell" and not entry.get("prompt"):
        raise ConfigError(f"{where}: speak = \"retell\" needs a prompt")
    args = tuple(ArgRule(_compile(a.get("patterns"), where),
                         tuple(str(x) for x in a.get("args", ())))
                 for a in entry.get("arg", []))
    ex = tuple((str(e["phrase"]), str(e.get("route", "")))
               for e in entry.get("example", []))
    w = entry.get("words")
    words = WordRule(limit=int(w.get("limit", 3)),
                     min_len=int(w.get("min_len", 3)),
                     stopwords=frozenset(w.get("stopwords", ()))) if w else None
    return Action(
        id=str(entry["id"]),
        patterns=_compile(entry.get("patterns"), where),
        run=str(entry.get("run", "")),
        args=args,
        default_args=tuple(str(x) for x in entry.get("default_args", ())),
        words=words,
        speak=speak,
        prompt=str(entry.get("prompt", "")),
        formatter=str(entry.get("formatter", "")),
        hint=str(entry.get("hint", "")),
        fail_say=str(entry.get("fail_say", "")),
        ok_say=str(entry.get("ok_say", "")),
        timeout_s=float(entry.get("timeout_s", 20)),
        examples=ex,
        source=str(entry.get("_from", "")),
    )


class _Blanks(dict):
    """A mapping that leaves an unknown placeholder as it was written."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def fill(template: str, **names: str) -> str:
    """Substitute {placeholders} in a line from the config, forgiving a typo.

    An unknown or misspelt name comes out unchanged instead of raising. These
    strings are things Jarvis says out loud, and a KeyError here would kill the
    answer the owner is waiting for over one bad brace.
    """
    try:
        return template.format_map(_Blanks(**names))
    except (IndexError, ValueError):
        return template


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------

@dataclass
class Registry:
    rooms: list[Room] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    default_room: str = ""

    def room(self, rid: str) -> Room | None:
        return next((r for r in self.rooms if r.id == rid), None)

    def route(self, phrase: str) -> str:
        """Automatic destination for a phrase: an action id, a room id, or "".

        Actions come first, then rooms that named a mention word. Explicit
        addressing ("tell the chief ...") is resolved by the caller before this
        and always outranks it - guessing must never beat being told.
        """
        for action in self.actions:
            if action.match(phrase):
                return action.id
        for room in self.rooms:
            if any(rx.search(phrase) for rx in room.mention):
                return room.id
        return ""

    def hints(self) -> str:
        """What Jarvis is told about his rooms and actions, in config order.

        The system prompt is not the place to name them: rename a room and the
        prompt should follow, which it only does if the sentence lives next to
        the room it is about.
        """
        return " ".join(h.strip() for h in
                        [*(r.hint for r in self.rooms),
                         *(a.hint for a in self.actions)] if h.strip())

    def action(self, aid: str) -> Action | None:
        return next((a for a in self.actions if a.id == aid), None)

    def address(self, command: str) -> tuple[str, str, str, str]:
        """Parse explicit addressing: ("ask"|"tell"|"", room_id, task, phrase).

        `phrase` is the wording that matched, so the caller can tell an order
        ("tell the chief X") from a bare name ("chief, X") - only the order
        outranks the automatic routes.

        Multi-word forms are looked for anywhere in the sentence; a bare name
        counts only at the very start and as a whole word, or "leader of
        opinion" would be read as an order to the room called "lead".
        """
        low = command.lower()
        for room in self.rooms:
            for kind, phrases in (("ask", room.ask), ("tell", room.tell),
                                  (room.bare_kind, room.bare)):
                for phrase in phrases:
                    if " " in phrase:
                        m = re.search(r"\b" + re.escape(phrase) + r"\b", low)
                    else:
                        # a hyphen is not a word break here, or "chief-of-staff"
                        # would be read as an order to the chief
                        m = re.match(r"\s*" + re.escape(phrase) + r"(?=[\s,.:;!?]|$)",
                                     low)
                    if not m:
                        continue
                    task = command[m.end():].lstrip(" ,:;-").strip()
                    if task:
                        return kind or "tell", room.id, task, phrase
        return "", "", "", ""


def load(root: pathlib.Path | None = None) -> Registry:
    """Read every TOML and build the registry. Raises ConfigError on bad input."""
    root = root or HERE
    rooms_raw, room_defaults = _merge(
        _files(root / "config" / "rooms.toml", root / "rooms.d",
               os.environ.get("JARVIS_ROOMS", "")), "room")
    actions_raw, _ = _merge(
        _files(root / "config" / "actions.toml", root / "actions.d",
               os.environ.get("JARVIS_ACTIONS", "")), "action")
    reg = Registry(rooms=[_room(e) for e in rooms_raw],
                   actions=[_action(e) for e in actions_raw],
                   default_room=str(room_defaults.get("room", "")))
    seen = {r.id for r in reg.rooms}
    for room in reg.rooms:
        for field_name, target in (("relay_via", room.relay_via),
                                   ("fallback", room.fallback)):
            if target and target not in seen:
                raise ConfigError(
                    f"{room.source}: room {room.id!r} has {field_name} = "
                    f"{target!r}, which is not an enabled room")
        if room.bare_kind not in ("ask", "tell"):
            raise ConfigError(
                f"{room.source}: room {room.id!r} has bare_kind = "
                f"{room.bare_kind!r}, expected \"ask\" or \"tell\"")
    if reg.default_room and reg.default_room not in seen:
        raise ConfigError(f"defaults.room = {reg.default_room!r} is not a room")
    return reg


# --------------------------------------------------------------------------
# running an action
# --------------------------------------------------------------------------

def _stdlib_only(path: str) -> bool:
    """True when the script has no PEP 723 dependency header."""
    try:
        with open(path, encoding="utf-8") as fh:
            return "# /// script" not in fh.read(400)
    except OSError:
        return False


def _resolve(path: str, root: pathlib.Path) -> str:
    """A relative `run` is relative to the repo, not to the current directory."""
    p = os.path.expanduser(path)
    return p if os.path.isabs(p) else str(root / p)


def run_action(action: Action, phrase: str, root: pathlib.Path | None = None,
               log=lambda _m: None) -> str | None:
    """Run one action. Returns its output, or None if it could not run.

    Output is raw here. Whether it is spoken as it stands, retold by Jarvis or
    swallowed is `action.speak`, and the caller decides - this function has no
    voice of its own.
    """
    root = root or HERE
    if not action.run:
        return ""
    m = action.match(phrase)
    named = {k: v for k, v in (m.groupdict() if m else {}).items() if v}
    try:
        cmd = action.run.format(**named)
    except (KeyError, IndexError) as e:
        log(f"action {action.id}: run has {e}, which the pattern does not capture")
        return None
    parts = cmd.split()
    if not parts:
        return None
    parts[0] = _resolve(parts[0], root)
    argv = parts + action.argv(phrase)
    if parts[0].endswith(".sh"):
        argv = ["bash", *argv]
    elif parts[0].endswith(".py"):
        # `uv run` only for a script that declares its own dependencies; a
        # stdlib script starts far faster on the interpreter we are already in,
        # and the owner is waiting for the answer out loud.
        argv = ([sys.executable, *argv] if _stdlib_only(parts[0])
                else ["uv", "run", "--quiet", *argv])
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=action.timeout_s)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"action {action.id} failed: {e}")
        return None
    if r.returncode != 0:
        log(f"action {action.id} exited {r.returncode}: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def format_output(action: Action, out: str, argv: list[str],
                  root: pathlib.Path | None = None) -> str:
    """Turn an action's stdout into one speakable line.

    Default: the first non-empty line. A `formatter = "module:function"` in the
    TOML replaces that - which is how a piece with its own output shape stays
    readable without teaching Jarvis about it.
    """
    if not out:
        return ""
    if not action.formatter:
        return next((l.strip() for l in out.splitlines() if l.strip()), "")
    mod_name, _, func_name = action.formatter.partition(":")
    import importlib
    root = root or HERE
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        func = getattr(importlib.import_module(mod_name), func_name)
    except (ImportError, AttributeError):
        return next((l.strip() for l in out.splitlines() if l.strip()), "")
    return str(func(out, argv))


if __name__ == "__main__":
    try:
        reg = load()
    except ConfigError as e:
        print(f"config error: {e}")
        sys.exit(1)
    # `plugins.py get <room> <field>` - how room.sh reads the config without a
    # TOML parser in bash. Prints nothing and exits 1 when there is no such room.
    if len(sys.argv) == 4 and sys.argv[1] == "get":
        target = reg.room(sys.argv[2])
        if target is None:
            sys.exit(1)
        if sys.argv[3] == "session":
            print(target.env_session())
        elif sys.argv[3] == "work_dir":
            print(target.env_dir())
        else:
            print(getattr(target, sys.argv[3], ""))
        sys.exit(0)
    if len(sys.argv) == 2 and sys.argv[1] == "rooms":
        print(" ".join(r.id for r in reg.rooms))
        sys.exit(0)
    print(f"rooms ({len(reg.rooms)}), default {reg.default_room or '-'}:")
    for r in reg.rooms:
        print(f"  {r.id:<10} label={r.label!r} session={r.env_session()!r} "
              f"mention={[p.pattern for p in r.mention]} "
              f"relay_via={r.relay_via or '-'}")
    print(f"actions ({len(reg.actions)}):")
    for a in reg.actions:
        print(f"  {a.id:<14} speak={a.speak:<7} run={a.run or '-'}")
    cases = [(p, want) for src in (*reg.rooms, *reg.actions) for p, want in src.examples]
    if cases:
        bad = [(p, want, reg.route(p.lower())) for p, want in cases
               if reg.route(p.lower()) != want]
        print(f"routing: {len(cases) - len(bad)}/{len(cases)} ok"
              + ("" if not bad else f", misses: {bad}"))
