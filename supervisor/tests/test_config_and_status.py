import json
import threading
import urllib.error
import urllib.request

import pytest
import yaml

from config import ConfigError, load_config
from status_server import MARKER, serve


def write(acme, mutate):
    data = yaml.safe_load((acme / "config.yaml").read_text(encoding="utf-8"))
    mutate(data)
    (acme / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return acme / "config.yaml"


def test_placeholder_environment_values_refuse_start(acme):
    path = write(acme, lambda d: d.setdefault("checks", {}).update({"lint": {"argv": ["tool", "--key=${LINT_KEY}"]}}))
    with pytest.raises(ConfigError, match="LINT_KEY"):
        load_config(path, env={})
    with pytest.raises(ConfigError, match="LINT_KEY"):
        load_config(path, env={"LINT_KEY": "changeme"})
    with pytest.raises(ConfigError, match="LINT_KEY"):
        load_config(path, env={"LINT_KEY": "<your key>"})
    assert load_config(path, env={"LINT_KEY": "k-8f2a91"})["checks"]["lint"]["argv"][1] == "--key=k-8f2a91"


def test_demo_tokens_only_work_in_a_demo_config(acme):
    path = write(acme, lambda d: d.update({"demo": False}))
    with pytest.raises(ConfigError, match="published demo token"):
        load_config(path, env={})


def test_services_are_loopback_only_and_shell_free(acme):
    path = write(acme, lambda d: d["services"][0].update({"host": "0.0.0.0"}))
    with pytest.raises(ConfigError, match="127.0.0.1 only"):
        load_config(path, env={})
    path = write(acme, lambda d: d["services"][0].update({"host": "127.0.0.1", "argv": "python server.py"}))
    with pytest.raises(ConfigError, match="no shell"):
        load_config(path, env={})


def test_overlapping_policy_classes_refuse_start(acme):
    path = write(acme, lambda d: d["policy"]["routine"].append("release"))
    with pytest.raises(ConfigError, match="more than one class"):
        load_config(path, env={})


@pytest.fixture
def status_server(config):
    server = serve(config, 0)
    port = server.server_address[1]
    server.RequestHandlerClass = __import__("status_server").make_handler(config, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    server.server_close()


def call(port, path, method="GET", host=None, origin=None):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"{}" if method == "POST" else None)
    request.add_header("Host", host or f"127.0.0.1:{port}")
    if origin:
        request.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_status_server_is_read_only_and_loopback(status_server):
    port = status_server
    code, body = call(port, "/health")
    assert code == 200 and body["service"] == MARKER
    assert call(port, "/api/requests")[0] == 200
    assert call(port, "/api/verify")[1] == {"problems": []}
    assert call(port, "/api/requests", method="POST")[0] == 405
    assert call(port, "/api/requests", host="evil.example")[0] == 403
    assert call(port, "/api/requests", origin="http://evil.example")[0] == 403
    assert call(port, "/admin")[0] == 404


def test_the_real_config_template_needs_real_token_hashes(tmp_path):
    from conftest import SUPERVISOR

    template = tmp_path / "config.yaml"
    template.write_text((SUPERVISOR / "config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ConfigError, match="HQ_OWNER_TOKEN_SHA256, HQ_REVIEWER_TOKEN_SHA256"):
        load_config(template, env={})
    with pytest.raises(ConfigError, match="HQ_REVIEWER_TOKEN_SHA256"):
        load_config(template, env={"HQ_OWNER_TOKEN_SHA256": "a" * 64, "HQ_REVIEWER_TOKEN_SHA256": "your-reviewer-token-sha256"})
    loaded = load_config(template, env={"HQ_OWNER_TOKEN_SHA256": "a" * 64, "HQ_REVIEWER_TOKEN_SHA256": "b" * 64})
    assert loaded["switches"]["execution_enabled"] is False
