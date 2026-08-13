#!/usr/bin/env python3

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path


UUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
UUID_RE = re.compile(rf"^{UUID_PATTERN}$")

CODEX_PERMISSION_SWITCHES = {
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "--full-auto",
    "--yolo",
}
CODEX_PERMISSION_LONG_VALUE_FLAGS = {
    "--add-dir",
    "--ask-for-approval",
    "--sandbox",
}
CODEX_PERMISSION_SHORT_VALUE_FLAGS = {"-a", "-s"}
CODEX_CONFIG_FLAGS = {"--config", "-c"}
CODEX_SAFE_CONFIG_KEYS = {
    "model",
    "model_reasoning_effort",
    "model_reasoning_summary",
    "model_verbosity",
}
CODEX_OPAQUE_CONFIG_FLAGS = {"--profile", "-p"}

CLAUDE_PERMISSION_SWITCHES = {
    "--allow-dangerously-skip-permissions",
    "--dangerously-skip-permissions",
}
CLAUDE_PERMISSION_VALUE_FLAGS = {
    "--add-dir",
    "--allowed-tools",
    "--allowedTools",
    "--disallowed-tools",
    "--disallowedTools",
    "--permission-mode",
    "--tools",
}
CLAUDE_VARIADIC_PERMISSION_FLAGS = {
    "--add-dir",
    "--allowed-tools",
    "--allowedTools",
    "--disallowed-tools",
    "--disallowedTools",
    "--tools",
}

CODEX_SAFE_VALUE_FLAGS = {
    "--config",
    "--model",
    "-c",
    "-m",
}
CODEX_DROP_VALUE_FLAGS = {
    "--cd",
    "--disable",
    "--enable",
    "--image",
    "--local-provider",
    "--remote",
    "--remote-auth-token-env",
    "-C",
    "-i",
}
CODEX_SAFE_COMPACT_FLAGS = {"-c", "-m"}
CODEX_DROP_COMPACT_FLAGS = {"-C", "-i"}
CODEX_SAFE_SWITCHES: set[str] = set()
CODEX_DROP_SWITCHES = {
    "--no-alt-screen",
    "--oss",
    "--search",
    "--strict-config",
}
CODEX_RESUME_ONLY_SWITCHES = {"--all", "--include-non-interactive", "--last"}

CLAUDE_SAFE_VALUE_FLAGS = {
    "--effort",
    "--fallback-model",
    "--max-budget-usd",
    "--model",
}
CLAUDE_RESTORE_DROP_VALUE_FLAGS = {
    "--append-system-prompt",
    "--debug-file",
    "--input-format",
    "--json-schema",
    "--name",
    "--output-format",
    "--remote-control-session-name-prefix",
    "--system-prompt",
    "-n",
}
CLAUDE_OPAQUE_CONFIG_FLAGS = {
    "--agent",
    "--agents",
    "--mcp-config",
    "--plugin-dir",
    "--plugin-url",
    "--setting-sources",
    "--settings",
}
CLAUDE_SAFE_SWITCHES: set[str] = set()
CLAUDE_RESTORE_DROP_SWITCHES = {
    "--ax-screen-reader",
    "--bare",
    "--brief",
    "--chrome",
    "--disable-slash-commands",
    "--exclude-dynamic-system-prompt-sections",
    "--ide",
    "--no-chrome",
    "--safe-mode",
    "--strict-mcp-config",
    "--verbose",
}
CLAUDE_RESUME_FLAGS = {"--resume", "-r"}
CLAUDE_DROP_SWITCHES = {
    "--background",
    "--bg",
    "--continue",
    "--fork-session",
    "--no-session-persistence",
    "--print",
    "-c",
    "-p",
}
CLAUDE_DROP_VALUE_FLAGS = {
    "--from-pr",
    "--session-id",
    "--tmux",
    "--worktree",
    "-w",
}


def is_uuid(value: object) -> bool:
    return isinstance(value, str) and UUID_RE.fullmatch(value) is not None


def _validated_absolute_executable(value: str) -> str:
    try:
        absolute = os.path.abspath(value)
    except OSError:
        return ""
    path = Path(absolute)
    if not path.is_file() or not os.access(absolute, os.X_OK):
        return ""
    return absolute


