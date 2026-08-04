#!/usr/bin/env bash

set -euo pipefail
umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STATE_HELPER="${SCRIPT_DIR}/assistant_completion_state.py"
STATE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/codex-tmux-status"
SERVER_STATE_DIR=""
TMUX_SOCKET=""
REFRESH_INTERVAL="${CODEX_TMUX_BADGE_REFRESH_INTERVAL:-2}"
DAEMON_INTERVAL="${CODEX_TMUX_BADGE_DAEMON_INTERVAL:-2}"
GC_INTERVAL="${CODEX_TMUX_BADGE_GC_INTERVAL:-60}"
RUN_COLOR="#0969DA"
RUN_MUTED_COLOR="#8C959F"
WAIT_COLOR="#BF8700"
DONE_COLOR="#1A7F37"
ERROR_COLOR="#CF222E"
TRACE_FILE="${CODEX_TMUX_BADGE_TRACE_FILE:-}"
FORCE_REFRESH=0
ACK_CHANGED=0
TARGET_WINDOW=""
MODE="${1:-}"

state_path_for_pane() {
  printf '%s/pane-%s.json' "${SERVER_STATE_DIR}" "${1#%}"
}

tmux_for_socket() {
  if [[ -n "${TMUX_SOCKET}" ]]; then
    tmux -S "${TMUX_SOCKET}" "$@"
  else
    tmux "$@"
  fi
}

ensure_state_root() {
  mkdir -p "${STATE_ROOT}"
  chmod 700 "${STATE_ROOT}" 2>/dev/null || true
}

resolve_server_state_dir() {
  [[ -n "${SERVER_STATE_DIR}" ]] && return 0
  [[ -f "${STATE_HELPER}" ]] || return 1

  ensure_state_root
  if [[ -z "${TMUX_SOCKET}" ]]; then
    TMUX_SOCKET=$(tmux display-message -p "#{socket_path}" 2>/dev/null || true)
    if [[ -z "${TMUX_SOCKET}" ]]; then
      TMUX_SOCKET=${TMUX:-}
      TMUX_SOCKET=${TMUX_SOCKET%%,*}
    fi
  fi
  [[ -n "${TMUX_SOCKET}" ]] || return 1

  SERVER_STATE_DIR=$(python3 "${STATE_HELPER}" \
    --state-dir "${STATE_ROOT}" \
    --tmux-socket "${TMUX_SOCKET}" \
    --resolve-state-dir 2>/dev/null || true)
  [[ -n "${SERVER_STATE_DIR}" ]] || return 1
  if [[ -n "${CODEX_TMUX_EXPECTED_SERVER_STATE_DIR:-}" ]] &&
    [[ "${SERVER_STATE_DIR}" != "${CODEX_TMUX_EXPECTED_SERVER_STATE_DIR}" ]]; then
    SERVER_STATE_DIR=""
    return 1
  fi
  mkdir -p "${SERVER_STATE_DIR}"
  chmod 700 "${SERVER_STATE_DIR}" 2>/dev/null || true
}

trace_event() {
  [[ -n "${TRACE_FILE}" ]] || return 0
  printf '%s\t%s\t%s\n' "${1:-event}" "$$" "${2:-}" >> "${TRACE_FILE}"
  chmod 600 "${TRACE_FILE}" 2>/dev/null || true
}

run_with_render_lock() {
  local lock_mode internal_mode lock_path script_path
  lock_mode=$1
  internal_mode=$2
  shift 2

  resolve_server_state_dir || return 0
  lock_path="${SERVER_STATE_DIR}/refresh-render.lock"
  script_path=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/$(basename -- "$0")

  # Hold the generation-scoped advisory lock across exec. Ordinary refreshes
  # are best-effort; focus acknowledgment blocks so ack + redraw is atomic
  # with respect to an older renderer that already loaded unread state.
  exec python3 - "${script_path}" "${lock_path}" "${lock_mode}" \
    "${internal_mode}" "$@" <<'PY'
import fcntl
import os
import sys


script, lock_path, lock_mode, internal_mode = sys.argv[1:5]
arguments = sys.argv[5:]
lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
os.fchmod(lock_fd, 0o600)
operation = fcntl.LOCK_EX
if lock_mode == "nonblocking":
    operation |= fcntl.LOCK_NB
try:
    fcntl.flock(lock_fd, operation)
except (BlockingIOError, OSError):
    os.close(lock_fd)
    raise SystemExit(0)

os.set_inheritable(lock_fd, True)
environment = os.environ.copy()
environment["CODEX_TMUX_RENDER_LOCK_HELD"] = "1"
environment["CODEX_TMUX_EXPECTED_SERVER_STATE_DIR"] = os.path.dirname(lock_path)
os.execve(
    "/bin/bash",
    ["bash", script, internal_mode] + arguments,
    environment,
)
PY
}

