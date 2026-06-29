#!/usr/bin/env bash

set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
CODEX_EFFORTS=(low medium high xhigh)
CLAUDE_EFFORTS=(low medium high xhigh max)

message() {
    local text="$1"

    if [[ -n "${TMUX:-}" ]] && command -v tmux >/dev/null 2>&1; then
        tmux display-message "$text" >/dev/null 2>&1 || true
    else
        printf '%s\n' "$text"
    fi
}

fail() {
    local text="$1"

    printf '%s: %s\n' "$SCRIPT_NAME" "$text" >&2
    message "$text"
    sleep 2
    exit 1
}

require_command() {
    local command="$1"

    command -v "$command" >/dev/null 2>&1 || fail "$command is not installed"
}

shell_join() {
    local result="" arg quoted

    for arg in "$@"; do
        printf -v quoted '%q' "$arg"
        result="${result}${result:+ }${quoted}"
    done

    printf '%s' "$result"
}

keep_open_shell_command() {
    local tool="$1"
    shift

    local shell command inner
    shell="${SHELL:-/bin/zsh}"
    [[ -x "$shell" ]] || shell="/bin/zsh"

    command="$(shell_join "$@")"
    inner="${command}; status=\$?; printf '\\n[${tool} exited with status %s; shell refreshed]\\n' \"\$status\"; exec \"\${SHELL:-/bin/zsh}\" -l"

    printf 'exec %s -lic %s' "$(shell_join "$shell")" "$(shell_join "$inner")"
}

build_choices() {
    local effort

    for effort in "${CODEX_EFFORTS[@]}"; do
        printf 'codex|%s|popup|%-7s %-6s %s\n' "$effort" "Codex" "$effort" "popup"
    done

    for effort in "${CLAUDE_EFFORTS[@]}"; do
        printf 'claude|%s|popup|%-7s %-6s %s\n' "$effort" "Claude" "$effort" "popup"
    done

    for effort in "${CODEX_EFFORTS[@]}"; do
        printf 'codex|%s|background|%-7s %-6s %s\n' "$effort" "Codex" "$effort" "background task"
    done

    for effort in "${CLAUDE_EFFORTS[@]}"; do
        printf 'claude|%s|background|%-7s %-6s %s\n' "$effort" "Claude" "$effort" "native background"
    done
}

choice_label() {
    local line="$1"

    line="${line#*|}"
    line="${line#*|}"
    line="${line#*|}"
    printf '%s' "$line"
}

choose_with_select() {
    local choices=() line index selected
    local tty="/dev/tty"

    while IFS= read -r line; do
        choices+=("$line")
    done < <(build_choices)

    for index in "${!choices[@]}"; do
        printf '%2d. %s\n' "$((index + 1))" "$(choice_label "${choices[$index]}")" >"$tty"
    done

    printf '\nChoice: ' >"$tty"
    IFS= read -r selected <"$tty"
    [[ "$selected" =~ ^[0-9]+$ ]] || return 1
    ((selected >= 1 && selected <= ${#choices[@]})) || return 1

    printf '%s' "${choices[$((selected - 1))]}"
}

choose_assistant() {
    if command -v fzf >/dev/null 2>&1; then
        build_choices | FZF_DEFAULT_OPTS="" fzf \
            --prompt="Assistant> " \
            --header="Enter launch | Esc cancel | filter: bg codex claude high" \
            --height=100% \
            --layout=reverse \
            --no-info \
            --delimiter='[|]' \
            --with-nth=4
    else
        choose_with_select
    fi
}

run_popup() {
    local tool="$1"
    local effort="$2"

    case "$tool" in
        codex)
            require_command codex
            exec codex --dangerously-bypass-approvals-and-sandbox -c "model_reasoning_effort=\"${effort}\""
            ;;
        claude)
            require_command claude
            exec claude --dangerously-skip-permissions --effort "$effort"
            ;;
        *)
            fail "unknown assistant: $tool"
            ;;
    esac
}

read_background_prompt() {
    local tool="$1"
    local effort="$2"
    local prompt
    local tty="/dev/tty"

    printf '\n%s %s background prompt (empty cancels): ' "$tool" "$effort" >"$tty"
    IFS= read -r prompt <"$tty"

    [[ -n "$prompt" ]] || return 1
    printf '%s' "$prompt"
}

run_codex_background() {
    local effort="$1"
    local prompt="$2"
    local cwd title command

    require_command codex
    require_command tmux

    cwd="$(pwd -P)"
    title="codex-bg-${effort}"
    command="$(keep_open_shell_command "codex" codex exec --dangerously-bypass-approvals-and-sandbox -c "model_reasoning_effort=\"${effort}\"" "$prompt")"

    tmux new-window -d -n "$title" -c "$cwd" "$command"
    message "Started ${title} in a detached tmux window"
}

run_claude_background() {
    local effort="$1"
    local prompt="$2"
    local output status

    require_command claude

    set +e
    output="$(claude --bg --dangerously-skip-permissions --effort "$effort" "$prompt" 2>&1)"
    status=$?
    set -e

    if [[ $status -ne 0 ]]; then
        printf '%s\n' "$output" >&2
        fail "claude background launch failed"
    fi

    [[ -n "$output" ]] && printf '%s\n' "$output"
    message "Started Claude background agent (${effort})"
    sleep 1
}

run_background() {
    local tool="$1"
    local effort="$2"
    local prompt

    prompt="$(read_background_prompt "$tool" "$effort")" || {
        message "Assistant background launch cancelled"
        exit 0
    }

    case "$tool" in
        codex)
            run_codex_background "$effort" "$prompt"
            ;;
        claude)
            run_claude_background "$effort" "$prompt"
            ;;
        *)
            fail "unknown assistant: $tool"
            ;;
    esac
}

main() {
    local choice tool effort mode _label

    choice="$(choose_assistant)" || exit 0
    IFS='|' read -r tool effort mode _label <<< "$choice"

    case "$mode" in
        popup)
            run_popup "$tool" "$effort"
            ;;
        background)
            run_background "$tool" "$effort"
            ;;
        *)
            fail "unknown launch mode: $mode"
            ;;
    esac
}

main "$@"