def resolve_executable(executable: str, cwd: str = "", tool: str = "") -> str:
    """Return an absolute executable path without dereferencing stable symlinks."""
    if not executable or "\0" in executable:
        return ""

    expanded = os.path.expanduser(executable)
    if os.path.sep not in expanded:
        discovered = shutil.which(executable)
        if not discovered:
            return ""
        return _validated_absolute_executable(discovered)

    path = Path(expanded)
    if not path.is_absolute():
        try:
            base = Path(cwd) if cwd else Path.cwd()
        except OSError:
            return ""
        path = base / path
    original = _validated_absolute_executable(str(path))
    if not original:
        return ""

    lookup_names = [Path(executable).name]
    if tool and tool not in lookup_names:
        lookup_names.append(tool)
    for name in lookup_names:
        discovered = shutil.which(name)
        if not discovered:
            continue
        stable = _validated_absolute_executable(discovered)
        if not stable:
            continue
        try:
            if os.path.samefile(stable, original):
                return stable
        except OSError:
            continue
    return original


def is_codex_safe_config(value: str) -> bool:
    text = value.lstrip("=")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        return False
    key, separator, config_value = text.partition("=")
    return (
        bool(separator)
        and key.strip() in CODEX_SAFE_CONFIG_KEYS
        and bool(config_value.strip())
    )


def _matches_long_flag(word: str, flags: set[str]) -> bool:
    return any(word.startswith(flag + "=") for flag in flags)


def strip_permission_overrides(words: list[str], tool: str) -> list[str]:
    """Remove per-invocation permission changes while leaving prompts untouched."""
    cleaned: list[str] = []
    index = 0

    while index < len(words):
        word = words[index]
        if word == "--":
            cleaned.extend(words[index:])
            break

        if tool == "codex":
            if word in CODEX_CONFIG_FLAGS:
                if index + 1 < len(words) and is_codex_safe_config(words[index + 1]):
                    cleaned.extend(words[index : index + 2])
                index += 2 if index + 1 < len(words) else 1
                continue
            if word.startswith("--config="):
                if is_codex_safe_config(word.split("=", 1)[1]):
                    cleaned.append(word)
                index += 1
                continue
            if word.startswith("-c") and len(word) > 2:
                if is_codex_safe_config(word[2:]):
                    cleaned.append(word)
                index += 1
                continue
            if word in CODEX_OPAQUE_CONFIG_FLAGS:
                index += 2 if index + 1 < len(words) else 1
                continue
            if word.startswith("--profile=") or (
                word.startswith("-p") and len(word) > 2
            ):
                index += 1
                continue
            if word in CODEX_PERMISSION_SWITCHES or _matches_long_flag(
                word, CODEX_PERMISSION_SWITCHES
            ):
                index += 1
                continue

            if word in CODEX_PERMISSION_LONG_VALUE_FLAGS | CODEX_PERMISSION_SHORT_VALUE_FLAGS:
                index += 2 if index + 1 < len(words) else 1
                continue
            if _matches_long_flag(word, CODEX_PERMISSION_LONG_VALUE_FLAGS):
                index += 1
                continue
            if any(
                word.startswith(flag) and len(word) > len(flag)
                for flag in CODEX_PERMISSION_SHORT_VALUE_FLAGS
            ):
                index += 1
                continue

        elif tool == "claude":
            if word in CLAUDE_OPAQUE_CONFIG_FLAGS:
                index += 2 if index + 1 < len(words) else 1
                continue
            if _matches_long_flag(word, CLAUDE_OPAQUE_CONFIG_FLAGS):
                index += 1
                continue
            if word in CLAUDE_PERMISSION_SWITCHES or _matches_long_flag(
                word, CLAUDE_PERMISSION_SWITCHES
            ):
                index += 1
                continue
            if word in CLAUDE_PERMISSION_VALUE_FLAGS:
                index += 1
                if word in CLAUDE_VARIADIC_PERMISSION_FLAGS:
                    while index < len(words) and not words[index].startswith("-"):
                        index += 1
                elif index < len(words):
                    index += 1
                continue
            if _matches_long_flag(word, CLAUDE_PERMISSION_VALUE_FLAGS):
                index += 1
                continue

        cleaned.append(word)
        index += 1

    return cleaned


def _compact_flag(word: str, flags: set[str]) -> bool:
    return any(word.startswith(flag) and len(word) > len(flag) for flag in flags)


