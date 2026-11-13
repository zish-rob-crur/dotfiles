import io
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import openai_client as api

SCHEMA = {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name'], 'additionalProperties': False}

class ClientTests(unittest.TestCase):
    def test_env_file_is_data_not_shell(self):
        with tempfile.TemporaryDirectory() as d, patch.object(Path, 'home', return_value=Path(d)), patch.dict(os.environ, {}, clear=True):
            (Path(d) / '.env').write_text('OTHER=ignore\nexport OPENAI_API_KEY_PRO_CREDIT="literal-$(false)" # comment\n')
            self.assertEqual(api.api_key(), 'literal-$(false)')
            with patch.dict(os.environ, {api.KEY_NAME: 'env-wins'}):
                self.assertEqual(api.api_key(), 'env-wins')

    def test_request_schema_usage_and_no_secret_in_logs(self):
        reply = {'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 10},
                 'output': [{'content': [{'type': 'output_text', 'text': '{"name":"ok"}'}]}]}
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {api.KEY_NAME: 'secret-key', 'XDG_CACHE_HOME': d}), patch.object(api.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(reply).encode())) as send:
            result = api.ask('api_key=sk-private\nhello', SCHEMA, state_dir=Path(d), log=logging.getLogger('test'))
            self.assertEqual(result, {'name': 'ok'})
            body = json.loads(send.call_args.args[0].data)
            self.assertEqual(body['model'], 'gpt-6-luna')
            self.assertNotIn('sk-private', body['input'])
            self.assertTrue(body['text']['format']['strict'])
            self.assertFalse(body['store'])
            ledger = json.loads((Path(d)/'tmux-openai/usage.json').read_text())
            self.assertEqual(ledger['requests'], 1)
            self.assertNotIn('secret-key', json.dumps(ledger))

    def test_budget_stops_before_network(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {api.KEY_NAME:'test', 'XDG_CACHE_HOME':d, 'TMUX_OPENAI_DAY_BUDGET':'0'}), patch.object(api.urllib.request, 'urlopen') as send:
            with self.assertRaisesRegex(api.OpenAIError, 'budget'):
                api.ask('hello', SCHEMA, state_dir=Path(d), log=logging.getLogger('test'))
            send.assert_not_called()

    def test_refusal_and_truncation_preserve_error(self):
        for reply in ({'status':'incomplete'}, {'status':'completed', 'output':[{'content':[{'type':'refusal'}]}]}):
            with self.subTest(reply=reply), tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {api.KEY_NAME:'test', 'XDG_CACHE_HOME':d}), patch.object(api.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(reply).encode())):
                with self.assertRaises(api.OpenAIError):
                    api.ask('hello', SCHEMA, state_dir=Path(d), log=logging.getLogger('test'))

    def test_reject_wrong_schema(self):
        for value in ({'name':3}, {'name':'ok','extra':'bad'}, {}):
            with self.assertRaises(api.OpenAIError):
                api.validate(value, SCHEMA)
