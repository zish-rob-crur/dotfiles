#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


TAB = "\t"
STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "tmux-pane-layout-status"
REFRESH_INTERVAL = float(os.environ.get("TMUX_PANE_LAYOUT_REFRESH_INTERVAL", "2"))
DAEMON_INTERVAL = float(os.environ.get("TMUX_PANE_LAYOUT_DAEMON_INTERVAL", "2"))

SHELL_ICON = ""
GRID_ICON = ""
CODEX_ICON = ""
CLAUDE_ICON = "✳"
SHELL_LIST_ICON = "·"
ELLIPSIS = "…"
SPINNER_PREFIXES = tuple(f"{char} " for char in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⠐")

SHELL_COMMANDS = {"sh", "zsh", "bash", "fish", "tmux"}
PRIORITY_ICONS = [
    CLAUDE_ICON,  # Claude Code
    CODEX_ICON,  # Codex
    "",  # Vim / Neovim
    "",  # Git
    "󰣀",  # SSH / remote
    "",  # Python
    "",  # Node / JS / TS
    "",  # Go
    "",  # Rust
    "",  # Docker
    "☸",  # Kubernetes
    "",  # Database
    "",  # Monitor
]


@dataclass(frozen=True)
class Pane:
    window_id: str
    pane_id: str
    active: bool
    left: int
    top: int
    width: int
    height: int
    command: str
    title: str
    path: str

    @property
    def center_x(self) -> float:
        return self.left + (self.width / 2)

    @property
    def center_y(self) -> float:
        return self.top + (self.height / 2)

    @property
    def icon(self) -> str:
        return icon_for_pane(self.command, self.title, self.path)


def tmux(args: list[str], check: bool = False) -> str:
    result = subprocess.run(
        ["tmux", *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return result.stdout.rstrip("\n")


def normalize_command(command: str) -> str:
    base = os.path.basename(command or "").lower()
    return base


def icon_for_command(command: str) -> str:
    command = normalize_command(command)

    if command.startswith("codex"):
        return CODEX_ICON
    if command.startswith("claude"):
        return CLAUDE_ICON

    if command in {"nvim", "vim", "vi"}:
        return ""
    if command in SHELL_COMMANDS:
        return SHELL_ICON
    if command in {"ssh", "mosh", "slogin"}:
        return "󰣀"
    if command in {"python", "python3", "ipython", "poetry", "pipenv", "pdm"}:
        return ""
    if command in {"node", "nodejs", "npm", "yarn", "pnpm", "bun", "tsx", "ts-node", "vite", "next", "nuxt"}:
        return ""
    if command == "deno":
        return ""
    if command in {"go", "dlv"}:
        return ""
    if command in {"cargo", "rustc", "rustup", "rust-analyzer"}:
        return ""
    if command in {"ruby", "irb", "rake", "bundle", "bundler"}:
        return ""
    if command in {"lua", "luajit"}:
        return ""
    if command in {"docker", "docker-compose", "podman"}:
        return ""
    if command in {"kubectl", "k9s", "helm", "minikube", "kind"}:
        return "☸"
    if command in {"git", "lazygit", "tig"}:
        return ""
    if command in {"htop", "btop", "top", "glances"}:
        return ""
    if command in {"psql", "pgcli", "mysql", "mariadb", "mongosh", "redis-cli", "sqlite3"}:
        return ""
    if command in {"java", "jshell", "mvn", "mvnw", "gradle", "gradlew"}:
        return ""
    if command in {"gcc", "g++", "clang", "clang++", "make", "cmake", "ninja"}:
        return ""

    return SHELL_ICON


def is_claude_pane(command: str, title: str, path: str) -> bool:
    command = normalize_command(command)

    if command.startswith("claude"):
        return True

    # Claude Code's native build can expose its version as pane_current_command
    # while setting a title like "✳ task summary".
    if title.startswith("✳ ") and re_fullmatch_version(command):
        return True

    return "/claude-envs/" in path


def re_fullmatch_version(command: str) -> bool:
    parts = command.split(".")
    return len(parts) == 3 and all(part.isdigit() for part in parts)


def icon_for_pane(command: str, title: str = "", path: str = "") -> str:
    if is_claude_pane(command, title, path):
        return CLAUDE_ICON

    return icon_for_command(command)


def important_icon(panes: list[Pane]) -> str:
    ordered = sorted(panes, key=lambda pane: (pane.top, pane.left, pane.pane_id))
    active = next((pane for pane in ordered if pane.active), ordered[0])
    active_icon = active.icon

    if active_icon != SHELL_ICON:
        return active_icon

    present = {pane.icon for pane in ordered}
    for icon in PRIORITY_ICONS:
        if icon in present:
            return icon

    return active_icon


def render_signal(panes: list[Pane]) -> str:
    if not panes:
        return ""

    ordered = sorted(panes, key=lambda pane: (pane.top, pane.left, pane.pane_id))
    count = len(ordered)

    if count == 1:
        icon = ordered[0].icon
        return icon

    if count == 2:
        left, right = sorted(ordered, key=lambda pane: (pane.left, pane.top, pane.pane_id))
        top, bottom = sorted(ordered, key=lambda pane: (pane.top, pane.left, pane.pane_id))

        if left.icon == right.icon:
            if left.icon == SHELL_ICON:
                return f"{GRID_ICON}2"
            return f"{left.icon}×2"

        dx = abs(left.center_x - right.center_x)
        dy = abs(top.center_y - bottom.center_y)

        if dx >= dy:
            return f"{left.icon}│{right.icon}"
        return f"{top.icon}/{bottom.icon}"

    icon = important_icon(ordered)
    if count <= 4:
        if icon == SHELL_ICON:
            return f"{GRID_ICON}{count}"
        return f"{icon}×{count}"

    if icon == SHELL_ICON:
        return f"{GRID_ICON}{count}"
    return f"{icon}+{count}"


def render_window_icon(panes: list[Pane]) -> str:
    if not panes:
        return ""

    icon = important_icon(panes)
    if icon == SHELL_ICON:
        return SHELL_LIST_ICON

    return icon


def collapse(value: str) -> str:
    return " ".join(value.split())


def display_basename(path: str) -> str:
    if not path:
        return "~"

    expanded = os.path.expanduser(path)
    if os.path.realpath(expanded) == os.path.realpath(str(Path.home())):
        return "~"

    return os.path.basename(expanded.rstrip(os.sep)) or expanded


def truncate(value: str, limit: int) -> str:
    value = collapse(value)
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    if limit == 1:
        return ELLIPSIS
    return value[: limit - 1].rstrip() + ELLIPSIS


def clean_title(title: str, command: str) -> str:
    title = collapse(title)
    if not title:
        return ""

    for prefix in (CODEX_ICON, CLAUDE_ICON):
        if title.startswith(prefix):
            title = title[len(prefix) :].strip()

    for prefix in SPINNER_PREFIXES:
        if title.startswith(prefix):
            title = title[len(prefix) :].strip()
            break

    command = normalize_command(command)
    generic_titles = SHELL_COMMANDS | {"vi", "vim", "nvim", command}
    if title.lower() in generic_titles:
        return ""

    return title


def pane_main_text(pane: Pane) -> str:
    title = clean_title(pane.title, pane.command)
    if title:
        return title

    command = normalize_command(pane.command)
    if pane.icon == CODEX_ICON:
        return "Codex"
    if pane.icon == CLAUDE_ICON:
        return "Claude"
    if command:
        return command
    return display_basename(pane.path)


def render_pane_labels(pane: Pane) -> tuple[str, str]:
    icon = pane.icon
    basename = display_basename(pane.path)
    main = pane_main_text(pane)
    active_limit = max(12, min(64, pane.width - 4))
    inactive_limit = max(8, min(34, pane.width - 4))

    if main.lower() == basename.lower():
        active = truncate(f"{icon} {main}", active_limit)
    else:
        active = truncate(f"{icon} {main} · {basename}", active_limit)

    if icon in {CODEX_ICON, CLAUDE_ICON}:
        inactive_main = clean_title(pane.title, pane.command) or basename
    else:
        inactive_main = basename
    inactive = truncate(f"{icon} {inactive_main}", inactive_limit)

    return active, inactive


def list_old_values() -> dict[str, tuple[str, str]]:
    output = tmux(["list-windows", "-a", "-F", f"#{{window_id}}{TAB}#{{@pane-layout-signal}}{TAB}#{{@pane-window-icon}}"])
    old_values: dict[str, tuple[str, str]] = {}
    for line in output.splitlines():
        if not line:
            continue
        window_id, _, rest = line.partition(TAB)
        signal, _, icon = rest.partition(TAB)
        if window_id:
            old_values[window_id] = (signal, icon)
    return old_values


def list_old_pane_labels() -> dict[str, tuple[str, str]]:
    output = tmux(
        [
            "list-panes",
            "-a",
            "-F",
            f"#{{pane_id}}{TAB}#{{@zish-pane-label-active}}{TAB}#{{@zish-pane-label-inactive}}",
        ]
    )
    old_values: dict[str, tuple[str, str]] = {}
    for line in output.splitlines():
        if not line:
            continue
        pane_id, _, rest = line.partition(TAB)
        active, _, inactive = rest.partition(TAB)
        if pane_id:
            old_values[pane_id] = (active, inactive)
    return old_values


def list_panes() -> dict[str, list[Pane]]:
    fmt = TAB.join(
        [
            "#{window_id}",
            "#{pane_id}",
            "#{pane_active}",
            "#{pane_left}",
            "#{pane_top}",
            "#{pane_width}",
            "#{pane_height}",
            "#{pane_current_command}",
            "#{pane_title}",
            "#{pane_current_path}",
        ]
    )
    output = tmux(["list-panes", "-a", "-F", fmt])
    panes_by_window: dict[str, list[Pane]] = defaultdict(list)

    for line in output.splitlines():
        if not line:
            continue
        parts = line.split(TAB, 9)
        if len(parts) != 10:
            continue

        window_id, pane_id, active, left, top, width, height, command, title, path = parts
        try:
            pane = Pane(
                window_id=window_id,
                pane_id=pane_id,
                active=active == "1",
                left=int(left),
                top=int(top),
                width=int(width),
                height=int(height),
                command=command,
                title=title,
                path=path,
            )
        except ValueError:
            continue

        panes_by_window[window_id].append(pane)

    return panes_by_window


def should_refresh(force: bool) -> bool:
    if force:
        return True

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    stamp_path = STATE_DIR / ".refresh-stamp"
    now = time.time()

    try:
        last = float(stamp_path.read_text().strip())
    except (FileNotFoundError, ValueError):
        last = 0

    if now - last < REFRESH_INTERVAL:
        return False

    stamp_path.write_text(f"{now}\n")
    return True


def refresh(force: bool = False, print_only: bool = False) -> int:
    if not print_only and not should_refresh(force):
        return 0

    old_values = list_old_values()
    old_pane_labels = list_old_pane_labels()
    panes_by_window = list_panes()
    changed = 0

    for window_id, (old_signal, old_icon) in old_values.items():
        panes = panes_by_window.get(window_id, [])
        signal = render_signal(panes)
        icon = render_window_icon(panes)
        if print_only:
            print(f"{window_id}{TAB}{old_signal}{TAB}{signal}{TAB}{old_icon}{TAB}{icon}")
            continue

        if signal != old_signal:
            tmux(["set-window-option", "-q", "-t", window_id, "@pane-layout-signal", signal])
            changed += 1

        if icon != old_icon:
            tmux(["set-window-option", "-q", "-t", window_id, "@pane-window-icon", icon])
            changed += 1

        for pane in panes:
            active_label, inactive_label = render_pane_labels(pane)
            old_active, old_inactive = old_pane_labels.get(pane.pane_id, ("", ""))

            if active_label != old_active:
                tmux(["set-option", "-pq", "-t", pane.pane_id, "@zish-pane-label-active", active_label])
                changed += 1

            if inactive_label != old_inactive:
                tmux(["set-option", "-pq", "-t", pane.pane_id, "@zish-pane-label-inactive", inactive_label])
                changed += 1

    if changed:
        tmux(["refresh-client", "-S"])

    return changed


def run_daemon() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_dir = STATE_DIR / ".refresh-daemon.lock"
    script_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    try:
        lock_dir.mkdir()
    except FileExistsError:
        pid_path = lock_dir / "pid"
        digest_path = lock_dir / "script-sha256"
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 0)
            old_digest = digest_path.read_text().strip() if digest_path.exists() else ""
            if old_digest == script_digest:
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
    (lock_dir / "script-sha256").write_text(f"{script_digest}\n")

    try:
        while True:
            if subprocess.run(["tmux", "has-session"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
                return 0

            try:
                refresh(force=True)
            except Exception:
                pass

            time.sleep(DAEMON_INTERVAL)
    finally:
        shutil.rmtree(lock_dir, ignore_errors=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Refresh cached tmux pane layout icons.")
    parser.add_argument("--daemon", action="store_true", help="run in the background until tmux exits")
    parser.add_argument("--force", action="store_true", help="skip refresh throttling")
    parser.add_argument("--print", action="store_true", dest="print_only", help="print computed signals without setting tmux options")
    args = parser.parse_args(argv[1:])

    if args.daemon:
        return run_daemon()

    if args.force or args.print_only:
        refresh(force=True, print_only=args.print_only)
        return 0

    refresh()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
