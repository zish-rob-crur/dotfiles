"""One structured `codex exec` call, shared by window_namer.py and task-board.py.

Runs the Codex CLI non-interactively on the user's subscription with a JSON
schema for the reply, and returns the parsed object (or None). Every call,
reply and failure is logged so naming and summary decisions can be audited.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

DEFAULT_MODEL = os.environ.get("TMUX_CODEX_MODEL", "gpt-5.4-mini")


def ask(prompt: str, schema: dict, *, state_dir: Path, log: logging.Logger,
        model: str = DEFAULT_MODEL, timeout: int = 120, label: str = "") -> dict | None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=state_dir) as tmp:
        schema_path = Path(tmp) / "schema.json"
        schema_path.write_text(json.dumps(schema))
        output = Path(tmp) / "out.json"
        stderr = Path(tmp) / "stderr.txt"
        log.info("codex: %s", label or "request")
        started = time.monotonic()
        try:
            subprocess.run(
                [
                    "codex", "exec",
                    "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
                    "-s", "read-only", "--color", "never",
                    "-C", tmp,
                    "-m", model, "-c", "model_reasoning_effort=low",
                    "--output-schema", str(schema_path), "-o", str(output),
                    prompt,
                ],
                stdin=subprocess.DEVNULL,  # codex exec reads stdin to EOF when it is not a tty
                stdout=subprocess.DEVNULL,
                stderr=stderr.open("w"),
                timeout=timeout,
            )
        except FileNotFoundError:
            log.error("codex CLI not found on PATH")
            return None
        except subprocess.TimeoutExpired:
            log.error("codex exec timed out after %ss\n%s", timeout, _tail(stderr))
            return None
        if not output.exists():
            log.error("codex returned nothing (not logged in or offline?)\n%s", _tail(stderr))
            return None
        raw = output.read_text()
        log.info("codex replied in %.1fs: %s", time.monotonic() - started, raw.strip())
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            log.error("codex reply is not JSON")
            return None
        return value if isinstance(value, dict) else None


def _tail(path: Path, lines: int = 15) -> str:
    if not path.exists():
        return ""
    return "".join(path.read_text(errors="replace").splitlines(keepends=True)[-lines:]).strip()
