#!/usr/bin/env python3
"""Search named tmux actions; destructive actions require confirmation."""
import argparse
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parent
ACTIONS = [
    ('pin', '󰐃  Pin / Unpin current window'),
    ('restart-all', '  Restart all agents — Codex + Claude, all sessions'),
    ('restart-codex', '  Restart Codex agents — all sessions'),
    ('restart-claude', '✳  Restart Claude agents — all sessions'),
    ('browse', '  Browse tmux commands, keybindings and sessions'),
]


def dispatch(action, client, window):
    if action == 'pin':
        subprocess.run(['python3', str(ROOT / 'window_pin.py'), '--target', window, '--client', client], check=True)
    elif action in {'restart-all', 'restart-codex', 'restart-claude'}:
        tool = action.removeprefix('restart-')
        command = shlex.join(['python3', str(ROOT / 'restart-assistant-panes.py'), '--tool', tool])
        label = 'Codex + Claude' if tool == 'all' else tool.title()
        subprocess.run(['tmux', 'confirm-before', '-t', client, '-p',
            f'Restart {label} in ALL sessions? Running work will be interrupted. (y/n)',
            'run-shell -b ' + shlex.quote(command)], check=True)
    elif action == 'browse':
        subprocess.run(['tmux', 'run-shell', '-b',
            shlex.quote(str(Path.home() / '.tmux/plugins/tmux-fzf/main.sh'))], check=True)
    else:
        raise ValueError('Unknown palette action')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', required=True)
    parser.add_argument('--window', required=True)
    args = parser.parse_args()
    result = subprocess.run(['fzf-tmux', '-p', '-w', '70%', '-h', '40%', '-x', 'C', '-y', 'C', '--',
        '--layout=reverse', '--border=rounded', '--border-label= Commands ', '--border-label-pos=0', '--padding=1,2', '--no-multi', '--no-preview', '--delimiter=\t', '--with-nth=2',
        '--prompt=Commands > ', '--header=Type to search · Enter: run · Esc: cancel'],
        input=''.join(f'{key}\t{label}\n' for key, label in ACTIONS), text=True, capture_output=True)
    if result.returncode in (1, 130):
        return
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Could not open command palette')
    selection = result.stdout.strip()
    if selection:
        dispatch(selection.split('\t', 1)[0], args.client, args.window)


if __name__ == '__main__':
    main()
