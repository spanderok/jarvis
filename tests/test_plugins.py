# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["pytest", "tomli; python_version < '3.11'"]
# ///
"""Tests for the room and action registry.

Run: uv run --with pytest pytest tests/ -q

Routing is the part of Jarvis that fails quietly. A pattern that stops matching
does not crash anything - the question just goes somewhere else, and nobody
notices for days. These tests are what makes that loud.

The shipped config is checked here too, not only synthetic ones: the examples
written next to each room and action in config/*.toml are the real contract.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import plugins  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def registry():
    return plugins.load(REPO)


def write(tmp_path: pathlib.Path, rooms: str = "", actions: str = "") -> pathlib.Path:
    """A whole config in a temp folder, so a test never reads the real one."""
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "rooms.toml").write_text(rooms, encoding="utf-8")
    (tmp_path / "config" / "actions.toml").write_text(actions, encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------
# the config that ships
# --------------------------------------------------------------------------

def test_shipped_config_loads(registry):
    assert registry.rooms, "config/rooms.toml has no enabled room"
    assert registry.actions, "config/actions.toml has no enabled action"


@pytest.mark.parametrize("phrase,want", [
    (p, w) for src in plugins.load(REPO).rooms + plugins.load(REPO).actions
    for p, w in src.examples
])
def test_shipped_examples_route_where_they_say(registry, phrase, want):
    """Every example written next to a room or action, run as a test."""
    assert registry.route(phrase.lower()) == want


def test_every_room_answers_to_something(registry):
    for room in registry.rooms:
        assert room.ask or room.tell or room.bare or room.mention, (
            f"room {room.id!r} cannot be reached by any wording")


def test_no_two_rooms_share_a_wording(registry):
    """The first match wins, so a shared word makes one room unreachable."""
    seen: dict[str, str] = {}
    for room in registry.rooms:
        for word in (*room.ask, *room.tell, *room.bare):
            assert word not in seen, (
                f"{word!r} belongs to both {seen[word]!r} and {room.id!r}")
            seen[word] = room.id


# --------------------------------------------------------------------------
# explicit addressing beats everything
# --------------------------------------------------------------------------

def test_an_order_names_its_room_and_keeps_the_task(registry):
    kind, room, task, phrase = registry.address("передай шефу выкати демо")
    assert (kind, room, task) == ("tell", "chief", "выкати демо")
    assert " " in phrase, "an order must be recognisable as explicit"


def test_asking_waits_and_telling_does_not(registry):
    assert registry.address("спроси шефа что там с ревью")[0] == "ask"
    assert registry.address("передай шефу выкати демо")[0] == "tell"


def test_a_bare_name_is_weaker_than_an_order(registry):
    """It still routes, but `phrase` has no space, which is how the daemon knows."""
    _, room, task, phrase = registry.address("шеф, посмотри логи")
    assert (room, task, " " in phrase) == ("chief", "посмотри логи", False)


def test_a_hyphen_is_not_a_word_break():
    """Otherwise "шеф-повар уволился" is an order to the chief."""
    reg = plugins.load(REPO)
    assert reg.address("шеф-повар уволился") == ("", "", "", "")


def test_a_name_with_nothing_after_it_is_not_an_order(registry):
    assert registry.address("шеф") == ("", "", "", "")


def test_a_name_in_the_middle_is_not_an_order(registry):
    assert registry.address("вчера шеф уехал в отпуск") == ("", "", "", "")


# --------------------------------------------------------------------------
# automatic routing
# --------------------------------------------------------------------------

def test_actions_are_tried_before_rooms(tmp_path):
    root = write(
        tmp_path,
        rooms='[[room]]\nid = "r"\nmention_patterns = [\'погода\']\n',
        actions='[[action]]\nid = "a"\npatterns = [\'погода\']\nrun = "x.sh"\n')
    assert plugins.load(root).route("какая погода") == "a"


def test_config_order_decides_between_two_rooms(tmp_path):
    root = write(tmp_path, rooms=(
        '[[room]]\nid = "first"\nmention_patterns = [\'слово\']\n'
        '[[room]]\nid = "second"\nmention_patterns = [\'слово\']\n'))
    assert plugins.load(root).route("тут слово") == "first"


def test_an_unrouted_phrase_goes_nowhere(registry):
    assert registry.route("сколько будет дважды два") == ""


# --------------------------------------------------------------------------
# merging drop-ins
# --------------------------------------------------------------------------

def test_a_drop_in_overrides_one_field_and_leaves_the_rest(tmp_path, monkeypatch):
    root = write(tmp_path, rooms=(
        '[[room]]\nid = "r"\nlabel = "старый"\nsession = "s"\ntell = ["передай r"]\n'))
    extra = tmp_path / "extra.toml"
    extra.write_text('[[room]]\nid = "r"\nlabel = "новый"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_ROOMS", str(extra))
    room = plugins.load(root).rooms[0]
    assert (room.label, room.session, room.tell) == ("новый", "s", ("передай r",))


def test_a_drop_in_can_switch_a_shipped_room_off(tmp_path, monkeypatch):
    root = write(tmp_path, rooms='[[room]]\nid = "r"\n[[room]]\nid = "keep"\n')
    extra = tmp_path / "extra.toml"
    extra.write_text('[[room]]\nid = "r"\nenabled = false\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_ROOMS", str(extra))
    assert [r.id for r in plugins.load(root).rooms] == ["keep"]


def test_an_override_keeps_its_original_place_in_the_order(tmp_path, monkeypatch):
    """Routing walks the list top to bottom - an override must not jump ahead."""
    root = write(tmp_path, rooms='[[room]]\nid = "a"\n[[room]]\nid = "b"\n')
    extra = tmp_path / "extra.toml"
    extra.write_text('[[room]]\nid = "b"\nlabel = "x"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_ROOMS", str(extra))
    assert [r.id for r in plugins.load(root).rooms] == ["a", "b"]


def test_example_files_are_documentation_and_never_load(tmp_path, monkeypatch):
    root = write(tmp_path, rooms='[[room]]\nid = "r"\n')
    drop = tmp_path / "drop"
    drop.mkdir()
    (drop / "sample.example.toml").write_text('[[room]]\nid = "ghost"\n',
                                              encoding="utf-8")
    monkeypatch.setenv("JARVIS_ROOMS", str(drop))
    assert [r.id for r in plugins.load(root).rooms] == ["r"]


# --------------------------------------------------------------------------
# a bad config stops the daemon rather than starting it half-wired
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rooms,reason", [
    ('[[room]]\nlabel = "no id"\n', "an entry without an id"),
    ('[[room]]\nid = "r"\nmention_patterns = [\'(unclosed\']\n', "a broken regex"),
    ('[[room]]\nid = "r"\nrelay_via = "nobody"\n', "a relay to nowhere"),
    ('[[room]]\nid = "r"\nfallback = "nobody"\n', "a fallback to nowhere"),
    ('[[room]]\nid = "r"\nbare_kind = "shout"\n', "an unknown bare_kind"),
    ('[defaults]\nroom = "nobody"\n', "a default room that does not exist"),
])
def test_a_broken_room_is_refused(tmp_path, rooms, reason):
    root = write(tmp_path, rooms=rooms)
    with pytest.raises(plugins.ConfigError):
        plugins.load(root)


@pytest.mark.parametrize("actions,reason", [
    ('[[action]]\nid = "a"\nspeak = "sing"\n', "an unknown speak mode"),
    ('[[action]]\nid = "a"\nspeak = "retell"\n', "retell without a prompt"),
])
def test_a_broken_action_is_refused(tmp_path, actions, reason):
    root = write(tmp_path, actions=actions)
    with pytest.raises(plugins.ConfigError):
        plugins.load(root)


def test_the_error_names_the_file_it_came_from(tmp_path):
    root = write(tmp_path, rooms='[[room]]\nid = "r"\nfallback = "nobody"\n')
    with pytest.raises(plugins.ConfigError, match="rooms.toml"):
        plugins.load(root)


def test_a_relay_to_a_disabled_room_is_refused(tmp_path):
    """It parses, but at run time there is nothing to relay through."""
    root = write(tmp_path, rooms=(
        '[[room]]\nid = "off"\nenabled = false\n'
        '[[room]]\nid = "r"\nrelay_via = "off"\n'))
    with pytest.raises(plugins.ConfigError):
        plugins.load(root)


# --------------------------------------------------------------------------
# arguments
# --------------------------------------------------------------------------

def test_the_first_matching_arg_rule_wins(tmp_path):
    root = write(tmp_path, actions='''
[[action]]
id = "a"
patterns = ['.']
default_args = ["state"]
[[action.arg]]
patterns = ['громче']
args = ["vol", "+10"]
[[action.arg]]
patterns = ['тише']
args = ["vol", "-10"]
''')
    action = plugins.load(root).actions[0]
    assert action.argv("сделай погромче") == ["vol", "+10"]
    assert action.argv("что играет") == ["state"]


def test_content_words_come_after_the_fixed_arguments(tmp_path):
    root = write(tmp_path, actions='''
[[action]]
id = "a"
patterns = ['.']
default_args = ["--json"]
[action.words]
limit = 2
min_len = 4
stopwords = ["агентом"]
''')
    action = plugins.load(root).actions[0]
    assert action.argv("что с агентом ревью тесты деплой") == [
        "--json", "ревью", "тесты"]


def test_a_stopword_matches_one_word_form_and_not_its_cases(tmp_path):
    """Why the shipped list spells out агент, агента, агентом and the rest.

    Matching is exact. Listing the nominative only lets every other case
    through as if it named a session, and the script then filters the registry
    down to nothing.
    """
    root = write(tmp_path, actions='''
[[action]]
id = "a"
patterns = ['.']
[action.words]
min_len = 3
stopwords = ["агент", "что"]
''')
    assert plugins.load(root).actions[0].argv("что с агентом") == ["агентом"]


def test_a_stopword_and_a_short_word_are_dropped(tmp_path):
    root = write(tmp_path, actions='''
[[action]]
id = "a"
patterns = ['.']
[action.words]
min_len = 4
stopwords = ["статус"]
''')
    assert plugins.load(root).actions[0].argv("дай статус по ревью") == ["ревью"]


# --------------------------------------------------------------------------
# lines Jarvis says
# --------------------------------------------------------------------------

def test_a_placeholder_is_filled():
    assert plugins.fill("Спросил {label}.", label="шефа") == "Спросил шефа."


def test_an_unknown_placeholder_survives_instead_of_raising():
    """These strings are said out loud - one bad brace must not eat the answer."""
    assert plugins.fill("{label} и {typo}", label="шеф") == "шеф и {typo}"


def test_hints_follow_the_config(registry):
    """Rename a room and the system prompt follows, because the text lives there."""
    hints = registry.hints()
    for room in registry.rooms:
        if room.hint:
            assert room.hint.strip() in hints


# --------------------------------------------------------------------------
# environment overrides
# --------------------------------------------------------------------------

def test_env_overrides_one_rooms_session(tmp_path, monkeypatch):
    root = write(tmp_path, rooms='[[room]]\nid = "chief"\nsession = "шеф"\n')
    monkeypatch.setenv("JARVIS_ROOM_CHIEF_SESSION", "boss")
    assert plugins.load(root).rooms[0].env_session() == "boss"


def test_a_room_dir_is_expanded(tmp_path):
    root = write(tmp_path, rooms='[[room]]\nid = "r"\nwork_dir = "~/somewhere"\n')
    assert not plugins.load(root).rooms[0].env_dir().startswith("~")