pane_is_focused() {
  local pane_id clients focused_pane flags
  pane_id=${1:-}
  [[ "${pane_id}" =~ ^%[0-9]+$ ]] || return 1

  if ! clients=$(tmux_for_socket list-clients -F "#{pane_id}"$'\t'"#{client_flags}" 2>/dev/null); then
    return 1
  fi
  while IFS=$'\t' read -r focused_pane flags; do
    [[ "${focused_pane}" == "${pane_id}" ]] || continue
    case ",${flags}," in
      *,focused,*) return 0 ;;
    esac
  done <<< "${clients}"
  return 1
}

server_identity_matches() {
  local expected_pid expected_start expected_socket current
  local current_pid current_start current_socket
  expected_pid=${1:-}
  expected_start=${2:-}
  expected_socket=${3:-}
  [[ "${expected_pid}" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "${expected_start}" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ -n "${expected_socket}" ]] || return 1
  [[ "${TMUX_SOCKET}" == "${expected_socket}" ]] || return 1

  if ! current=$(tmux_for_socket display-message -p \
    "#{pid}"$'\t'"#{start_time}"$'\t'"#{socket_path}" 2>/dev/null); then
    return 1
  fi
  IFS=$'\t' read -r current_pid current_start current_socket <<< "${current}"
  [[ "${current_pid}" == "${expected_pid}" ]] || return 1
  [[ "${current_start}" == "${expected_start}" ]] || return 1
  [[ "${current_socket}" == "${expected_socket}" ]] || return 1
}

agent_tool() {
  local command title
  command=${1:-}
  title=${2:-}

  case "${command}" in
    codex|codex-*) printf 'codex'; return 0 ;;
    claude|claude-*) printf 'claude'; return 0 ;;
  esac

  if [[ "${command}" =~ ^[0-9]+[.][0-9]+[.][0-9]+$ ]]; then
    if [[ "${title}" == "✳ "* ]] || is_running_title "${title}"; then
      printf 'claude'
      return 0
    fi
  fi

  return 1
}

is_running_title() {
  case "${1:-}" in
    "⠋ "*|"⠙ "*|"⠹ "*|"⠸ "*|"⠼ "*|"⠴ "*|"⠦ "*|"⠧ "*|"⠇ "*|"⠏ "*) return 0 ;;
    *) return 1 ;;
  esac
}

has_token() {
  local haystack needle
  haystack=$1
  needle=$2

  case "${haystack}" in
    *"|${needle}|"*) return 0 ;;
    *) return 1 ;;
  esac
}

append_unique_token() {
  local haystack needle
  haystack=$1
  needle=$2

  if has_token "${haystack}" "${needle}"; then
    printf '%s' "${haystack}"
  else
    printf '%s%s|' "${haystack}" "${needle}"
  fi
}

