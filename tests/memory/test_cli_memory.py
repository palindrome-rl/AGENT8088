"""The /memory command, and memory being on by default.

Config writes go to a tmp_path file: no test may touch a real
~/.agent8088/config.txt, which is the migration that once clobbered a working
provider key for an hour.
"""
import pytest


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """The CLI module with memory wired to a temp store and a temp config."""
    from agent8088 import cli as cli_module
    from agent8088 import memory as memory_module

    config = tmp_path / "config.txt"
    config.write_text("memory=1\n", encoding="utf-8")
    monkeypatch.setattr(cli_module.A, "CONFIG_PATH", config)
    monkeypatch.setattr(cli_module.A, "APP_CONFIG", dict(cli_module.A.APP_CONFIG))
    monkeypatch.setattr(cli_module.A, "MEMORY_DB_PATH", tmp_path / "memory.db")

    memory_module.reset()
    memory_module.configure(
        config={"memory": "1", "memory_embed_model": ""},
        client_factory=lambda: None,
        completion=lambda prompt: ('{"memories": []}', {}),
        db_path=tmp_path / "memory.db",
        project=str(tmp_path),
    )
    yield cli_module
    memory_module.reset()


def output(capsys):
    return capsys.readouterr().out


# -- defaults --------------------------------------------------------------

def test_an_explicit_zero_turns_memory_off():
    from agent8088 import memory as memory_module
    memory_module.reset()
    memory_module.configure(config={"memory": "0"}, db_path="/tmp/x.db")
    assert not memory_module.enabled()
    memory_module.reset()


def test_the_shipped_config_enables_memory():
    """config.txt is what a fresh install gets; it must not disagree with the
    package default."""
    from pathlib import Path

    from agent8088 import engine
    shipped = (Path(engine.__file__).parent / "config.txt").read_text(encoding="utf-8")
    assert "\nmemory=1" in shipped


# -- status ----------------------------------------------------------------

def test_status_reports_the_store(cli, capsys):
    cli.A.memory.store().add("prefers uv over pip", user_id="owner")
    cli.cmd_memory("")
    printed = output(capsys)
    assert "Memories" in printed
    assert "keyword only" in printed


def test_status_when_memory_is_off(cli, capsys):
    cli.A.memory.reset()
    cli.A.memory.configure(config={"memory": "0"}, db_path=cli.A.MEMORY_DB_PATH)
    cli.cmd_memory("")
    assert "off" in output(capsys)


# -- on / off --------------------------------------------------------------

def test_turning_memory_off_persists_to_config(cli, capsys):
    cli.cmd_memory("off")
    assert "memory=0" in cli.A.CONFIG_PATH.read_text(encoding="utf-8")
    assert "kept" in output(capsys).lower()


def test_turning_memory_on_persists_to_config(cli):
    cli.cmd_memory("off")
    cli.cmd_memory("on")
    assert "memory=1" in cli.A.CONFIG_PATH.read_text(encoding="utf-8")


def test_turning_memory_on_names_the_pull_command_when_the_embedder_is_missing(
        cli, capsys, monkeypatch):
    """Stubbed rather than probed: whether the developer happens to have Ollama
    running must not decide whether this test passes."""
    class Unavailable:
        model = "nomic-embed-text"
        last_error = "model not found"

        def available(self):
            return False

    monkeypatch.setattr(cli.A.memory, "embedder", lambda: Unavailable())
    cli.cmd_memory("on")
    printed = output(capsys)
    assert "ollama pull nomic-embed-text" in printed


# -- add / forget / clear --------------------------------------------------

def test_add_stores_a_memory_by_hand(cli, capsys):
    cli.cmd_memory("add deploys always go through staging first")
    assert "remembered" in output(capsys)
    rows = cli.A.memory.store().get_all(user_id="owner")
    assert rows[0]["text"] == "deploys always go through staging first"
    assert rows[0]["source"] == "user"


def test_adding_the_same_memory_twice_says_so(cli, capsys):
    cli.cmd_memory("add deploys always go through staging first")
    capsys.readouterr()
    cli.cmd_memory("add deploys always go through staging first")
    assert "already remembered" in output(capsys)


