import json
import pytest

SECRET = "fake-regression-credential-123456"


@pytest.fixture
def workspace(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(engine, "ALLOWED_PATHS", [tmp_path])
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    monkeypatch.setattr(engine, "PLAN_AUDIT", False)
    monkeypatch.setattr(engine, "BLOCK_SECRETS_IN_WRITES", True)
    monkeypatch.setattr(engine, "_SECRET_VALUES", {SECRET, "short"})
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    engine.reset_turn_counters()
    return engine, repo


def write(engine, target, content, **extra):
    return engine.exec_tool("write_file", json.dumps({"filename": str(target), "content": content, **extra}))


def test_new_file_secret_is_refused_without_echo_or_budget_spend(workspace):
    engine, repo = workspace
    result = write(engine, repo / "app.txt", SECRET)
    assert "credential" in result and SECRET not in result
    assert not (repo / "app.txt").exists() and engine._turn_writes == 0


def test_append_part_with_secret_leaves_original_intact(workspace):
    engine, repo = workspace
    path = repo / "app.txt"
    path.write_text("original", encoding="utf-8")
    assert "credential" in write(engine, path, SECRET, mode="append")
    assert path.read_text(encoding="utf-8") == "original"


def test_append_boundary_cannot_assemble_secret(workspace):
    engine, repo = workspace
    path = repo / "app.txt"
    path.write_text(SECRET[:-8], encoding="utf-8")
    result = write(engine, path, SECRET[-8:], mode="append")
    assert "credential" in result and SECRET not in result
    assert path.read_text(encoding="utf-8") == SECRET[:-8]


def test_multiple_parts_cannot_assemble_secret(workspace):
    engine, repo = workspace
    path = repo / "app.txt"
    path.touch()
    assert write(engine, path, SECRET[:10]).startswith("Wrote ")
    assert write(engine, path, SECRET[10:20], mode="append").startswith("Appended ")
    assert "credential" in write(engine, path, SECRET[20:], mode="append")
    assert path.read_text(encoding="utf-8") == SECRET[:20]


def test_edit_checks_final_text_not_only_replacement(workspace):
    engine, repo = workspace
    path = repo / "app.txt"
    original = SECRET[:-8] + "PLACEHOLDER"
    path.write_text(original, encoding="utf-8")
    result = engine.exec_tool("edit_file", json.dumps({"filename": str(path),
        "old_string": "PLACEHOLDER", "new_string": SECRET[-8:]}))
    assert "credential" in result and SECRET not in result
    assert path.read_text(encoding="utf-8") == original and engine._turn_writes == 0


def test_ordinary_text_and_short_values_pass(workspace):
    engine, repo = workspace
    assert write(engine, repo / "app.txt", "ordinary short text").startswith("Wrote ")


def test_off_switch_allows_complete_and_boundary_secret(workspace, monkeypatch):
    engine, repo = workspace
    monkeypatch.setattr(engine, "BLOCK_SECRETS_IN_WRITES", False)
    path = repo / "app.txt"
    path.touch()
    assert write(engine, path, SECRET[:-8]).startswith("Wrote ")
    assert write(engine, path, SECRET[-8:], mode="append").startswith("Appended ")
    assert path.read_text(encoding="utf-8") == SECRET


def test_denial_audit_contains_path_not_secret(workspace, monkeypatch):
    engine, repo = workspace
    records = []
    monkeypatch.setattr(engine, "_audit", lambda *args, **kwargs: records.append(kwargs))
    write(engine, repo / "app.txt", SECRET)
    assert any(item.get("reason") == "secret_in_file" for item in records)
    assert SECRET not in json.dumps(records)


def test_downloaded_content_is_scanned_before_writing(workspace, monkeypatch):
    engine, repo = workspace
    monkeypatch.setattr(engine, "_fetch_url_bytes", lambda url: (SECRET.encode(), None, "text/plain"))
    path = repo / "app.txt"
    result = engine.exec_tool("write_file", json.dumps({"filename": str(path),
        "source_url": "https://example.com/app.txt"}))
    assert "credential" in result and not path.exists()


def test_unicode_secret_split_across_append_boundary(workspace, monkeypatch):
    engine, repo = workspace
    secret = "credential-é-🔒-123456789"
    monkeypatch.setattr(engine, "_SECRET_VALUES", {secret})
    path = repo / "app.txt"
    path.write_text("x" * 100000 + secret[:-5], encoding="utf-8")
    assert "credential" in write(engine, path, secret[-5:], mode="append")
    assert not path.read_text(encoding="utf-8").endswith(secret)


@pytest.mark.parametrize("line", [
    'UA = "HarborWatch/1.0"',
    'headers = {"User-Agent": "HarborPriceWatch/1.0 (competitor price check)"}',
    'USER_AGENT = "Mozilla/5.0 AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36"',
    'req.add_header("User-Agent", "Python-urllib/3.11")',
])
def test_a_product_and_version_is_not_a_credential(line):
    from agent8088 import secret_scan
    assert secret_scan.find_secrets(line) == []


@pytest.mark.parametrize("line", [
    'key = "sk-live-AbCdEf0123456789xyz"',
    'token = "Kx9fQ2mPz7Lr4Tw8YbN3/vH6"',
    'blob = "aB3dE5gH7jK9mN1pQ3sT/5.0"',
])
def test_a_real_looking_key_is_still_reported(line):
    from agent8088 import secret_scan
    assert secret_scan.find_secrets(line)


@pytest.mark.parametrize("line", [
    "// teammates/EV spreads, and the top 20 moves/items/partners per Pokemon",
    "see strategies/archetypes for the full list",
    "headers = {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9'}",
    "//  atkMult/defMult/spaMult/spdMult/speMult : passive multipliers on the holder's stats",
    "html = get('https://bulbapedia.bulbagarden.net/wiki/List_of_Pok%C3%A9mon_in_Pok%C3%A9mon_Champions')",
    "lead with service-and-stock-and-turnaround rather than price",
])
def test_ordinary_text_is_not_reported_as_a_credential(line):
    from agent8088 import secret_scan
    assert secret_scan.find_secrets(line) == []


@pytest.mark.parametrize("line", [
    'token = "Kx9fQ2mPz7Lr4Tw8YbN3/vH6"',
    'blob = "aB3dE5gH7jK9mN1pQ3sT/5.0"',
    'key = "aBcDeFgHiJ/kLmNoPqRsTu"',
    'k = "sk-live-AbCdEf0123456789xyz"',
    'a = "AKIAABCDEFGHIJKLMNOP"',
    'h = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"',
    "url = postgres://admin:s3cretpass@db.example.com/app",
])
def test_key_shapes_are_still_reported(line):
    from agent8088 import secret_scan
    assert secret_scan.find_secrets(line)


@pytest.mark.parametrize("line", [
    "{ name: 'Flamethrower', effectChance:30, power: 90 }",
    "{ sleepTarget:1, selfDrain:0.33, effectChance:.25 }",
    "const o=document.createElement('option'); const d=document.getElementById('log');",
    "l.scrollTop=l.scrollHeight; s.style.animationDuration=rand+'s';",
    "const idxs=Engine.getActiveIdx(side); const myTeam=fighters.filter(f=>f.alive);",
    "randF=0.85+Math.random()*0.15; f.el.offsetTop-6; targetSide:1-side",
    "// MOVES/ABILITIES/ITEMS tables; a1=teamAlive(1)",
])
def test_source_code_is_not_reported_as_a_credential(line):
    from agent8088 import secret_scan
    assert secret_scan.find_secrets(line) == []


@pytest.mark.parametrize("line", [
    "token=Kx9fQ2mPz7Lr4Tw8YbN3vH6",
    "auth:aB3dE5gH7jK9mN1pQ3sT",
    "secret=9f86d081884c7d659a2feaa0",
    "x=Zq8Lr4Tw8YbN3vH6Kx9fQ2mPz7+Ab",
])
def test_a_key_after_a_name_and_separator_is_still_reported(line):
    from agent8088 import secret_scan
    assert secret_scan.find_secrets(line)


@pytest.mark.parametrize("line", [
    "/* ===================== DATA ===================== */",
    "/* ===================== STAT CALC ===================== */",
    "# ----------------------------------------",
    "//////////////////////////////////////////",
    "# ++++++++++++++++++++ Setup2 ++++++++++++++++++++",
    "/*=====================Team2Build=====================*/",
    "## ____________________ helpers ____________________",
])
def test_a_banner_or_divider_line_is_not_a_credential(line):
    from agent8088 import secret_scan
    assert secret_scan.find_secrets(line) == []


@pytest.mark.parametrize("line", [
    'key = "=====Kx9fQ2mPz7Lr4Tw8YbN3vH6====="',
    '/* ===== */ token = "Kx9fQ2mPz7Lr4Tw8YbN3/vH6"',
    'k = "sk-live-AbCdEf0123456789xyz" // ==========',
])
def test_a_key_next_to_a_banner_is_still_reported(line):
    from agent8088 import secret_scan
    assert secret_scan.find_secrets(line)


def test_only_an_opaque_string_with_no_digit_is_weak():
    from agent8088 import secret_scan
    weak = secret_scan.find_secrets('key = "aBcDeFgHiJ/kLmNoPqRsTu"')
    with_digits = secret_scan.find_secrets('blob = "aB3dE5gH7jK9mN1pQ3sT/5.0"')
    vendor = secret_scan.find_secrets('k = "sk-live-AbCdEf0123456789xyz"')
    assert weak and all(f.weak for f in weak)
    assert with_digits and not any(f.weak for f in with_digits)
    assert vendor and not any(f.weak for f in vendor)


def _step_state(engine, tmp_path, monkeypatch, text):
    from agent8088 import trajectory
    monkeypatch.setattr(engine, "PROJECT_ROOT", tmp_path)
    page = tmp_path / "notes.md"
    page.write_text(text, encoding="utf-8")
    state = trajectory.TrajectoryState({}, goal="g", message_count=1)
    state.data["mutation_count"] = 1
    return state, str(page)


def test_a_finding_that_only_looks_like_a_key_is_asked_about_once_and_never_recorded_as_unmet(engine, tmp_path, monkeypatch):
    state, page = _step_state(engine, tmp_path, monkeypatch, 'key = "aBcDeFgHiJ/kLmNoPqRsTu"\n')
    first = engine._secrets_step(state, [page])
    assert first is not None and first.unmet == ""
    state.data["mutation_count"] = 2       # the file changed, so it is scanned again
    assert engine._secrets_step(state, [page]) is None


def test_a_vendor_key_is_asked_about_every_time_and_recorded_when_the_budget_runs_out(engine, tmp_path, monkeypatch):
    state, page = _step_state(engine, tmp_path, monkeypatch, 'k = "sk-live-AbCdEf0123456789xyz"\n')
    first = engine._secrets_step(state, [page])
    state.data["mutation_count"] = 2
    second = engine._secrets_step(state, [page])
    assert first is not None and "credential" in first.unmet
    assert second is not None and "credential" in second.unmet
