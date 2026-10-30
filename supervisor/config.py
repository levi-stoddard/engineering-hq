"""Load and check the supervisor config.

The supervisor refuses to start when:
  * a `${VAR}` the config references is unset or still a placeholder,
  * an approver uses one of the published demo tokens outside a config marked `demo: true`,
  * a service is not a fixed argument list, or would be checked anywhere but 127.0.0.1,
  * an action appears in more than one policy class.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

import yaml

from policy import Policy, PolicyError

ENV_REF = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
PLACEHOLDER = re.compile(r"(?i)^(|changeme|change-me|todo|xxx+|your[-_ ].*|<.*>|replace[-_ ]?me.*|placeholder.*)$")
# The example config ships with these tokens so the walkthrough works without setup.
# They are public, so they are only accepted in a config marked `demo: true`.
DEMO_TOKENS = ("acme-demo-owner", "acme-demo-reviewer")
DEMO_TOKEN_SHA256 = {hashlib.sha256(t.encode()).hexdigest() for t in DEMO_TOKENS}
NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class ConfigError(Exception):
    pass


def is_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER.match(value.strip()))


def _resolve_env(value: Any, env: dict[str, str], missing: list[str]) -> Any:
    if isinstance(value, dict):
        return {k: _resolve_env(v, env, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v, env, missing) for v in value]
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            resolved = env.get(name, "")
            if is_placeholder(resolved):
                missing.append(name)
            return resolved
        return ENV_REF.sub(replace, value)
    return value


def load_config(path: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    path = Path(path).resolve()
    if not path.is_file():
        raise ConfigError(f"config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    missing: list[str] = []
    config = _resolve_env(raw, dict(os.environ if env is None else env), missing)
    if missing:
        raise ConfigError(f"set these environment variables to real values first: {', '.join(sorted(set(missing)))}")

    base = path.parent
    config["_config_path"] = str(path)
    config["_base"] = str(base)
    config["state_dir"] = str((base / config.get("state_dir", "state")).resolve())
    releases = config.setdefault("releases", {})
    releases["dir"] = str((base / releases.get("dir", "releases")).resolve())
    releases["sources"] = str((base / releases.get("sources", "release-sources")).resolve())
    vault = config.setdefault("vault", {})
    vault["dir"] = str((base / vault.get("dir", "vault")).resolve())

    check_config(config)
    return config


def check_config(config: dict[str, Any]) -> None:
    approvers = config.get("approvers") or {}
    if not approvers:
        raise ConfigError("at least one approver is required")
    for name, spec in approvers.items():
        if not NAME.match(name):
            raise ConfigError(f"approver names are role-style identifiers (a-z, 0-9, -, _): {name!r}")
        token_hash = str((spec or {}).get("token_sha256", "")).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", token_hash):
            raise ConfigError(f"approver {name}: token_sha256 must be a 64-character SHA-256 hex digest")
        if token_hash in DEMO_TOKEN_SHA256 and not config.get("demo", False):
            raise ConfigError(f"approver {name} uses a published demo token; set a real one (scripts/new_token.py)")

    for name, check in (config.get("checks") or {}).items():
        argv = (check or {}).get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ConfigError(f"check {name}: argv must be a non-empty list of strings (no shell)")

    for service in config.get("services") or []:
        name = service.get("name", "?")
        if not isinstance(service.get("argv"), list) or not all(isinstance(a, str) for a in service["argv"]):
            raise ConfigError(f"service {name}: argv must be a list of strings (no shell)")
        if service.get("host", "127.0.0.1") != "127.0.0.1":
            raise ConfigError(f"service {name}: services are supervised on 127.0.0.1 only")
        port = service.get("port")
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ConfigError(f"service {name}: port must be an integer between 1024 and 65535")
        if not str(service.get("health", "")).startswith("/") or not service.get("marker"):
            raise ConfigError(f"service {name}: needs a health path and a body marker")

    try:
        Policy.from_config(config)
    except PolicyError as exc:
        raise ConfigError(str(exc)) from exc


SUPERVISOR_DIR = str(Path(__file__).resolve().parent)


def expand_argv(argv: list[str], python: str) -> list[str]:
    """Two substitutions only: `{python}` (the interpreter running the supervisor) and
    `{supervisor}` (the folder this code lives in). Nothing else in argv is interpreted."""
    return [python if part == "{python}" else part.replace("{supervisor}", SUPERVISOR_DIR) for part in argv]
