"""Shared interactive-shell startup for verified assistant resumes."""
import math
import os
from pathlib import Path
import shlex


def start_interval():
    try:
        value = float(os.environ.get('TMUX_ASSISTANT_START_INTERVAL', '3'))
        if math.isfinite(value) and value >= 0:
            return value
    except ValueError:
        pass
    return 3.0


def shell_command(words, cwd, tool, delay=0):
    if tool not in {'codex', 'claude'}:
        raise ValueError('Unsupported assistant')
    shell = os.environ.get('SHELL', '')
    if Path(shell).name not in {'bash', 'zsh'} or not Path(shell).is_file():
        shell = next((path for path in ('/bin/zsh', '/bin/bash') if Path(path).is_file()), '')
    if not shell:
        raise RuntimeError('No interactive bash/zsh shell found')
    workdir = str(Path(cwd).expanduser()) if cwd else str(Path.home())
    # Keep the literal command name so the interactive shell expands the user's
    # alias/function. Only the already-verified resume arguments are carried over.
    command = shlex.join([tool, *words[1:]])
    inner = (f'cd {shlex.quote(workdir)} 2>/dev/null || cd; '
             f'{command}; agent_exit_code=$?; '
             f'printf \'\\n[{tool} exited with status %s]\\n\' "$agent_exit_code"; '
             f'exec {shlex.quote(shell)} -il')
    launch = f'exec {shlex.quote(shell)} -lic {shlex.quote(inner)}'
    # Delay before rc files load as shell initialization can also be expensive.
    return (f'sleep {delay:g}; ' if delay > 0 else '') + launch
