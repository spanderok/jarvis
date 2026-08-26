# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["pytest", "tomli; python_version < '3.11'"]
# ///
"""Tests for the long-term memory hook.

Run: uv run --with pytest pytest tests/ -q

The rule this file exists to hold: recall never breaks an answer. A store that
is down, slow, empty or shouting on stderr must cost the owner an ordinary
answer with no context in it - never an error read out loud, and never silence.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import memory  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent

BODY = '''
[memory]
enabled = true
recall = "{recall}"
timeout_s = {timeout}
max_chars = {max_chars}
template = """Notes:
{{facts}}

The question: {{q}}"""
'''


def build(tmp_path: pathlib.Path, script: str, *, timeout: float = 3.0,
          max_chars: int = 1500, monkeypatch=None) -> memory.Memory:
    """A memory config pointing at a throwaway script."""
    (tmp_path / "config").mkdir(exist_ok=True)
    tool = tmp_path / "tool.sh"
    tool.write_text(script, encoding="utf-8")
    (tmp_path / "config" / "memory.toml").write_text(
        BODY.format(recall="tool.sh", timeout=timeout, max_chars=max_chars),
        encoding="utf-8")
    if monkeypatch is not None:
        monkeypatch.delenv("JARVIS_MEMORY", raising=False)
    return memory.load(tmp_path)


# --------------------------------------------------------------------------
# the config that ships
# --------------------------------------------------------------------------

def test_the_shipped_config_is_off():
    """Nobody's notes get read because they cloned a repository."""
    assert not memory.load(REPO).available()


def test_the_shipped_config_still_parses():
    """Off is not an excuse for a file that would not load once switched on."""
    assert memory.load(REPO).recall


def test_a_repo_with_no_memory_config_is_simply_off(tmp_path):
    assert not memory.load(tmp_path).available()


# --------------------------------------------------------------------------
# recall, on a good day
# --------------------------------------------------------------------------

def test_the_question_reaches_the_command(tmp_path, monkeypatch):
    mem = build(tmp_path, 'echo "asked about: $1"\n', monkeypatch=monkeypatch)
    assert memory.recall(mem, "the deploys", tmp_path) == "asked about: the deploys"


def test_what_came_back_goes_in_front_of_the_question(tmp_path, monkeypatch):
    mem = build(tmp_path, 'echo "Tuesdays."\n', monkeypatch=monkeypatch)
    asked = memory.wrap(mem, "when do we deploy",
                        memory.recall(mem, "when do we deploy", tmp_path))
    assert "Tuesdays." in asked
    assert asked.rstrip().endswith("when do we deploy")


def test_nothing_found_leaves_the_question_untouched(tmp_path, monkeypatch):
    mem = build(tmp_path, "exit 0\n", monkeypatch=monkeypatch)
    facts = memory.recall(mem, "anything", tmp_path)
    assert facts == ""
    assert memory.wrap(mem, "anything", facts) == "anything"


# --------------------------------------------------------------------------
# recall, on a bad day - every one of these must cost only the context
# --------------------------------------------------------------------------

def test_a_slow_store_is_given_up_on(tmp_path, monkeypatch):
    mem = build(tmp_path, "sleep 5\necho late\n", timeout=0.3,
                monkeypatch=monkeypatch)
    said = []
    assert memory.recall(mem, "q", tmp_path, said.append) == ""
    assert any("gave up" in line for line in said)


def test_a_failing_store_says_so_in_the_log_and_nowhere_else(tmp_path, monkeypatch):
    mem = build(tmp_path, 'echo "boom" >&2\nexit 3\n', monkeypatch=monkeypatch)
    said = []
    assert memory.recall(mem, "q", tmp_path, said.append) == ""
    assert any("exited 3" in line for line in said)


def test_a_missing_command_is_not_an_exception(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "memory.toml").write_text(
        BODY.format(recall="no-such-tool.sh", timeout=1, max_chars=100),
        encoding="utf-8")
    monkeypatch.delenv("JARVIS_MEMORY", raising=False)
    mem = memory.load(tmp_path)
    assert memory.recall(mem, "q", tmp_path) == ""


def test_a_store_having_a_bad_day_cannot_return_a_whole_document(tmp_path, monkeypatch):
    """A long answer is seconds of reading before he says his own first word."""
    mem = build(tmp_path, 'python3 -c "print(\'word \' * 4000)"\n',
                max_chars=200, monkeypatch=monkeypatch)
    said = []
    facts = memory.recall(mem, "q", tmp_path, said.append)
    assert len(facts) <= 204        # the trim, plus the " ..." it ends with
    assert facts.endswith("...")
    assert any("trimmed" in line for line in said)


