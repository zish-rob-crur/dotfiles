#!/usr/bin/env python3
"""Headless PTY benchmark for the dedicated Edit Anywhere Neovim server."""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import math
import os
from pathlib import Path
import pty
import re
import secrets
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGER = REPO_ROOT / "bin" / "edit-anywhere-server"
ENTRYPOINT = REPO_ROOT / "bin" / "edit-anywhere-nvim"
RUNTIME_ROOT = REPO_ROOT / "edit-anywhere" / "nvim"
SESSION_ID_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}-[A-Fa-f0-9]{8}$")


class BenchmarkFailure(RuntimeError):
    """The benchmark facility or the server violated an invariant."""


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{secrets.token_hex(4)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise BenchmarkFailure(f"expected an object in {path}")
    return value


def wait_for_file(path: Path, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and not path.is_symlink():
            try:
                return read_json(path)
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(0.005)
    raise BenchmarkFailure(f"timed out waiting for {path}")


def nearest_rank(values: list[float], percentile: int) -> float:
    if not values:
        raise BenchmarkFailure("cannot summarize an empty sample")
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * percentile / 100))
    return ordered[rank - 1]


class Harness:
    def __init__(self, cache_root: Path, *, nvim: str | None = None) -> None:
        self.cache_root = cache_root.resolve()
        self.nvim = nvim or shutil.which("nvim") or ""
        if not self.nvim:
            raise BenchmarkFailure("nvim was not found")
        self.env = os.environ.copy()
        self.env.update(
            {
                "EDIT_ANYWHERE_CACHE_ROOT": str(self.cache_root),
                "EDIT_ANYWHERE_RUNTIME_ROOT": str(RUNTIME_ROOT),
                "EDIT_ANYWHERE_NVIM_BIN": self.nvim,
                "TERM": "xterm-256color",
                "COLORTERM": "truecolor",
            }
        )
        self.socket = self.cache_root / "server" / "nvim.sock"
        self.pid_path = self.cache_root / "server" / "nvim.pid"
        self.started = False

    def manager(
        self,
        *arguments: str,
        check: bool = True,
        timeout: float = 5.0,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [str(MANAGER), *arguments],
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if check and completed.returncode != 0:
            raise BenchmarkFailure(
                f"manager {' '.join(arguments)} failed ({completed.returncode}): "
                f"{completed.stderr.strip()}"
            )
        return completed

    def start(self) -> None:
        self.cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.cache_root, 0o700)
        self.manager("start", timeout=8.0)
        health = self.health()
        if not (
            health.get("state") == "IDLE"
            and health.get("prewarmed") is True
            and health.get("adapters_ok") is True
            and health.get("layout_ok") is True
        ):
            raise BenchmarkFailure(f"server did not become healthy: {health}")
        self.started = True

    def stop(self) -> None:
        if not self.started:
            return
        self.manager("stop", "--abort-active", check=False, timeout=5.0)
        self.started = False

    def health(self) -> dict[str, Any]:
        completed = self.manager("health")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise BenchmarkFailure(f"invalid health response: {completed.stdout!r}") from error

    def server_pid(self) -> int:
        try:
            return int(self.pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError) as error:
            raise BenchmarkFailure("server PID is unavailable") from error

    def rss_kib(self) -> int:
        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(self.server_pid())],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            return int(completed.stdout.strip())
        except ValueError as error:
            raise BenchmarkFailure("could not read server RSS") from error

    def create_session(self, body: str) -> tuple[str, str, Path]:
        session_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4).upper()
        nonce = secrets.token_urlsafe(24)
        context_token = secrets.token_urlsafe(24)
        session_dir = self.cache_root / "sessions" / session_id
        session_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        os.chmod(session_dir, 0o700)
        now = time.time_ns() // 1_000_000
        request = {
            "protocol_version": 1,
            "session_id": session_id,
            "nonce": nonce,
            "created_at_unix_ms": now,
            "expires_at_unix_ms": now + 120_000,
            "editor": {"filetype": "markdown", "cursor": "end", "start_insert": True},
            "context": {
                "source": "window-ocr",
                "token": context_token,
                "relative_path": "context.txt",
            },
            "source_window": {
                "pid": os.getpid(),
                "window_id": 1,
                "bundle_id": "dev.zish.edit-anywhere-benchmark",
            },
        }
        atomic_write(session_dir / "input.md", body.encode("utf-8"))
        atomic_write(
            session_dir / "request.json",
            (json.dumps(request, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8"),
        )
        return session_id, nonce, session_dir

    def _spawn_entrypoint(self, session_id: str) -> tuple[subprocess.Popen[bytes], int]:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        process = subprocess.Popen(
            [str(ENTRYPOINT), session_id],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=self.env,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave)
        return process, master

    @staticmethod
    def _wait_for_sentinel(master: int, sentinel: bytes, timeout: float) -> tuple[float, bytes]:
        deadline = time.monotonic() + timeout
        captured = bytearray()
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], min(0.05, deadline - time.monotonic()))
            if not readable:
                continue
            try:
                chunk = os.read(master, 65_536)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            captured.extend(chunk)
            if len(captured) > 2_000_000:
                del captured[:-1_000_000]
            if sentinel in captured:
                return time.monotonic(), bytes(captured)
        raise BenchmarkFailure(f"remote UI did not emit ready sentinel {sentinel!r}")

    @staticmethod
    def _finish_remote_ui(process: subprocess.Popen[bytes], master: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while process.poll() is None and time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.05)
            if readable:
                try:
                    os.read(master, 65_536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            raise BenchmarkFailure("remote UI did not detach")

    def run_session(
        self,
        *,
        body: str,
        action: str = "cancel",
        inserted_text: str = "",
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        if action not in {"cancel", "commit"}:
            raise ValueError("action must be cancel or commit")
        session_id, nonce, session_dir = self.create_session(body)
        expected_pid = self.server_pid()
        request_start = time.monotonic()
        process, master = self._spawn_entrypoint(session_id)
        decision = wait_for_file(session_dir / "decision.json", timeout)
        if decision.get("outcome") != "accepted" or decision.get("nonce") != nonce:
            raise BenchmarkFailure(f"invalid decision: {decision}")

        try:
            ready_at, ready_capture = self._wait_for_sentinel(
                master,
                f"EDIT_ANYWHERE_READY:{session_id}".encode(),
                timeout,
            )
            ready = wait_for_file(session_dir / "ui-ready.json", 0.5)
            if ready.get("session_id") != session_id or ready.get("nonce") != nonce:
                raise BenchmarkFailure(f"invalid ui-ready identity: {ready}")

            if action == "commit" and inserted_text:
                os.write(master, inserted_text.encode("utf-8"))
                time.sleep(0.03)
            os.write(master, b"\x1b")
            time.sleep(0.03)
            os.write(master, b"ZZ" if action == "commit" else b"ZQ")
            self._finish_remote_ui(process, master, timeout)
        finally:
            try:
                os.close(master)
            except OSError:
                pass
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

        result = wait_for_file(session_dir / "result.json", timeout)
        expected_status = "committed" if action == "commit" else "cancelled"
        if result.get("status") != expected_status or result.get("nonce") != nonce:
            raise BenchmarkFailure(f"unexpected result: {result}")
        output = session_dir / "output.md"
        if action == "cancel" and output.exists():
            raise BenchmarkFailure("cancelled session unexpectedly produced output.md")
        if action == "commit":
            expected_body = body + inserted_text
            if output.read_text(encoding="utf-8") != expected_body:
                raise BenchmarkFailure("committed output did not match the edited buffer")

        if self.server_pid() != expected_pid:
            raise BenchmarkFailure("server PID changed during a warm session")
        health = self.health()
        if health.get("state") != "IDLE" or health.get("active_session") is not None:
            raise BenchmarkFailure(f"server did not return to IDLE: {health}")

        return {
            "session_id": session_id,
            "request_to_ui_ready_ms": round((ready_at - request_start) * 1000, 3),
            "server_pid": expected_pid,
            "status": expected_status,
            "ready_screen_has_ocr_status": "󱄽".encode("utf-8") in ready_capture,
        }


def write_benchmark_artifacts(cache_root: Path, samples: list[dict[str, Any]], summary: dict[str, Any]) -> Path:
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4).upper()
    benchmarks_root = cache_root / "benchmarks"
    benchmarks_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(benchmarks_root, 0o700)
    directory = benchmarks_root / run_id
    directory.mkdir(mode=0o700, exist_ok=False)
    os.chmod(directory, 0o700)
    attach = b"".join(
        (json.dumps(sample, separators=(",", ":")) + "\n").encode("utf-8") for sample in samples
    )
    atomic_write(directory / "attach.jsonl", attach)
    atomic_write(
        directory / "summary.json",
        (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return directory


def metric_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def evaluate_hammerspoon_metric(
    session_id: str,
    metric: dict[str, Any],
) -> tuple[str, str | None, float | None]:
    """Return (complete|failed|pending, reason, end_to_end_ms)."""
    if metric.get("protocol_version") != 1:
        return "failed", "protocol_version_mismatch", None
    if metric.get("session_id") != session_id:
        return "failed", "session_id_mismatch", None
    if metric.get("process") != "hammerspoon":
        return "failed", "process_mismatch", None

    focused = metric_number(metric.get("hotkey_to_qt_focused_ms"))
    ready = metric_number(metric.get("hotkey_to_ui_ready_ms"))
    end_to_end = metric_number(metric.get("end_to_end_ms"))
    if focused is not None and ready is not None and end_to_end is not None:
        expected = max(focused, ready)
        if not math.isclose(end_to_end, expected, rel_tol=0, abs_tol=0.5):
            return "failed", "end_to_end_formula_mismatch", None
        return "complete", None, end_to_end

    status = metric.get("status")
    if isinstance(status, str) and (
        status.startswith("rejected:")
        or status in {"finished_without_writeback", "failed", "clipboard_only"}
    ):
        return "failed", f"terminal_status:{status}", None
    return "pending", "incomplete_hammerspoon_metrics", None


class PassiveE2ECollector:
    """Read-only watcher for Hammerspoon-owned metric shards."""

    def __init__(
        self,
        cache_root: Path,
        *,
        warmups: int,
        samples: int,
        timeout: float,
        session_timeout: float,
    ) -> None:
        self.cache_root = cache_root.expanduser().resolve()
        self.sessions_root = self.cache_root / "sessions"
        if not self.cache_root.is_dir() or self.cache_root.is_symlink():
            raise BenchmarkFailure(f"cache root is unavailable or unsafe: {self.cache_root}")
        if not self.sessions_root.is_dir() or self.sessions_root.is_symlink():
            raise BenchmarkFailure(f"sessions directory is unavailable or unsafe: {self.sessions_root}")
        self.warmups = warmups
        self.samples = samples
        self.target = warmups + samples
        self.timeout = timeout
        self.session_timeout = session_timeout
        self.baseline = {entry.name for entry in self.sessions_root.iterdir() if entry.is_dir()}
        self.pending: dict[str, dict[str, Any]] = {}
        self.finalized: set[str] = set()
        self.records: list[dict[str, Any]] = []
        self.started_monotonic = time.monotonic()
        self.started_at_unix_ms = time.time_ns() // 1_000_000

    def _discover(self) -> None:
        candidates: list[tuple[int, str, Path]] = []
        for entry in self.sessions_root.iterdir():
            if (
                not entry.is_dir()
                or entry.is_symlink()
                or entry.name in self.baseline
                or entry.name in self.pending
                or entry.name in self.finalized
                or not SESSION_ID_PATTERN.fullmatch(entry.name)
            ):
                continue
            try:
                created = entry.stat().st_ctime_ns
            except OSError:
                continue
            candidates.append((created, entry.name, entry))
        for _, session_id, directory in sorted(candidates):
            self.pending[session_id] = {
                "directory": directory,
                "first_seen": time.monotonic(),
            }

    def _finalize(
        self,
        session_id: str,
        *,
        outcome: str,
        reason: str | None,
        end_to_end_ms: float | None,
        metric: dict[str, Any] | None,
    ) -> None:
        sequence = len(self.records) + 1
        is_warmup = sequence <= self.warmups
        record = {
            "protocol_version": 1,
            "sequence": sequence,
            "phase": "warmup" if is_warmup else "sample",
            "session_id": session_id,
            "outcome": outcome,
            "reason": reason,
            "end_to_end_ms": round(end_to_end_ms, 3) if end_to_end_ms is not None else None,
            "hotkey_to_qt_focused_ms": (
                metric_number(metric.get("hotkey_to_qt_focused_ms")) if metric else None
            ),
            "hotkey_to_ui_ready_ms": (
                metric_number(metric.get("hotkey_to_ui_ready_ms")) if metric else None
            ),
            "hammerspoon_status": metric.get("status") if metric else None,
            "observed_at_unix_ms": time.time_ns() // 1_000_000,
        }
        self.records.append(record)
        self.finalized.add(session_id)
        self.pending.pop(session_id, None)
        position = sequence if is_warmup else sequence - self.warmups
        total = self.warmups if is_warmup else self.samples
        value = f"{end_to_end_ms:.3f} ms" if end_to_end_ms is not None else f"FAILED ({reason})"
        print(f"{record['phase']} {position:02d}/{total}: {value}", file=sys.stderr)

    def _poll_oldest(self) -> None:
        if not self.pending:
            return
        session_id = next(iter(self.pending))
        pending = self.pending[session_id]
        metric_path = pending["directory"] / "metrics" / "hammerspoon.json"
        metric: dict[str, Any] | None = None
        if metric_path.is_file() and not metric_path.is_symlink():
            try:
                metric = read_json(metric_path)
            except (json.JSONDecodeError, OSError, BenchmarkFailure):
                metric = None
        if metric is not None:
            outcome, reason, value = evaluate_hammerspoon_metric(session_id, metric)
            if outcome == "complete":
                self._finalize(
                    session_id,
                    outcome="ok",
                    reason=None,
                    end_to_end_ms=value,
                    metric=metric,
                )
                return
            if outcome == "failed":
                self._finalize(
                    session_id,
                    outcome="failed",
                    reason=reason,
                    end_to_end_ms=None,
                    metric=metric,
                )
                return
        if time.monotonic() - float(pending["first_seen"]) >= self.session_timeout:
            self._finalize(
                session_id,
                outcome="failed",
                reason="hammerspoon_metrics_timeout",
                end_to_end_ms=None,
                metric=metric,
            )

    def run(self) -> tuple[bool, bool]:
        print(
            f"Passive E2E collector armed. Trigger {self.warmups} warmups and "
            f"{self.samples} measured sessions yourself; Ctrl-C stops safely.",
            file=sys.stderr,
        )
        interrupted = False
        timed_out = False
        try:
            while len(self.records) < self.target:
                self._discover()
                self._poll_oldest()
                if self.timeout > 0 and time.monotonic() - self.started_monotonic >= self.timeout:
                    timed_out = True
                    break
                time.sleep(0.025)
        except KeyboardInterrupt:
            interrupted = True
            print("\nPassive E2E collection interrupted; writing partial results.", file=sys.stderr)
        return interrupted, timed_out


def summarize_e2e(
    collector: PassiveE2ECollector,
    *,
    interrupted: bool,
    timed_out: bool,
) -> dict[str, Any]:
    sample_records = [record for record in collector.records if record["phase"] == "sample"]
    values = [
        float(record["end_to_end_ms"])
        for record in sample_records
        if record["outcome"] == "ok" and record["end_to_end_ms"] is not None
    ]
    failures = sum(record["outcome"] != "ok" for record in sample_records)
    warmup_failures = sum(
        record["outcome"] != "ok" for record in collector.records if record["phase"] == "warmup"
    )
    return {
        "protocol_version": 1,
        "mode": "e2e-passive",
        "started_at_unix_ms": collector.started_at_unix_ms,
        "finished_at_unix_ms": time.time_ns() // 1_000_000,
        "warmups_requested": collector.warmups,
        "samples_requested": collector.samples,
        "warmups_observed": sum(record["phase"] == "warmup" for record in collector.records),
        "samples_observed": len(sample_records),
        "successful_samples": len(values),
        "unique_sessions_discovered": len(collector.records) + len(collector.pending),
        "pending_sessions": sorted(collector.pending),
        "duplicate_sessions": 0,
        "p50_ms": round(nearest_rank(values, 50), 3) if values else None,
        "p95_ms": round(nearest_rank(values, 95), 3) if values else None,
        "max_ms": round(max(values), 3) if values else None,
        "failures": failures,
        "warmup_failures": warmup_failures,
        "interrupted": interrupted,
        "timed_out": timed_out,
        "thresholds": {"p50_ms": 300, "p95_ms": 400},
    }


def write_e2e_artifacts(
    cache_root: Path,
    records: list[dict[str, Any]],
    summary: dict[str, Any],
) -> Path:
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4).upper()
    benchmarks_root = cache_root / "benchmarks"
    benchmarks_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(benchmarks_root, 0o700)
    directory = benchmarks_root / run_id
    directory.mkdir(mode=0o700, exist_ok=False)
    os.chmod(directory, 0o700)
    summary["artifact_dir"] = str(directory)
    raw = b"".join(
        (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8") for record in records
    )
    atomic_write(directory / "e2e.jsonl", raw)
    atomic_write(
        directory / "summary.json",
        (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return directory


def start_synthetic_hammerspoon_metrics(
    cache_root: Path,
    *,
    warmups: int,
    samples: int,
) -> threading.Thread:
    def produce() -> None:
        time.sleep(0.1)
        prefix = time.strftime("%Y%m%d-%H%M%S")
        for index in range(warmups + samples):
            session_id = f"{prefix}-{index + 1:08X}"
            session_dir = cache_root / "sessions" / session_id
            session_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
            value = 175.0 if index < warmups else 225.0 + ((index - warmups) % 4) * 25.0
            metric = {
                "protocol_version": 1,
                "session_id": session_id,
                "process": "hammerspoon",
                "hotkey_to_qt_focused_ms": value - 10,
                "hotkey_to_ui_ready_ms": value,
                "end_to_end_ms": value,
                "status": "ui_ready",
                "updated_at_unix_ms": time.time_ns() // 1_000_000,
            }
            atomic_write(
                session_dir / "metrics" / "hammerspoon.json",
                (json.dumps(metric, separators=(",", ":")) + "\n").encode("utf-8"),
            )
            time.sleep(0.04)

    thread = threading.Thread(target=produce, name="synthetic-hammerspoon-metrics", daemon=True)
    thread.start()
    return thread


def e2e_command(arguments: argparse.Namespace) -> int:
    self_test_root: Path | None = None
    producer: threading.Thread | None = None
    if arguments.self_test:
        if arguments.cache_root:
            raise BenchmarkFailure("--self-test cannot be combined with --cache-root")
        self_test_root = Path(tempfile.mkdtemp(prefix="ea-e2e-test.", dir="/tmp"))
        (self_test_root / "sessions").mkdir(mode=0o700)
        cache_root = self_test_root
    else:
        cache_root = Path(arguments.cache_root or "~/.cache/edit-anywhere").expanduser()

    try:
        collector = PassiveE2ECollector(
            cache_root,
            warmups=arguments.warmups,
            samples=arguments.samples,
            timeout=arguments.timeout,
            session_timeout=arguments.session_timeout,
        )
        if arguments.self_test:
            producer = start_synthetic_hammerspoon_metrics(
                cache_root,
                warmups=arguments.warmups,
                samples=arguments.samples,
            )
        interrupted, timed_out = collector.run()
        if producer:
            producer.join(timeout=2.0)
        summary = summarize_e2e(collector, interrupted=interrupted, timed_out=timed_out)
        write_e2e_artifacts(cache_root, collector.records, summary)
        print(json.dumps(summary, indent=2, sort_keys=True))

        complete = len(collector.records) == collector.target
        if interrupted or timed_out or not complete:
            return 2
        passed = (
            summary["failures"] == 0
            and summary["warmup_failures"] == 0
            and summary["p50_ms"] is not None
            and summary["p95_ms"] is not None
            and summary["p50_ms"] <= 300
            and summary["p95_ms"] <= 400
        )
        return 0 if passed else 1
    finally:
        if self_test_root is not None and not arguments.keep:
            shutil.rmtree(self_test_root, ignore_errors=True)
        elif self_test_root is not None:
            print(f"kept E2E self-test cache: {self_test_root}", file=sys.stderr)


def attach_command(arguments: argparse.Namespace) -> int:
    owned_cache = arguments.cache_root is None
    cache_root = (
        Path(tempfile.mkdtemp(prefix="ea-bench.", dir="/tmp"))
        if owned_cache
        else Path(arguments.cache_root).expanduser()
    )
    harness = Harness(cache_root, nvim=arguments.nvim)
    samples: list[dict[str, Any]] = []
    try:
        harness.start()
        server_pid = harness.server_pid()
        for _ in range(arguments.warmups):
            harness.run_session(body="warmup", action="cancel")
        rss_start = harness.rss_kib()
        for index in range(arguments.samples):
            sample = harness.run_session(body=f"sample-{index}", action="cancel")
            samples.append(sample)
            print(
                f"{index + 1:02d}/{arguments.samples}: "
                f"{sample['request_to_ui_ready_ms']:.3f} ms",
                file=sys.stderr,
            )
        rss_end = harness.rss_kib()
        values = [float(sample["request_to_ui_ready_ms"]) for sample in samples]
        summary = {
            "protocol_version": 1,
            "warmups": arguments.warmups,
            "samples": arguments.samples,
            "server_pid": server_pid,
            "p50_ms": round(nearest_rank(values, 50), 3),
            "p95_ms": round(nearest_rank(values, 95), 3),
            "max_ms": round(max(values), 3),
            "failures": 0,
            "rss_start_kib": rss_start,
            "rss_end_kib": rss_end,
            "rss_growth_kib": rss_end - rss_start,
            "thresholds": {"p50_ms": 60, "p95_ms": 100, "rss_growth_kib": 30 * 1024},
        }
        artifact_dir = write_benchmark_artifacts(cache_root, samples, summary)
        summary["artifact_dir"] = str(artifact_dir)
        print(json.dumps(summary, indent=2, sort_keys=True))
        passed = (
            summary["p50_ms"] <= 60
            and summary["p95_ms"] <= 100
            and summary["rss_growth_kib"] <= 30 * 1024
        )
        return 0 if passed else 1
    finally:
        harness.stop()
        if owned_cache and not arguments.keep:
            shutil.rmtree(cache_root, ignore_errors=True)
        elif owned_cache:
            print(f"kept benchmark cache: {cache_root}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    attach = subparsers.add_parser("attach", help="measure warm request-to-ready using an isolated PTY")
    attach.add_argument("--warmups", type=int, default=3)
    attach.add_argument("--samples", type=int, default=20)
    attach.add_argument("--cache-root")
    attach.add_argument("--nvim")
    attach.add_argument("--keep", action="store_true")
    attach.set_defaults(handler=attach_command)
    e2e = subparsers.add_parser(
        "e2e-start",
        help="passively collect new Hammerspoon end-to-end metrics",
    )
    e2e.add_argument("--warmups", type=int, default=3)
    e2e.add_argument("--samples", type=int, default=20)
    e2e.add_argument(
        "--timeout",
        type=float,
        default=900.0,
        help="overall collection deadline in seconds; 0 waits until Ctrl-C",
    )
    e2e.add_argument(
        "--session-timeout",
        type=float,
        default=6.0,
        help="seconds to wait for a discovered session's complete metrics",
    )
    e2e.add_argument("--cache-root")
    e2e.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    e2e.add_argument("--keep", action="store_true", help=argparse.SUPPRESS)
    e2e.set_defaults(handler=e2e_command)
    return parser


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()
    if getattr(arguments, "warmups", 0) < 0 or getattr(arguments, "samples", 1) <= 0:
        parser.error("warmups must be non-negative and samples must be positive")
    if getattr(arguments, "timeout", 0) < 0 or getattr(arguments, "session_timeout", 1) <= 0:
        parser.error("timeout must be non-negative and session-timeout must be positive")
    try:
        return int(arguments.handler(arguments))
    except (BenchmarkFailure, OSError, subprocess.SubprocessError) as error:
        print(f"benchmark facility error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