detect_pane_status() {
  local tool content line
  local -a lines
  local detected_status
  tool=${1:-}
  content=${2:-}
  lines=()
  detected_status=""

  # Status controls live at the bottom of both TUIs. Keeping the scope small
  # avoids treating quoted prompts or errors from older turns as current.
  while IFS= read -r line; do
    lines+=("${line}")
    if (( ${#lines[@]} > 16 )); then
      lines=("${lines[@]:1}")
    fi
  done <<< "${content}"

  for line in "${lines[@]}"; do
    case "${tool}" in
      codex)
        if [[ "${line}" == *"You've hit your usage limit"* ]] ||
          [[ "${line}" =~ ^[[:space:]]*■.*[Ee][Rr][Rr][Oo][Rr] ]]; then
          detected_status="errored"
        elif [[ "${line}" =~ ^[[:space:]]*›[[:space:]]+[0-9]+\. ]] ||
          [[ "${line}" =~ Press[[:space:]]enter[[:space:]]to[[:space:]](confirm|continue) ]] ||
          [[ "${line}" =~ enter[[:space:]]to[[:space:]]submit[[:space:]]answer ]]; then
          detected_status="waiting"
        elif [[ "${line}" =~ ^[[:space:]]*•.*esc[[:space:]]to[[:space:]]interrupt ]]; then
          detected_status="working"
        fi
        ;;
      claude)
        if [[ "${line}" =~ ^[[:space:]]*[Ee][Rr][Rr][Oo][Rr]: ]]; then
          detected_status="errored"
        elif [[ "${line}" == *"Enter to confirm"* ]] ||
          [[ "${line}" =~ ^[[:space:]]*❯[[:space:] ]+[0-9]+\. ]]; then
          detected_status="waiting"
        elif [[ "${line}" =~ ^[[:space:]]*[✻✳✶✽✢·✦✧+*][[:space:]]+[^[:space:]]+…[[:space:]]+\( ]] ||
          [[ "${line}" =~ ^[[:space:]]*[✻✳✶✽✢·✦✧+*].*esc[[:space:]]to[[:space:]]interrupt ]]; then
          detected_status="working"
        fi
        ;;
    esac
  done

  # The lowest matching control is the current one: for example, a permission
  # dialog can be rendered below a still-visible "esc to interrupt" line.
  printf '%s' "${detected_status}"
}

acknowledge_pane() {
  local pane_id result
  pane_id=${1:-}
  [[ "${pane_id}" =~ ^%[0-9]+$ ]] || return 0
  [[ -f "${STATE_HELPER}" ]] || return 0

  resolve_server_state_dir || return 0
  result=$(python3 "${STATE_HELPER}" \
    --state-dir "${STATE_ROOT}" \
    --tmux-socket "${TMUX_SOCKET}" \
    --expected-state-dir "${SERVER_STATE_DIR}" \
    --ack-pane "${pane_id}" \
    --require-focused 2>/dev/null || true)
  if [[ "${result}" == "changed" ]]; then
    ACK_CHANGED=1
  else
    ACK_CHANGED=0
  fi
  return 0
}

load_unread_panes() {
  local pane_id
  unread_panes="|"
  resolve_server_state_dir || return 0

  while IFS= read -r pane_id; do
    [[ "${pane_id}" =~ ^%[0-9]+$ ]] || continue
    unread_panes=$(append_unique_token "${unread_panes}" "${pane_id}")
  done < <(SERVER_STATE_DIR="${SERVER_STATE_DIR}" python3 - <<'PY'
import json
import os
from pathlib import Path


state_dir = Path(os.environ["SERVER_STATE_DIR"])
for path in state_dir.glob("pane-*.json"):
    try:
        os.chmod(str(path), 0o600)
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        continue
    if isinstance(payload, dict) and payload.get("unread") is True:
        pane_num = path.name.removeprefix("pane-").removesuffix(".json")
        if pane_num.isdigit():
            print(f"%{pane_num}")
PY
  )
}

record_captured_pane() {
  local window_id pane_id tool running_title pane_content pane_status state_path
  window_id=$1
  pane_id=$2
  tool=$3
  running_title=$4
  pane_content=$5

  pane_status=$(detect_pane_status "${tool}" "${pane_content}")
  if [[ -z "${pane_status}" && "${running_title}" == "1" ]]; then
    pane_status="working"
  fi

  case "${pane_status}" in
    errored) errored_windows=$(append_unique_token "${errored_windows}" "${window_id}") ;;
    waiting) waiting_windows=$(append_unique_token "${waiting_windows}" "${window_id}") ;;
    working) running_windows=$(append_unique_token "${running_windows}" "${window_id}") ;;
  esac

  if [[ -z "${pane_status}" ]]; then
    state_path=$(state_path_for_pane "${pane_id}")
    if [[ -f "${state_path}" ]] && has_token "${unread_panes}" "${pane_id}"; then
      done_windows=$(append_unique_token "${done_windows}" "${window_id}")
    fi
  fi
}