def test_recall_is_skipped_entirely_while_memory_is_off(tmp_path, monkeypatch):
    mem = build(tmp_path, "echo something\n", monkeypatch=monkeypatch)
    off = memory.Memory(enabled=False, recall=mem.recall)
    assert memory.recall(off, "q", tmp_path) == ""


# --------------------------------------------------------------------------
# a broken config stops him at start
# --------------------------------------------------------------------------

def test_enabled_with_nothing_to_call_is_refused(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "memory.toml").write_text(
        '[memory]\nenabled = true\nrecall = ""\n', encoding="utf-8")
    with pytest.raises(memory.MemoryError_, match="recall"):
        memory.load(tmp_path)


@pytest.mark.parametrize("template", [
    "no placeholders at all",
    "only the facts: {facts}",
    "only the question: {q}",
])
def test_a_template_that_lost_a_placeholder_is_refused(tmp_path, template):
    """Losing {q} loses the question - he would answer the notes instead."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "memory.toml").write_text(
        f'[memory]\nenabled = true\nrecall = "x.sh"\ntemplate = "{template}"\n',
        encoding="utf-8")
    with pytest.raises(memory.MemoryError_):
        memory.load(tmp_path)


def test_a_timeout_of_zero_is_refused(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "memory.toml").write_text(
        '[memory]\nenabled = true\nrecall = "x.sh"\ntimeout_s = 0\n',
        encoding="utf-8")
    with pytest.raises(memory.MemoryError_, match="timeout"):
        memory.load(tmp_path)


def test_an_env_override_pointing_nowhere_is_loud(tmp_path, monkeypatch):
    """Silently falling back to "off" would look exactly like a working store."""
    monkeypatch.setenv("JARVIS_MEMORY", str(tmp_path / "gone.toml"))
    with pytest.raises(memory.MemoryError_, match="not a file"):
        memory.load(tmp_path)


# --------------------------------------------------------------------------
# remember
# --------------------------------------------------------------------------

def test_remember_hands_over_both_halves(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY", raising=False)
    sink = tmp_path / "written"
    tool = tmp_path / "write.sh"
    tool.write_text(f'printf "%s|%s" "$1" "$2" > {sink}\n', encoding="utf-8")
    mem = memory.Memory(enabled=True, recall="x.sh", remember="write.sh")
    memory.remember(mem, "the question", "the answer", tmp_path)
    for _ in range(50):                     # it is detached, so wait for it
        if sink.exists():
            break
        import time
        time.sleep(0.05)
    assert sink.read_text() == "the question|the answer"


def test_remember_does_nothing_without_an_answer(tmp_path):
    mem = memory.Memory(enabled=True, recall="x.sh", remember="gone.sh")
    memory.remember(mem, "q", "", tmp_path)     # must not raise


def test_remember_is_skipped_while_memory_is_off(tmp_path):
    mem = memory.Memory(enabled=False, remember="gone.sh")
    memory.remember(mem, "q", "a", tmp_path)    # must not raise


# --------------------------------------------------------------------------
# the example connectors that ship
# --------------------------------------------------------------------------

def test_the_example_recall_finds_a_note(tmp_path, monkeypatch):
    """The shipped example has to work, or nobody gets past step one."""
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "deploys.md").write_text(
        "Decided 12 Aug: deploys go out on Tuesdays.\n", encoding="utf-8")
    monkeypatch.setenv("JARVIS_NOTES", str(notes))
    mem = memory.Memory(enabled=True, recall="memory.d/recall.example.sh")
    assert "Tuesdays" in memory.recall(mem, "what about the deploys", REPO)


def test_the_example_recall_says_nothing_when_it_finds_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_NOTES", str(tmp_path))
    mem = memory.Memory(enabled=True, recall="memory.d/recall.example.sh")
    assert memory.recall(mem, "something nobody wrote down", REPO) == ""


def test_the_example_recall_ignores_a_question_of_short_words(tmp_path, monkeypatch):
    """Grammar words match every note there is, so they are not searched for."""
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("we do it on the day\n", encoding="utf-8")
    monkeypatch.setenv("JARVIS_NOTES", str(notes))
    mem = memory.Memory(enabled=True, recall="memory.d/recall.example.sh")
    assert memory.recall(mem, "is it on us", REPO) == ""