def test_forget_accepts_the_short_id_that_search_prints(cli, capsys):
    memory_id = cli.A.memory.store().add("prefers uv over pip", user_id="owner")
    cli.cmd_memory(f"forget {memory_id[:8]}")
    assert "forgotten" in output(capsys)
    assert cli.A.memory.store().count(user_id="owner") == 0


def test_forget_refuses_an_ambiguous_prefix(cli, capsys, monkeypatch):
    store = cli.A.memory.store()
    store.add("first fact", user_id="owner")
    store.add("second fact", user_id="owner")
    # Force both ids to share a prefix so the ambiguity is real rather than luck.
    ids = [row["id"] for row in store.get_all(user_id="owner")]
    store.connect().execute("UPDATE memories SET id='abcd1111-x' WHERE id=?", (ids[0],))
    store.connect().execute("UPDATE memories SET id='abcd2222-x' WHERE id=?", (ids[1],))
    store.connect().commit()
    cli.cmd_memory("forget abcd")
    assert "matches 2 memories" in output(capsys)
    assert store.count(user_id="owner") == 2


def test_forget_reports_an_unknown_id(cli, capsys):
    cli.cmd_memory("forget deadbeef")
    assert "no memory starts with" in output(capsys)


def test_clear_asks_before_deleting(cli, capsys, monkeypatch):
    cli.A.memory.store().add("prefers uv", user_id="owner")
    monkeypatch.setattr(cli, "_confirm_destructive", lambda *args, **kwargs: False)
    cli.cmd_memory("clear")
    assert "kept" in output(capsys)
    assert cli.A.memory.store().count(user_id="owner") == 1


def test_clear_deletes_when_confirmed(cli, capsys, monkeypatch):
    cli.A.memory.store().add("prefers uv", user_id="owner")
    monkeypatch.setattr(cli, "_confirm_destructive", lambda *args, **kwargs: True)
    cli.cmd_memory("clear")
    assert "cleared 1" in output(capsys)
    assert cli.A.memory.store().count(user_id="owner") == 0


# -- search ----------------------------------------------------------------

def test_search_shows_each_legs_rank(cli, capsys):
    """The per-leg ranks are the point of this view: they are the only way to tell
    a tuning problem from a missing embedder."""
    cli.A.memory.store().add("prefers uv over pip", user_id="owner")
    cli.cmd_memory("search uv")
    printed = output(capsys)
    assert "Words" in printed and "Meaning" in printed
    assert "RRF" in printed


def test_search_says_when_nothing_matched(cli, capsys):
    cli.A.memory.store().add("prefers uv over pip", user_id="owner")
    cli.cmd_memory("search zzzznomatch")
    assert "no memories matched" in output(capsys)


def test_search_without_a_query_shows_usage(cli, capsys):
    cli.cmd_memory("search")
    assert "usage" in output(capsys)


# -- guards ----------------------------------------------------------------

def test_subcommands_refuse_while_memory_is_off(cli, capsys):
    cli.A.memory.reset()
    cli.A.memory.configure(config={"memory": "0"}, db_path=cli.A.MEMORY_DB_PATH)
    cli.cmd_memory("add something durable")
    assert "/memory on first" in output(capsys)


def test_an_unknown_subcommand_lists_the_real_ones(cli, capsys):
    cli.cmd_memory("frobnicate")
    printed = output(capsys)
    assert "unknown" in printed
    assert "forget" in printed


def test_the_command_is_registered(cli):
    assert cli.COMMANDS["memory"] is cli.cmd_memory
    assert "memory" in cli._COMPLETABLE_COMMANDS


def test_the_repl_asks_for_background_capture(cli):
    """The REPL renders the answer first and learns after, so the user never waits
    for an extraction call. It is a run_agent argument rather than module state so
    the gateway and cron are unaffected by the CLI having been imported."""
    import inspect
    source = inspect.getsource(cli.do_chat)
    assert "memory_background=True" in source


