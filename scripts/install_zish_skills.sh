#!/usr/bin/env bash
set -euo pipefail

dry_run="${DRY_RUN:-0}"
if [[ ${1:-} == --dry-run ]]; then
    dry_run=1
    shift
fi
[[ $# == 0 ]] || { echo "Usage: install_zish_skills.sh [--dry-run]" >&2; exit 2; }

repo="$HOME/GitHubRepos/zish-rob-crur/zish-skills"
if [[ ! -e "$repo" && ! -L "$repo" ]]; then
    remote=https://github.com/zish-rob-crur/zish-skills.git
    echo "Clone $remote into $repo"
    if [[ "$dry_run" == 1 ]]; then
        echo "Would then link skills/*/SKILL.md directories for Codex, Claude Code and agy"
        exit 0
    fi
    mkdir -p "$(dirname "$repo")"
    git clone "$remote" "$repo"
fi
if [[ ! -e "$repo/.git" ]] || ! git -C "$repo" rev-parse --show-toplevel >/dev/null; then
    echo "Expected a Git checkout at $repo" >&2
    exit 1
fi

manifests=("$repo"/skills/*/SKILL.md)
[[ -f "${manifests[0]}" ]] || { echo "No skills found under $repo/skills" >&2; exit 1; }

directories=("$HOME/.agents/skills" "$HOME/.claude/skills")
agy_skills="$HOME/.gemini/antigravity-cli/skills"
if [[ ! "$agy_skills" -ef "${directories[0]}" && ! "$agy_skills" -ef "${directories[1]}" ]]; then
    directories+=("$agy_skills")
fi
for directory in "${directories[@]}"; do
    for manifest in "${manifests[@]}"; do
        source_dir="${manifest%/SKILL.md}"
        target="$directory/${source_dir##*/}"
        [[ "$target" -ef "$source_dir" ]] && continue
        echo "Link $target -> $source_dir"
        if [[ "$dry_run" == 1 ]]; then
            if [[ -e "$target" || -L "$target" ]]; then
                echo "Would back up $target under ${directory}-backups"
            fi
            continue
        fi
        mkdir -p "$directory"
        if [[ -e "$target" || -L "$target" ]]; then
            mkdir -p "${directory}-backups"
            backup=$(mktemp -d "${directory}-backups/$(date +%Y%m%d%H%M%S).XXXXXX")
            mv "$target" "$backup/"
            echo "Backed up $target to $backup/"
        fi
        ln -s "$source_dir" "$target"
    done
done
