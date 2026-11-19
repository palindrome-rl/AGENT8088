"""Opt-in real Chromium/browser-use checks with a controlled model and local page.

No paid provider is used. Only the model boundary is scripted; Agent, Chromium,
DOM operations and Agent8088's SSRF proxy all execute normally.
"""
import http.server
import json
import os
import threading
from types import SimpleNamespace as N

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT8088_RUN_BROWSER_RECOVERY") != "1",
    reason="set AGENT8088_RUN_BROWSER_RECOVERY=1 for real Chromium recovery checks")

PAGE = b'''<!doctype html><h1>Recovery fixture</h1>
<form id="f"><input id="name"><button type="submit">Go</button></form><p id="result"></p>
<script>document.getElementById('f').onsubmit = e => {
e.preventDefault(); window.submission = fetch('/submit', {method:'POST',
body: document.getElementById('name').value}).then(r=>r.text()).then(t=>{
document.getElementById('result').textContent=t; return t;});};</script>'''


@pytest.fixture
def page(engine, monkeypatch):
    state = {"submissions": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)

        def do_POST(self):
            text = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
            state["submissions"] += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(f"submitted: {text}".encode())

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = f"127.0.0.1:{server.server_port}"
    monkeypatch.setattr(engine, "SSRF_ALLOW_HOSTS", engine.SSRF_ALLOW_HOSTS | {host})
    monkeypatch.setenv("AGENT8088_BROWSER_HEADLESS", "1")
    monkeypatch.setenv("AGENT8088_BROWSER_REUSE_SESSION", "0")
    monkeypatch.setenv("AGENT8088_BROWSER_HITL", "0")
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "false")
    monkeypatch.setenv("BROWSER_USE_CLOUD_SYNC", "false")
    monkeypatch.setattr(engine, "BROWSER_TASK_TIMEOUT_SECONDS", 45)
    try:
        yield f"http://{host}/", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def scripted_browser_model(monkeypatch, code, expected):
    from browser_use.llm.views import ChatInvokeCompletion

    class Model:
        model = "controlled-fixture"
        provider = "fixture"
        name = "controlled-fixture"
        model_name = model
        _verified_api_keys = True
        calls = 0

        async def ainvoke(self, messages, output_format=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                action = {"evaluate": {"code": code}}
            else:
                # A done claim must be grounded in the actual browser result.
                observed = "\n".join(str(m.content) for m in messages)
                assert expected in observed, observed[-3000:]
                action = {"done": {"text": expected, "success": True}}
            output = output_format.model_validate({
                "evaluation_previous_goal": "Controlled fixture check",
                "memory": "Use only observed local fixture output",
                "next_goal": "Verify result", "action": [action],
            })
            return ChatInvokeCompletion(completion=output, usage=None)

    model = Model()
    monkeypatch.setattr("agent8088.browser_llm.build_browser_chat_model", lambda *a, **k: model)
    return model


def test_real_browser_submits_once_when_the_outer_model_repeats(engine, monkeypatch, page):
    url, state = page
    model = scripted_browser_model(monkeypatch,
        "document.getElementById('name').value='Ada';"
        "document.getElementById('f').requestSubmit(); window.submission;", "submitted: Ada")
    args = {"url": url, "task": "Use the browser to fill Ada and submit the local fixture once."}
    calls = 0

    def completion(*a, **k):
        nonlocal calls
        calls += 1
        tc = [N(function=N(name="browse_page", arguments=json.dumps(args)))] if calls <= 2 else None
        return N(choices=[N(message=N(content="" if tc else "done", tool_calls=tc), finish_reason="stop")], usage=None)

    monkeypatch.setattr(engine, "create_completion", completion)
    monkeypatch.setattr(engine, "PERMISSION_MODE", "full-auto")
    results = []
    engine.run_agent([{"role": "user", "content": args["task"]}], max_turns=4,
                     allowed_tools={"browse_page"}, tools_def=[],
                     on_result=lambda name, result: results.append(result))
    assert any("submitted: Ada" in result for result in results), results
    assert state["submissions"] == 1
    assert model.calls == 2


def test_real_browser_proxy_blocks_metadata_fetch(engine, monkeypatch, page):
    url, _state = page
    checked = []
    ssrf = engine._ssrf_check

    def recording(url):
        result = ssrf(url)
        if "169.254.169.254" in url:
            checked.append(result)
        return result

    monkeypatch.setattr(engine, "_ssrf_check", recording)
    scripted_browser_model(monkeypatch,
        "fetch('http://169.254.169.254/latest/meta-data/')"
        ".then(()=> 'metadata succeeded').catch(()=> 'metadata blocked');", "metadata blocked")
    result = engine._exec_browser({"url": url, "task": "Use the browser to test the local fixture's metadata fetch."})
    assert "metadata blocked" in result, result
    assert checked and all(checked), checked