old_badge_for_window() {
  local old_badges window_id line id value
  old_badges=$1
  window_id=$2

  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    id=${line%%$'\t'*}
    value=${line#*$'\t'}
    if [[ "${id}" == "${window_id}" ]]; then
      printf '%s' "${value}"
      return 0
    fi
  done <<EOF
${old_badges}
EOF
}

should_refresh() {
  local stamp_path now last

  resolve_server_state_dir || return 1

  if [[ "${FORCE_REFRESH}" == "1" ]]; then
    return 0
  fi

  stamp_path="${SERVER_STATE_DIR}/.refresh-stamp"
  now=$(date +%s)
  last=0
  if [[ -f "${stamp_path}" ]]; then
    IFS= read -r last < "${stamp_path}" || last=0
  fi
  [[ ${last} =~ ^[0-9]+$ ]] || last=0
  if (( now - last < REFRESH_INTERVAL )); then
    return 1
  fi
  printf '%s\n' "${now}" > "${stamp_path}"
}

render_running() {
  local phase dot_color

  phase=$(( ($(date +%s) / 2) % 2 ))
  dot_color="${RUN_COLOR}"

  if [[ ${phase} -eq 1 ]]; then
    dot_color="${RUN_MUTED_COLOR}"
  fi

  printf ' #[push-default]#[fg=%s,bold]●#[pop-default]' "${dot_color}"
}

render_waiting() {
  printf ' #[push-default]#[fg=%s,bold]◆#[pop-default]' "${WAIT_COLOR}"
}

render_done() {
  printf ' #[push-default]#[fg=%s,bold]󰄬#[pop-default]' "${DONE_COLOR}"
}

render_errored() {
  printf ' #[push-default]#[fg=%s,bold]×#[pop-default]' "${ERROR_COLOR}"
}

gc_stale_state_files() {
  local current_panes pane_listing path pane_num pane_id stamp_path now last suffix
  local valid_pane_count

  resolve_server_state_dir || return 0
  [[ -d "${SERVER_STATE_DIR}" ]] || return 0

  stamp_path="${SERVER_STATE_DIR}/.gc-stamp"
  now=$(date +%s)
  last=0
  if [[ -f "${stamp_path}" ]]; then
    IFS= read -r last < "${stamp_path}" || last=0
  fi
  [[ ${last} =~ ^[0-9]+$ ]] || last=0
  if (( now - last < GC_INTERVAL )); then
    return 0
  fi

  # A transient tmux/socket error must never be interpreted as "all panes
  # disappeared". An active tmux server also cannot legitimately expose an
  # empty global pane set, so both failure and empty/invalid output preserve
  # every durable resume state and retry on the next daemon round.
  if ! pane_listing=$(tmux_for_socket list-panes -a -F "#{pane_id}" 2>/dev/null); then
    return 0
  fi

  current_panes="|"
  valid_pane_count=0
  while IFS= read -r pane_id; do
    [[ "${pane_id}" =~ ^%[0-9]+$ ]] || continue
    if ! has_token "${current_panes}" "${pane_id}"; then
      current_panes=$(append_unique_token "${current_panes}" "${pane_id}")
      valid_pane_count=$(( valid_pane_count + 1 ))
    fi
  done <<< "${pane_listing}"
  (( valid_pane_count > 0 )) || return 0

  printf '%s\n' "${now}" > "${stamp_path}"

  for suffix in json ack; do
    for path in "${SERVER_STATE_DIR}"/pane-*."${suffix}"; do
      [[ -e "${path}" ]] || break
      pane_num=${path##*/pane-}
      pane_num=${pane_num%.${suffix}}
      pane_id="%${pane_num}"
      has_token "${current_panes}" "${pane_id}" || rm -f "${path}"
    done
  done
}

list_windows_for_refresh() {
  if [[ -n "${TARGET_WINDOW}" ]]; then
    tmux_for_socket display-message -p -t "${TARGET_WINDOW}" \
      "#{window_id}"$'\t'"#{@codex-badge}" 2>/dev/null || true
  else
    tmux_for_socket list-windows -a -F \
      "#{window_id}"$'\t'"#{@codex-badge}" 2>/dev/null || true
  fi
}

list_panes_for_refresh() {
  if [[ -n "${TARGET_WINDOW}" ]]; then
    tmux_for_socket list-panes -t "${TARGET_WINDOW}" -F \
      "#{window_id}"$'\t'"#{pane_id}"$'\t'"#{pane_current_command}"$'\t'"#{pane_title}" \
      2>/dev/null || true
  else
    tmux_for_socket list-panes -a -F \
      "#{window_id}"$'\t'"#{pane_id}"$'\t'"#{pane_current_command}"$'\t'"#{pane_title}" \
      2>/dev/null || true
  fi
}

main() {
  local windows old_badges errored_windows waiting_windows running_windows done_windows
  local seen_windows seen_panes
  local window_id old_badge pane_id pane_cmd pane_title badge tool running_title pane_content

  resolve_server_state_dir || return 0
  if [[ -z "${TARGET_WINDOW}" ]]; then
    should_refresh || return 0
    gc_stale_state_files
  fi
  load_unread_panes

  windows=$'\n'
  old_badges=""
  seen_windows="|"
  seen_panes="|"
  errored_windows="|"
  waiting_windows="|"
  running_windows="|"
  done_windows="|"

  while IFS=$'\t' read -r window_id old_badge; do
    [[ -n "${window_id}" ]] || continue
    has_token "${seen_windows}" "${window_id}" && continue
    seen_windows=$(append_unique_token "${seen_windows}" "${window_id}")
    trace_event window "${window_id}"
    windows="${windows}${window_id}"$'\n'
    old_badges="${old_badges}${window_id}"$'\t'"${old_badge}"$'\n'
  done < <(list_windows_for_refresh)

  while IFS=$'\t' read -r window_id pane_id pane_cmd pane_title; do
    [[ -n "${window_id}" && -n "${pane_id}" ]] || continue
    has_token "${seen_panes}" "${pane_id}" && continue
    seen_panes=$(append_unique_token "${seen_panes}" "${pane_id}")
    tool=$(agent_tool "${pane_cmd}" "${pane_title}" || true)
    [[ -n "${tool}" ]] || continue

    running_title=0
    if is_running_title "${pane_title}"; then
      running_title=1
    fi
    trace_event capture "${pane_id}"
    pane_content=$(tmux_for_socket capture-pane -p -J -t "${pane_id}" 2>/dev/null || true)
    record_captured_pane \
      "${window_id}" "${pane_id}" "${tool}" "${running_title}" "${pane_content}"
  done < <(list_panes_for_refresh)

  # Freeze the socket inode generation through the expensive scan. A server
  # can restart at the same path while capture-pane is running; never apply a
  # badge computed from the old server to reused window ids on the new one.
  if [[ -n "${CODEX_TMUX_EXPECTED_SERVER_STATE_DIR:-}" ]]; then
    SERVER_STATE_DIR=""
    resolve_server_state_dir || return 0
  fi

  while IFS= read -r window_id; do
    [[ -n "${window_id}" ]] || continue

    badge=""
    if has_token "${errored_windows}" "${window_id}"; then
      badge=$(render_errored)
    elif has_token "${waiting_windows}" "${window_id}"; then
      badge=$(render_waiting)
    elif has_token "${running_windows}" "${window_id}"; then
      badge=$(render_running)
    elif has_token "${done_windows}" "${window_id}"; then
      badge=$(render_done)
    fi

    old_badge=$(old_badge_for_window "${old_badges}" "${window_id}")
    if [[ "${old_badge}" != "${badge}" ]]; then
      tmux_for_socket set-window-option -q -t "${window_id}" @codex-badge "${badge}" >/dev/null 2>&1 || true
    fi
  done <<EOF
${windows}
EOF
}

refresh_pane_window() {
  local pane_id
  pane_id=${1:-}
  TARGET_WINDOW=$(tmux_for_socket display-message -p -t "${pane_id}" "#{window_id}" 2>/dev/null || true)
  [[ "${TARGET_WINDOW}" =~ ^@[0-9]+$ ]] || return 0
  main
}

run_daemon() {
  local server_id script_path

  ensure_state_root
  script_path=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/$(basename -- "$0")
  server_id=$(tmux_for_socket display-message -p "#{socket_path}" 2>/dev/null || true)
  if [[ -z "${server_id}" ]]; then
    server_id=${TMUX:-}
    server_id=${server_id%%,*}
  fi
  [[ -n "${server_id}" ]] || return 0

  # The Python supervisor keeps a kernel advisory lock open for its whole
  # lifetime. Unlike deleting a mkdir lock, this cannot remove a successor's
  # lock during takeover. The metadata owner token only gets cleared by the
  # process that wrote it.
  exec python3 - "${script_path}" "${STATE_ROOT}" "${server_id}" \
    --badge-daemon-supervisor <<'PY'
import fcntl
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path


command_script = os.path.abspath(sys.argv[1])
script = str(Path(command_script).resolve())
state_dir = Path(sys.argv[2])
raw_server_id = sys.argv[3].strip()
server_id = os.path.realpath(raw_server_id)
state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(str(state_dir), 0o700)

digest = hashlib.sha256(Path(script).read_bytes()).hexdigest()
server_key = hashlib.sha256(server_id.encode("utf-8")).hexdigest()[:16]
lock_path = state_dir / f".refresh-daemon-{server_key}.lock"
lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
os.chmod(str(lock_path), 0o600)


def read_metadata():
    try:
        os.lseek(lock_fd, 0, os.SEEK_SET)
        raw = os.read(lock_fd, 65536).decode("utf-8")
        value = json.loads(raw) if raw.strip() else {}
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def process_command(pid):
    proc_path = Path(f"/proc/{pid}/cmdline")
    try:
        if proc_path.is_file():
            return proc_path.read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
        return subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def process_start(pid):
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        if stat_path.is_file():
            return stat_path.read_text(encoding="utf-8").rsplit(") ", 1)[1].split()[19]
        return subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "lstart="],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError, IndexError):
        return ""


def verified_owner(metadata):
    try:
        pid = int(metadata["pid"])
    except (KeyError, TypeError, ValueError):
        return None
    if metadata.get("script") != script or metadata.get("server_id") != server_id:
        return None
    if not metadata.get("owner_token"):
        return None
    if metadata.get("process_start") != process_start(pid):
        return None
    command = process_command(pid)
    visible_script_paths = {script, command_script}
    if script.startswith("/private/var/"):
        visible_script_paths.add(script.removeprefix("/private"))
    if not any(path in command for path in visible_script_paths) or (
        "--badge-daemon-supervisor" not in command
    ):
        return None
    return pid


def try_lock():
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, OSError):
        return False


