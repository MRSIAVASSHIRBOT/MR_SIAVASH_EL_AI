"""Cross-runtime contract checks for Telegram bridge command dispatch."""

from __future__ import annotations

import re
from pathlib import Path

from el_ai.main import BRIDGE_OWNER_COMMANDS


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_WORKER_SOURCE = _REPOSITORY_ROOT / "cloudflare" / "telegram-edge" / "src" / "index.js"
_COMMAND_LITERAL = re.compile(r'"(?P<command>/[a-z0-9_]+)"')


def _read_worker_bridge_commands() -> tuple[str, ...]:
    """Read the Worker allowlist and reject source shapes this check cannot parse."""
    source = _WORKER_SOURCE.read_text(encoding="utf-8")
    declaration = "const BRIDGE_OWNER_COMMANDS = new Set(["
    start = source.find(declaration)
    assert start >= 0, (
        "Cloudflare Worker BRIDGE_OWNER_COMMANDS declaration changed; "
        "update this contract test deliberately"
    )

    body_start = start + len(declaration)
    end = source.find("]);", body_start)
    assert end >= 0, "Cloudflare Worker bridge command allowlist is not closed"
    body = source[body_start:end]

    commands = tuple(
        item.group("command") for item in _COMMAND_LITERAL.finditer(body)
    )
    unparsed = _COMMAND_LITERAL.sub("", body)
    assert commands, "Cloudflare Worker bridge command allowlist must not be empty"
    assert not unparsed.replace(",", "").strip(), (
        "Cloudflare Worker bridge command allowlist contains unparsed entries"
    )
    return commands


def test_python_and_cloudflare_worker_bridge_command_allowlists_match() -> None:
    worker_commands = _read_worker_bridge_commands()

    assert len(worker_commands) == len(set(worker_commands)), (
        "Cloudflare Worker bridge command allowlist contains duplicates"
    )
    assert frozenset(worker_commands) == BRIDGE_OWNER_COMMANDS, (
        "Python EL_AI and Cloudflare Worker bridge command allowlists must match exactly"
    )
