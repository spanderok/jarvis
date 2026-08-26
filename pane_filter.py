"""Turn a Claude Code terminal pane into something worth speaking out loud.

The pane holds tool calls, their sub-results, status hints and links. Reading all
of that aloud is what made Jarvis narrate log noise and full URLs.
"""
import re

CHROME = re.compile(
    r"^\s*(?:[─━═╭╮╯╰│┃┌┐└┘├┤┬┴┼╔╗╚╝║╞╡╪⎿↳]+"       # box drawing, tool sub-lines
    r"|[●⏺*✱✻✽·⧉]?\s*[A-Z][A-Za-z ]{1,20}\("         # Web Search(...), Bash(...)
    r"|⏵⏵|❯|›|\?|\[|✔|✗)"
    r"|Did \d+ search|Allowed by|Baked for|Saut|Cooking|Running|Waiting"
    r"|esc to interrupt|auto mode|shift\+tab|shift\+enter|for agents|tokens"
    r"|Context left|⧉|Transcript saving|Read \d+ lines|Listed \d+"
    r"|in clipboard|ctrl\+v|to paste|to interrupt|Press |bypass permissions"
    r"|\+\d+ lines|ctrl\+o to expand"
    # the thinking line is a random gerund every time (Cogitated, Sautéed,
    # Percolated...), so match the glyph and the "for 3s" tail instead
    r"|^\s*[✻✽✱*·]|\bfor \d+s\b")
URL = re.compile(r"\(?\bhttps?://\S+\)?")
# session ids and hashes: unlistenable as digits, and never the point of the answer
IDS = re.compile(r"[\[(]\s*[0-9a-f]{4,}\s*[\])]")
# the shell banner means we typed into a plain prompt, not into Claude
SHELL_BANNER = re.compile(r"Last login:|unset CLAUDE_CODE_CHILD_SESSION|@MacBook-Pro")
BULLET = re.compile(r"^\s*[-–—•*]\s*")
# A line with no letters at all is decoration, not speech. The chef answered with
# a table on 20.08 and Jarvis read its borders out loud: "├─────────┼────────┼".
LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)
# "⏺ Bash(...)" is a tool call, "⏺ Петров написал..." is the answer. Only the
# head of a block carries the bullet, so the last non-call bullet starts the
# answer - everything above it is machinery. Without this cut the wrapped tail of
# a shell command ("w) USERID=$(security find-generic-password ...") reached the
# speaker: the line filter drops the call itself but not its continuation lines.
BLOCK_HEAD = re.compile(r"^\s*⏺\s+")
TOOL_HEAD = re.compile(r"^\s*⏺\s+[A-Za-z_][\w .-]{0,30}\(")
# Claude Code's own cross-session notices look like assistant speech but are
# English machinery: "рокет is idle — finished a turn at 09:15 · «…»"
NOTICE = re.compile(r"\bis idle\b|finished a turn|Cross-session|cross-session")
# The prompt glyph never appears in speech, but it does in the line the TUI draws
# for an outgoing cross-session message: "@ рокет❯"
PROMPTY = re.compile(r"❯")


def answer_blocks(block: str) -> str:
    """Everything the agent said, tool calls and notices left behind.

    Taking only the LAST block used to lose the point: relaying a question
    through the chef gives four blocks, and the useful one is in the middle
    ("Рокет ответил: …"), while the last is housekeeping ("Сессия рокет
    освободилась"). Checked on the pane of 20.08, 09:15.
    """
    out, keep = [], False
    for line in block.replace("\r", "\n").split("\n"):
        if BLOCK_HEAD.match(line):
            keep = not TOOL_HEAD.match(line) and not NOTICE.search(line)
            if keep:
                out.append(line)
            continue
        if keep and line.strip() and not line.lstrip().startswith(("⎿", "⏵", "─")):
            out.append(line)
    return "\n".join(out) if out else block


def speakable(block: str) -> str:
    out = []
    for raw in answer_blocks(block).replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip() or CHROME.search(line):
            continue
        line = URL.sub("", line)
        line = IDS.sub("", line)
        line = line.lstrip("⏺●•⧉ ").strip()
        line = BULLET.sub("", line)
        if not line or line in {"(", ")", "-"} or not LETTERS.search(line):
            continue
        if PROMPTY.search(line):
            continue
        out.append(line)
    text = re.sub(r"\s{2,}", " ", " ".join(out))
    text = text.replace("°C", " градусов").replace("..", " до ")
    return text.strip()


def looks_like_shell(text: str) -> bool:
    """True when the text is a shell prompt, not an answer from Claude."""
    return bool(SHELL_BANNER.search(text))