if not try_lock():
    previous = read_metadata()
    owner_pid = verified_owner(previous)
    if owner_pid is None:
        raise SystemExit(0)
    if previous.get("digest") != digest:
        try:
            os.kill(owner_pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    # Same-version launchers normally observe a healthy owner for the whole
    # bounded wait and exit. If that owner has already received SIGTERM (or is
    # naturally releasing the lock), one waiter becomes its successor instead
    # of both processes exiting and leaving a zero-owner gap.
    for _ in range(60):
        if try_lock():
            break
        time.sleep(0.05)
    else:
        raise SystemExit(0)

owner_token = secrets.token_hex(16)
metadata = {
    "pid": os.getpid(),
    "owner_token": owner_token,
    "process_start": process_start(os.getpid()),
    "script": script,
    "digest": digest,
    "server_id": server_id,
    "stopping": False,
}


def publish_metadata(stopping=False):
    metadata["stopping"] = bool(stopping)
    encoded = (json.dumps(metadata, separators=(",", ":")) + "\n").encode("utf-8")
    os.ftruncate(lock_fd, 0)
    os.lseek(lock_fd, 0, os.SEEK_SET)
    os.write(lock_fd, encoded)
    os.fsync(lock_fd)


publish_metadata()


def legacy_environment_matches(pid):
    environ_path = Path(f"/proc/{pid}/environ")
    try:
        if environ_path.is_file():
            values = environ_path.read_bytes().split(b"\0")
            for value in values:
                if not value.startswith(b"TMUX="):
                    continue
                socket = value[5:].split(b",", 1)[0].decode("utf-8", errors="replace")
                if os.path.realpath(socket) == server_id:
                    return True
            return False
        command = subprocess.check_output(
            ["ps", "eww", "-p", str(pid), "-o", "command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return (
            f" TMUX={raw_server_id}," in command
            or f" TMUX={server_id}," in command
        )
    except (OSError, subprocess.CalledProcessError):
        return False


def retire_legacy_daemons():
    """One-time migration from the old removable-directory lock."""
    try:
        listing = subprocess.check_output(
            ["ps", "-axo", "pid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return
    expected = f"bash {script} --daemon"
    for line in listing.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        pid = int(fields[0])
        if pid == os.getpid() or not fields[1].startswith(expected):
            continue
        if legacy_environment_matches(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass


stop_requested = False
active_child = None


def terminate_child_group(child, grace_seconds=1):
    """Terminate the whole renderer session, including orphaned descendants."""

    try:
        os.killpg(child.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        child.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass

    # The bash group leader may exit while a descendant still holds the
    # inherited render-lock descriptor. Always retire the remaining process
    # group before starting another render round.
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        child.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass


def request_stop(_signum, _frame):
    global stop_requested, active_child
    stop_requested = True
    publish_metadata(stopping=True)
    if active_child is not None and active_child.poll() is None:
        try:
            os.killpg(active_child.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass


signal.signal(signal.SIGINT, request_stop)
signal.signal(signal.SIGTERM, request_stop)
retire_legacy_daemons()

interval = float(os.environ.get("CODEX_TMUX_BADGE_DAEMON_INTERVAL", "2"))
max_loops = int(os.environ.get("CODEX_TMUX_BADGE_DAEMON_MAX_LOOPS", "0"))
configured_render_timeout = os.environ.get("CODEX_TMUX_BADGE_RENDER_TIMEOUT", "")
render_timeout = (
    max(0.1, float(configured_render_timeout))
    if configured_render_timeout
    else max(5.0, interval * 3.0)
)
loops = 0
try:
    while not stop_requested:
        try:
            active = subprocess.run(
                ["tmux", "-S", raw_server_id, "has-session"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=1,
            )
        except subprocess.TimeoutExpired:
            # A temporarily wedged tmux query is not evidence that the server
            # disappeared. Keep ownership and retry instead of creating a
            # zero-daemon gap.
            time.sleep(min(max(interval, 0.05), 0.5))
            continue
        if active.returncode != 0:
            break
        active_child = subprocess.Popen(
            ["bash", script, "--force"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        child_deadline = time.monotonic() + render_timeout
        while (
            active_child.poll() is None
            and not stop_requested
            and time.monotonic() < child_deadline
        ):
            try:
                active_child.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        if active_child.poll() is None:
            terminate_child_group(active_child)
        active_child = None
        loops += 1
        if max_loops > 0 and loops >= max_loops:
            break
        deadline = time.monotonic() + interval
        while not stop_requested and time.monotonic() < deadline:
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
finally:
    if active_child is not None:
        terminate_child_group(active_child)
    # Cleanup is guarded by the token and happens while the kernel lock is
    # still held, so a terminating owner cannot erase successor metadata.
    current = read_metadata()
    if current.get("owner_token") == owner_token:
        os.ftruncate(lock_fd, 0)
        os.lseek(lock_fd, 0, os.SEEK_SET)
        os.write(lock_fd, b"{}\n")
        os.fsync(lock_fd)
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
PY
}

case "${MODE}" in
  --ack-pane)
    TMUX_SOCKET=${5:-}
    server_identity_matches "${3:-}" "${4:-}" "${5:-}" || exit 0
    run_with_render_lock blocking --ack-render-locked \
      "${2:-}" "${3:-}" "${4:-}" "${5:-}"
    ;;
  --ack-render-locked)
    [[ "${CODEX_TMUX_RENDER_LOCK_HELD:-}" == "1" ]] || exit 0
    TMUX_SOCKET=${5:-}
    server_identity_matches "${3:-}" "${4:-}" "${5:-}" || exit 0
    resolve_server_state_dir || exit 0
    if pane_is_focused "${2:-}"; then
      acknowledge_pane "${2:-}"
      # The socket path may have been reused after the ack helper returned.
      # Re-resolve its inode generation under the frozen render-lock namespace
      # before addressing a potentially reused pane/window id in tmux.
      SERVER_STATE_DIR=""
      resolve_server_state_dir || exit 0
      server_identity_matches "${3:-}" "${4:-}" "${5:-}" || exit 0
      refresh_pane_window "${2:-}"
      tmux_for_socket refresh-client -S >/dev/null 2>&1 || true
    fi
    ;;
  --identify)
    agent_tool "${2:-}" "${3:-}" || true
    ;;
  --detect)
    detect_pane_status "${2:-}" "$(cat)"
    ;;
  --daemon)
    run_daemon
    ;;
  --force)
    run_with_render_lock nonblocking --render-force-locked
    ;;
  --render-force-locked)
    [[ "${CODEX_TMUX_RENDER_LOCK_HELD:-}" == "1" ]] || exit 0
    FORCE_REFRESH=1
    main
    ;;
  --render-locked)
    [[ "${CODEX_TMUX_RENDER_LOCK_HELD:-}" == "1" ]] || exit 0
    main
    ;;
  *)
    run_with_render_lock nonblocking --render-locked
    ;;
esac
