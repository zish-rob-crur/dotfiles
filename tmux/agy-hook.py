#!/usr/bin/env python3
"""Antigravity lifecycle -> tmux status and Ghostty completion notification."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

from assistant_completion_state import acknowledge, write_completion

HERE = Path(__file__).resolve().parent
STATE_ROOT = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home()/'.cache'))) / 'codex-tmux-status'


def tmux(socket, *args):
    return subprocess.check_output(['tmux', '-S', socket, *args], text=True,
                                   stderr=subprocess.DEVNULL, timeout=2).strip()


def event_state(event, payload):
    if event == 'PreInvocation':
        return 'working'
    if event != 'Stop':
        return None
    if payload.get('fullyIdle') is not True:
        return 'working'
    if payload.get('error') or payload.get('terminationReason') == 'error':
        return 'errored'
    return 'done' if payload.get('terminationReason') in ('model_stop', 'NO_TOOL_CALL') else 'idle'


def notify(payload):
    spec = importlib.util.spec_from_file_location('ghostty_notify', HERE/'codex-notify-ghostty.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = module.tmux_context()
    title = 'Antigravity 完成'
    task = module.truncate(module.collapse(str(payload.get('lastUserInput', '任务完成'))), 110)
    location = module.tmux_label(context)
    message = module.truncate(' · '.join(filter(None, [title, location, task])), 180)
    if module.is_ghostty(context.get('client_termname', ''), context.get('client_termtype', '')):
        if module.send_osc9(context.get('client_tty', ''), message):
            return
    module.send_terminal_notifier(title, task, location, 'agy-'+payload['conversationId'])


def handle(event, payload, environment=None):
    env = os.environ if environment is None else environment
    state = event_state(event, payload)
    conversation = payload.get('conversationId')
    if state is None or not isinstance(conversation, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', conversation):
        return False
    inherited = env.get('TMUX', '').split(',', 2)
    pane = env.get('TMUX_PANE', '')
    if len(inherited) < 2 or not inherited[1].isdigit() or not re.fullmatch(r'%\d+', pane):
        return False
    socket = inherited[0]
    fmt = '\t'.join(['#{session_name}','#{window_id}','#{window_index}','#{window_name}',
                     '#{pane_id}','#{socket_path}','#{pid}','#{start_time}','#{pane_current_command}'])
    fields = tmux(socket, 'display-message', '-p', '-t', pane, fmt).split('\t')
    if len(fields) != 9 or fields[6] != inherited[1] or fields[4] != pane or fields[8] != 'agy':
        return False  # Never attribute an IDE hook or a stale pane to a CLI conversation.
    current = tmux(socket, 'show-options', '-pqv', '-t', pane, '@agy-conversation')
    if event == 'PreInvocation':
        tmux(socket, 'set-option', '-pq', '-t', pane, '@agy-conversation', conversation)
        tmux(socket, 'set-option', '-pq', '-t', pane, '@agy-last-completion', '')
        acknowledge(STATE_ROOT, pane, socket)
    elif current and current != conversation:
        return False
    if event == 'Stop' and current != conversation:
        return False  # A start hook must establish identity first.
    event_key = f"{conversation}:{payload.get('executionNum')}"
    if state == 'done':
        if not isinstance(payload.get('executionNum'), int):
            return False
        if tmux(socket, 'show-options', '-pqv', '-t', pane, '@agy-last-completion') == event_key:
            return False
        paths = payload.get('workspacePaths') or []
        if not write_completion(STATE_ROOT, '\t'.join(fields[:8]),
                                {'session_id': conversation, 'cwd': paths[0] if paths else ''}, 'agy', time.time_ns()):
            return False
        tmux(socket, 'set-option', '-pq', '-t', pane, '@agy-last-completion', event_key)
    tmux(socket, 'set-option', '-pq', '-t', pane, '@agy-state', '' if state in ('done','idle') else state)
    if state == 'done':
        notify(payload)
    return True


def install(path):
    data = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError('Expected a JSON object in hooks.json')
    original = json.dumps(data, sort_keys=True)
    command = shlex.join([sys.executable, str(HERE/'agy-hook.py')])
    data['dotfiles-tmux'] = {event: [{'type':'command','command':command+' '+event,'timeout':10}]
                            for event in ('PreInvocation','Stop')}
    if json.dumps(data, sort_keys=True) == original:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(path.name+'.backup-'+str(time.time_ns()))
        backup.write_bytes(path.read_bytes())
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('event', nargs='?', choices=['PreInvocation','Stop'])
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    if args.install:
        install(Path.home()/'.gemini/config/hooks.json')
        print('Installed dotfiles-tmux hooks; other hooks preserved.')
        return
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            handle(args.event, payload)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass  # A notification failure must never interrupt the agent.
    print(json.dumps({'decision':'allow'} if args.event == 'Stop' else {}))


if __name__ == '__main__':
    main()
