"""Single-instance daemon loop shared by the tmux helper daemons.

A lock directory holds the owner's pid and the script digest. A newer script
version takes over from a running older one; an instance that loses the lock
(for example because the cache directory was recreated) exits on its next round
instead of running alongside the new owner.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path


def run_daemon(lock_dir: Path, script: Path, interval: float, round_fn: Callable[[], None], log=None,
               sources: list[Path] | None = None) -> int:
    """`sources` lists every file whose change should replace a running daemon
    (the script plus the local modules it imports); defaults to the script alone."""
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in (sources or [script]))).hexdigest()

    try:
        lock_dir.mkdir()
    except FileExistsError:
        try:
            pid = int((lock_dir / "pid").read_text().strip())
            os.kill(pid, 0)
            if (lock_dir / "script-sha256").read_text().strip() == digest:
                return 0
            os.kill(pid, 15)
            time.sleep(0.2)
        except (FileNotFoundError, ProcessLookupError, ValueError, PermissionError):
            pass
        shutil.rmtree(lock_dir, ignore_errors=True)
        try:
            lock_dir.mkdir()
        except FileExistsError:
            return 0

    (lock_dir / "pid").write_text(f"{os.getpid()}\n")
    (lock_dir / "script-sha256").write_text(f"{digest}\n")

    def owns_lock() -> bool:
        try:
            return (lock_dir / "pid").read_text().strip() == str(os.getpid())
        except OSError:
            return False

    if log:
        log.info("daemon start pid=%d interval=%ss", os.getpid(), interval)
    try:
        while True:
            if not owns_lock():
                if log:
                    log.info("daemon exit: lock taken over")
                return 0
            if subprocess.run(["tmux", "has-session"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
                if log:
                    log.info("daemon exit: tmux server gone")
                return 0
            try:
                round_fn()
            except Exception:
                if log:
                    log.exception("daemon round failed")
            time.sleep(interval)
    finally:
        if owns_lock():
            shutil.rmtree(lock_dir, ignore_errors=True)