def test_capture_is_synchronous_unless_the_caller_asks_otherwise(engine):
    """The default must be the behaviour that cannot lose data: a daemon thread in
    the gateway or cron would drop the write when the process exits."""
    import inspect
    signature = inspect.signature(engine.run_agent)
    assert signature.parameters["memory_background"].default is False


# -- upgrade path ----------------------------------------------------------
#
# Setup edits a config in place, so one written before the `memory` key existed
# never gains it and falls back to the conservative code default. A user who
# upgrades would silently have no memory while a fresh install has it. Same shape
# and same reasoning as the web_search_no_prompt backfill.

def _set_line(content, key, value):
    import re
    pattern = rf'^{re.escape(key)}=.*'
    if re.search(pattern, content, re.MULTILINE):
        return re.sub(pattern, lambda _: f"{key}={value}", content, flags=re.MULTILINE)
    return content + f"\n{key}={value}\n"


def _packaged_memory_default():
    """Read the shipped value rather than hardcoding it, so setup and the template
    cannot drift apart."""
    import re
    from pathlib import Path

    from agent8088 import engine
    packaged = (Path(engine.__file__).parent / "config.txt").read_text(encoding="utf-8")
    return re.search(r'^\s*memory=(.*)$', packaged, re.MULTILINE).group(1).strip()


def test_an_older_config_gains_the_memory_key(cli):
    result = cli._backfill_memory_key("default_provider=ollama\n", _set_line)
    assert f"memory={_packaged_memory_default()}" in result


def test_the_backfilled_value_tracks_the_packaged_template(cli):
    import re
    result = cli._backfill_memory_key("default_provider=ollama\n", _set_line)
    match = re.search(r'^\s*memory=(.*)$', result, re.MULTILINE)
    assert match.group(1).strip() == _packaged_memory_default()


def test_the_backfill_does_not_overrule_a_deliberate_opt_out(cli):
    """Backfill fills a gap; it must not switch memory back on for someone who
    turned it off by hand."""
    result = cli._backfill_memory_key("memory=0\n", _set_line)
    assert "memory=0" in result
    assert "memory=1" not in result


def test_the_backfill_is_announced_rather_than_silent(cli, capsys):
    """It starts spending a model call per turn, so it must not happen quietly."""
    cli._backfill_memory_key("default_provider=ollama\n", _set_line)
    printed = output(capsys)
    assert "Added memory=1" in printed
    assert "extra model call per turn" in printed
    assert "/memory off" in printed


def test_the_backfill_reports_whether_semantic_recall_is_available(cli, capsys,
                                                                  monkeypatch):
    monkeypatch.setattr(cli, "_embedding_model_present", lambda: False)
    cli._backfill_memory_key("default_provider=ollama\n", _set_line)
    printed = output(capsys)
    assert "keyword search only" in printed
    assert "ollama pull nomic-embed-text" in printed


def test_the_backfill_says_nothing_extra_when_the_embedder_is_there(cli, capsys,
                                                                   monkeypatch):
    monkeypatch.setattr(cli, "_embedding_model_present", lambda: True)
    cli._backfill_memory_key("default_provider=ollama\n", _set_line)
    printed = output(capsys)
    assert "Semantic recall: on" in printed
    assert "ollama pull" not in printed


def test_the_embedder_probe_reports_false_without_ollama(cli, monkeypatch):
    """No Ollama is not an error -- a cloud provider serves /embeddings itself --
    but claiming a local model is installed when it is not is the exact failure
    this reporting exists to prevent."""
    import subprocess
    def explode(*args, **kwargs):
        raise FileNotFoundError("ollama")
    monkeypatch.setattr(subprocess, "run", explode)
    assert cli._embedding_model_present() is False


def test_setup_backfills_memory_into_an_older_config(cli, tmp_path, monkeypatch):
    """End to end through _run_setup, the way the installer runs it."""
    from agent8088 import providers
    import tests.test_cli_setup as setup_tests

    config = tmp_path / "upgrade-config.txt"
    saved = setup_tests._run_setup_over(config, monkeypatch)
    assert f"memory={_packaged_memory_default()}" in saved
