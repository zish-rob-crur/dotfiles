"""Small Responses API client for tmux background summaries and naming.

Uses only the standard library; credentials never reach subprocess arguments.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_MODEL = os.environ.get('TMUX_OPENAI_MODEL', 'gpt-6-luna')
KEY_NAME = 'OPENAI_API_KEY_PRO_CREDIT'
MAX_OUTPUT = 4096


class OpenAIError(Exception):
    pass


def api_key() -> str:
    value = os.environ.get(KEY_NAME, '').strip()
    if value:
        return value
    try:
        lines = (Path.home() / '.env').read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        match = re.match(r'^\s*(?:export\s+)?' + KEY_NAME + r'\s*=\s*(.*?)\s*$', line)
        if match:
            value = match[1]
            if value.startswith(('"', "'")):
                quote = value[0]
                end = value.find(quote, 1)
                value = value[1:end] if end >= 1 else ''
            else:
                value = value.split(' #', 1)[0].strip()
            if value:
                return value
    raise OpenAIError(f'Missing {KEY_NAME} in environment or ~/.env')


def clean_text(text: str) -> str:
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', text)
    text = re.sub(r'(?im)((?:api[_-]?key|access[_-]?token|password|authorization)\s*[:=]\s*)\S+', r'\1[REDACTED]', text)
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)


def validate(value, schema: dict) -> None:
    kind = schema.get('type')
    if kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['properties']):
            raise OpenAIError('Invalid structured response object')
        for key, spec in schema['properties'].items():
            validate(value[key], spec)
    elif kind == 'array':
        if not isinstance(value, list):
            raise OpenAIError('Invalid structured response array')
        for item in value:
            validate(item, schema['items'])
    elif kind == 'string' and not isinstance(value, str):
        raise OpenAIError('Invalid structured response string')


def ask(prompt: str, schema: dict, *, state_dir: Path, log: logging.Logger,
        model: str = DEFAULT_MODEL, timeout: int = 30, label: str = '') -> dict:
    key = api_key()
    state_dir.mkdir(parents=True, exist_ok=True)
    # Shared accounting across the board and the standalone namer.
    usage_dir = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'tmux-openai'
    usage_dir.mkdir(parents=True, exist_ok=True)
    with (usage_dir / 'usage.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OpenAIError('OpenAI background request already in progress') from None
        return _request(prompt, schema, key, model, timeout, label, log, usage_dir)


def _request(prompt, schema, key, model, timeout, label, log, usage_dir):
    usage_path = usage_dir / 'usage.json'
    try:
        usage = json.loads(usage_path.read_text())
    except FileNotFoundError:
        usage = {}
    except (OSError, ValueError):
        raise OpenAIError('Cannot read OpenAI usage ledger') from None
    day, month = time.strftime('%Y-%m-%d'), time.strftime('%Y-%m')
    if usage.get('month') != month:
        usage = {'month': month, 'month_usd': 0, 'requests': 0}
    if usage.get('day') != day:
        usage.update(day=day, day_usd=0)
    body = json.dumps({
        'model': model, 'input': clean_text(prompt), 'store': False,
        'reasoning': {'effort': 'none'}, 'max_output_tokens': MAX_OUTPUT,
        'text': {'format': {'type': 'json_schema', 'name': 'tmux_result', 'strict': True, 'schema': schema}},
    }).encode()
    # Conservative reservation (UTF-8 bytes upper-bound ordinary text tokens).
    input_rate = float(os.environ.get('TMUX_OPENAI_INPUT_USD_PER_M', '0.1'))
    output_rate = float(os.environ.get('TMUX_OPENAI_OUTPUT_USD_PER_M', '0.5'))
    reserve = (len(body) * input_rate * 1.25 + MAX_OUTPUT * output_rate) / 1_000_000
    if (usage['day_usd'] + reserve > float(os.environ.get('TMUX_OPENAI_DAY_BUDGET', '1')) or
            usage['month_usd'] + reserve > float(os.environ.get('TMUX_OPENAI_MONTH_BUDGET', '20'))):
        raise OpenAIError('OpenAI local budget reached; cached summaries remain available')
    usage['day_usd'] += reserve
    usage['month_usd'] += reserve
    usage['requests'] += 1

    def save():
        tmp = usage_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(usage, indent=2))
        tmp.replace(usage_path)

    save()  # Unknown outcomes retain their reservation, including process interruption.
    request = urllib.request.Request('https://api.openai.com/v1/responses', data=body,
                                    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            reply = json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not log response bodies: they may contain request data or credentials.
        raise OpenAIError(f'OpenAI HTTP {exc.code}; check credentials, model access or quota') from None
    except (OSError, ValueError):
        raise OpenAIError('OpenAI network timeout or invalid response') from None
    counts = reply.get('usage', {})
    if 'input_tokens' in counts and 'output_tokens' in counts:
        # Includes reasoning tokens; conservatively charges cached input at full rate.
        cost = (counts['input_tokens'] * input_rate * 1.25 + counts['output_tokens'] * output_rate) / 1_000_000
        usage['day_usd'] += cost - reserve
        usage['month_usd'] += cost - reserve
        usage.update(last_model=model, last_usage=counts, last_seconds=round(time.monotonic() - started, 3))
        save()
    log.info('openai model=%s seconds=%.2f input=%s output=%s', model, time.monotonic() - started,
             counts.get('input_tokens'), counts.get('output_tokens'))
    if reply.get('status') != 'completed':
        raise OpenAIError('OpenAI response incomplete; previous summary retained')
    texts = [c.get('text', '') for item in reply.get('output', []) for c in item.get('content', [])
             if c.get('type') == 'output_text']
    try:
        value = json.loads(''.join(texts))
    except ValueError:
        raise OpenAIError('OpenAI returned no structured result') from None
    validate(value, schema)
    return value