def codex_resume_id(words: list[str]) -> str:
    """Return an ID only from the real resume subcommand, not an option value."""
    index = 1
    while index < len(words):
        word = words[index]
        if word == "--":
            return ""
        if word in (
            CODEX_SAFE_VALUE_FLAGS
            | CODEX_DROP_VALUE_FLAGS
            | CODEX_PERMISSION_LONG_VALUE_FLAGS
            | CODEX_PERMISSION_SHORT_VALUE_FLAGS
            | CODEX_OPAQUE_CONFIG_FLAGS
        ):
            index += 2
            continue
        if _compact_flag(
            word,
            CODEX_SAFE_COMPACT_FLAGS
            | CODEX_DROP_COMPACT_FLAGS
            | CODEX_PERMISSION_SHORT_VALUE_FLAGS
            | {"-p"},
        ):
            index += 1
            continue
        if any(
            word.startswith(flag + "=")
            for flag in CODEX_SAFE_VALUE_FLAGS
            | CODEX_DROP_VALUE_FLAGS
            | CODEX_PERMISSION_LONG_VALUE_FLAGS
            | {"--profile"}
        ):
            index += 1
            continue
        if word in CODEX_SAFE_SWITCHES | CODEX_DROP_SWITCHES | CODEX_PERMISSION_SWITCHES:
            index += 1
            continue
        if word != "resume":
            return ""
        index += 1
        while index < len(words):
            word = words[index]
            if word in CODEX_RESUME_ONLY_SWITCHES:
                index += 1
                continue
            if word in (
                CODEX_SAFE_VALUE_FLAGS
                | CODEX_DROP_VALUE_FLAGS
                | CODEX_PERMISSION_LONG_VALUE_FLAGS
                | CODEX_PERMISSION_SHORT_VALUE_FLAGS
                | CODEX_OPAQUE_CONFIG_FLAGS
            ):
                index += 2
                continue
            if _compact_flag(
                word,
                CODEX_SAFE_COMPACT_FLAGS
                | CODEX_DROP_COMPACT_FLAGS
                | CODEX_PERMISSION_SHORT_VALUE_FLAGS
                | {"-p"},
            ):
                index += 1
                continue
            if any(
                word.startswith(flag + "=")
                for flag in CODEX_SAFE_VALUE_FLAGS
                | CODEX_DROP_VALUE_FLAGS
                | CODEX_PERMISSION_LONG_VALUE_FLAGS
                | {"--profile"}
            ):
                index += 1
                continue
            if word in CODEX_SAFE_SWITCHES | CODEX_DROP_SWITCHES | CODEX_PERMISSION_SWITCHES:
                index += 1
                continue
            return word if is_uuid(word) else ""
        return ""
    return ""


def claude_resume_id(words: list[str]) -> str:
    index = 1
    while index < len(words):
        word = words[index]
        if word == "--":
            return ""
        if word in CLAUDE_RESUME_FLAGS:
            if index + 1 < len(words) and is_uuid(words[index + 1]):
                return words[index + 1]
            index += 1
            continue
        if word.startswith("--resume="):
            value = word.split("=", 1)[1]
            return value if is_uuid(value) else ""
        if word in CLAUDE_OPAQUE_CONFIG_FLAGS:
            index += 2
            continue
        if _matches_long_flag(word, CLAUDE_OPAQUE_CONFIG_FLAGS):
            index += 1
            continue
        if word in CLAUDE_PERMISSION_VALUE_FLAGS:
            index += 1
            if word in CLAUDE_VARIADIC_PERMISSION_FLAGS:
                while index < len(words) and not words[index].startswith("-"):
                    index += 1
            elif index < len(words):
                index += 1
            continue
        if word in (
            CLAUDE_SAFE_VALUE_FLAGS
            | CLAUDE_RESTORE_DROP_VALUE_FLAGS
            | CLAUDE_DROP_VALUE_FLAGS
        ):
            index += 2
            continue
        if any(
            word.startswith(flag + "=")
            for flag in CLAUDE_PERMISSION_VALUE_FLAGS
            | CLAUDE_SAFE_VALUE_FLAGS
            | CLAUDE_RESTORE_DROP_VALUE_FLAGS
            | CLAUDE_DROP_VALUE_FLAGS
        ):
            index += 1
            continue
        if word in (
            CLAUDE_PERMISSION_SWITCHES
            | CLAUDE_SAFE_SWITCHES
            | CLAUDE_RESTORE_DROP_SWITCHES
            | CLAUDE_DROP_SWITCHES
        ):
            index += 1
            continue
        # A positional prompt or unknown option makes the invocation ambiguous.
        return ""
    return ""


