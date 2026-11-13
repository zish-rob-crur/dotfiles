import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from tmux_panes import tool_of
from test_codex_session_titles import LAYOUT
from test_codex_window_badges_refresh import identify
spec = importlib.util.spec_from_file_location('agy_hook', HERE/'agy-hook.py')
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

class AgyTests(unittest.TestCase):
    def setUp(self):
        self.options = {'@agy-conversation':'conversation-1'}
        self.env = {'TMUX':'/tmp/socket,100,0','TMUX_PANE':'%1'}
        self.command = 'agy'
        self.meta = ['main','@1','1','test','%1','/tmp/socket','100','200']
        def tmux(socket, *args):
            if args[0] == 'display-message':
                return '\t'.join(self.meta+[self.command])
            if args[0] == 'show-options':
                return self.options.get(args[-1], '')
            if args[0] == 'set-option':
                self.options[args[-2]] = args[-1]
                return ''
            self.fail(args)
        self.tmux = patch.object(hook, 'tmux', side_effect=tmux).start()
        self.write = patch.object(hook, 'write_completion', return_value=True).start()
        self.ack = patch.object(hook, 'acknowledge').start()
        self.notify = patch.object(hook, 'notify').start()
        self.addCleanup(patch.stopall)
        self.payload = {'conversationId':'conversation-1','fullyIdle':True,'terminationReason':'NO_TOOL_CALL','executionNum':1,'workspacePaths':['/work']}

    def test_recognition_and_icon(self):
        self.assertEqual(tool_of('agy','agy'), 'agy')
        self.assertEqual(identify('agy','agy'), 'agy')
        self.assertEqual(LAYOUT.icon_for_command('/local/bin/agy'), LAYOUT.AGY_ICON)
        self.assertNotEqual(LAYOUT.AGY_ICON, LAYOUT.CODEX_ICON)
        self.assertEqual(tool_of('agython',''), '')

    def test_documented_and_observed_success_reasons(self):
        for reason in ['model_stop', 'NO_TOOL_CALL']:
            self.assertEqual(hook.event_state('Stop', {**self.payload, 'terminationReason':reason}), 'done')
        self.assertEqual(hook.event_state('Stop', {**self.payload, 'terminationReason':'max_steps_exceeded'}), 'idle')

    def test_start_clears_old_unread_and_marks_working(self):
        self.assertTrue(hook.handle('PreInvocation', self.payload, self.env))
        self.ack.assert_called_once()
        self.assertEqual(self.options['@agy-state'],'working')
        self.notify.assert_not_called()

    def test_completion_is_written_and_notified_only_once(self):
        self.assertTrue(hook.handle('Stop',self.payload,self.env))
        self.assertFalse(hook.handle('Stop',self.payload,self.env))
        self.write.assert_called_once()
        self.assertEqual(self.write.call_args.args[3], 'agy')
        self.assertEqual(self.write.call_args.args[2]['session_id'], 'conversation-1')
        self.notify.assert_called_once()
        self.assertEqual(self.options['@agy-state'],'')

    def test_new_turn_can_reuse_execution_number_after_resume(self):
        hook.handle('Stop', self.payload, self.env)
        hook.handle('PreInvocation', self.payload, self.env)
        self.assertTrue(hook.handle('Stop', self.payload, self.env))
        self.assertEqual(self.notify.call_count, 2)

    def test_background_and_error_are_not_completed(self):
        for changes, state in [({'fullyIdle':False},'working'), ({'error':'failed'},'errored'), ({'terminationReason':'cancelled'},'')]:
            hook.handle('Stop',{**self.payload,**changes},self.env)
            self.assertEqual(self.options['@agy-state'],state)
        self.write.assert_not_called()
        self.notify.assert_not_called()

    def test_stale_pane_session_and_ide_are_ignored(self):
        for changes in [{'conversationId':'other'}, {'conversationId':''}]:
            self.assertFalse(hook.handle('Stop',{**self.payload,**changes},self.env))
        self.command='zsh'
        self.assertFalse(hook.handle('Stop',self.payload,self.env))
        self.command='agy'
        self.meta[6]='999'
        self.assertFalse(hook.handle('Stop',self.payload,self.env))
        self.assertFalse(hook.handle('Stop',self.payload,{}))
        self.write.assert_not_called()
        self.notify.assert_not_called()

    def test_installer_preserves_other_hooks_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'hooks.json'
            other={'orca-status':{'Stop':[{'command':'existing'}]}}
            path.write_text(json.dumps(other))
            hook.install(path)
            once=json.loads(path.read_text())
            hook.install(path)
            self.assertEqual(json.loads(path.read_text()),once)
            self.assertEqual(once['orca-status'],other['orca-status'])
            self.assertEqual(set(once['dotfiles-tmux']),{'PreInvocation','Stop'})
