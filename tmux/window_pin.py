#!/usr/bin/env python3
"""Toggle a window-scoped pin shared by linked sessions and clients.

The pin is the window option @focus-pin, so it lives as long as the tmux server.
tmux-resurrect's post-save-all and post-restore-all hooks call --save and
--restore to carry it across a server restart, keyed by session name and
window number, which a restore brings back unchanged.
"""
import argparse
import os
from pathlib import Path
import subprocess

PINS = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local' / 'share'))) / 'tmux' / 'focus-pins.tsv'


def tmux(*args):
    return subprocess.check_output(['tmux', *args], text=True)


def toggle_pin(target=None):
    """Pin or unpin target's window (the current client's window without one)."""
    window = tmux('display-message', '-p', *(['-t', target] if target else []), '#{window_id}').strip()
    if not window.startswith('@') or not window[1:].isdigit():
        raise ValueError('Window is no longer available')
    pinned = tmux('show-options', '-wqv', '-t', window, '@focus-pin').strip() != '1'
    if pinned:
        subprocess.run(['tmux', 'set-option', '-w', '-t', window, '@focus-pin', '1'], check=True)
    else:
        subprocess.run(['tmux', 'set-option', '-wu', '-t', window, '@focus-pin'], check=True)
    return pinned


def save():
    rows = tmux('list-windows', '-a', '-F', '#{session_name}\t#{window_index}\t#{@focus-pin}').splitlines()
    pins = ''.join(f'{session}\t{index}\n' for session, index, pin in (row.split('\t') for row in rows) if pin == '1')
    PINS.parent.mkdir(parents=True, exist_ok=True)
    temp = PINS.with_suffix('.tmp')
    temp.write_text(pins)
    temp.replace(PINS)


def restore():
    if not PINS.exists():
        return
    for line in PINS.read_text().splitlines():
        session, _, index = line.partition('\t')
        subprocess.run(['tmux', 'set-option', '-w', '-t', f'={session}:{index}', '@focus-pin', '1'],
                       stderr=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', default=os.environ.get('TMUX_PANE'))
    parser.add_argument('--client')
    parser.add_argument('--save', action='store_true')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if args.save:
        return save()
    if args.restore:
        return restore()
    try:
        pinned = toggle_pin(args.target)
        command = ['tmux', 'display-message']
        if args.client:
            command += ['-c', args.client]
        subprocess.run(command + ['Pinned current window' if pinned else 'Unpinned current window'], check=True)
    except (subprocess.CalledProcessError, ValueError) as exc:
        parser.exit(1, f'Could not toggle window pin: {exc}\n')


if __name__ == '__main__':
    main()