def resume_id_from_words(words: list[str], tool: str) -> str:
    return claude_resume_id(words) if tool == "claude" else codex_resume_id(words)


def codex_resume_words(words: list[str], session_id: str) -> list[str]:
    words = strip_permission_overrides(words, "codex")
    executable = words[0] if words else "codex"
    cleaned = [executable]
    index = 1
    seen_resume = False

    while index < len(words):
        word = words[index]
        if word == "--":
            break
        if word in CODEX_SAFE_VALUE_FLAGS:
            if index + 1 < len(words):
                cleaned.extend(words[index : index + 2])
            index += 2
            continue
        if _compact_flag(word, CODEX_SAFE_COMPACT_FLAGS):
            cleaned.append(word)
            index += 1
            continue
        if any(word.startswith(flag + "=") for flag in CODEX_SAFE_VALUE_FLAGS):
            cleaned.append(word)
            index += 1
            continue
        if word in CODEX_DROP_VALUE_FLAGS:
            index += 2 if index + 1 < len(words) else 1
            continue
        if _compact_flag(word, CODEX_DROP_COMPACT_FLAGS):
            index += 1
            continue
        if any(word.startswith(flag + "=") for flag in CODEX_DROP_VALUE_FLAGS):
            index += 1
            continue
        if word in CODEX_SAFE_SWITCHES:
            cleaned.append(word)
            index += 1
            continue
        if word in CODEX_DROP_SWITCHES:
            index += 1
            continue
        if word == "resume" and not seen_resume:
            seen_resume = True
            index += 1
            while index < len(words) and words[index] in CODEX_RESUME_ONLY_SWITCHES:
                index += 1
            if index < len(words) and is_uuid(words[index]):
                index += 1
            continue
        if word in CODEX_RESUME_ONLY_SWITCHES or (seen_resume and is_uuid(word)):
            index += 1
            continue
        if word.startswith("-"):
            index += 1
            continue
        # A positional value here is an old prompt/subcommand. Never replay it.
        break

    return cleaned + ["--yolo", "resume", session_id]


def claude_resume_words(words: list[str], session_id: str) -> list[str]:
    words = strip_permission_overrides(words, "claude")
    executable = words[0] if words else "claude"
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", executable.rsplit("/", 1)[-1]):
        executable = "claude"
    cleaned = [executable]
    index = 1

    while index < len(words):
        word = words[index]
        if word == "--":
            break
        if word in CLAUDE_RESUME_FLAGS:
            index += 1
            if index < len(words) and not words[index].startswith("-"):
                index += 1
            continue
        if word.startswith("--resume="):
            index += 1
            continue
        if word in CLAUDE_DROP_SWITCHES | CLAUDE_RESTORE_DROP_SWITCHES:
            index += 1
            continue
        if word in CLAUDE_DROP_VALUE_FLAGS | CLAUDE_RESTORE_DROP_VALUE_FLAGS:
            index += 2 if index + 1 < len(words) and not words[index + 1].startswith("-") else 1
            continue
        if any(
            word.startswith(flag + "=")
            for flag in CLAUDE_DROP_VALUE_FLAGS | CLAUDE_RESTORE_DROP_VALUE_FLAGS
        ):
            index += 1
            continue
        if word in CLAUDE_SAFE_VALUE_FLAGS:
            if index + 1 < len(words):
                cleaned.extend(words[index : index + 2])
            index += 2
            continue
        if any(word.startswith(flag + "=") for flag in CLAUDE_SAFE_VALUE_FLAGS):
            cleaned.append(word)
            index += 1
            continue
        if word in CLAUDE_SAFE_SWITCHES:
            cleaned.append(word)
            index += 1
            continue
        if word.startswith("-"):
            index += 1
            continue
        # Do not replay an initial or resume-picker prompt.
        break

    return cleaned + ["--dangerously-skip-permissions", "--resume", session_id]


def build_resume_words(words: list[str], tool: str, session_id: str) -> list[str]:
    if not is_uuid(session_id):
        raise ValueError("invalid assistant session id")
    if tool == "claude":
        return claude_resume_words(words, session_id)
    return codex_resume_words(words, session_id)
