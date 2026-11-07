import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('focus_notes', Path(__file__).with_name('focus_notes.py'))
fn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fn)


class FocusNotesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_config = fn.CONFIG
        fn.CONFIG = self.root / 'config.toml'
        fn.CONFIG.write_text('[[vaults]]\nname="work"\npath=' + repr(str(self.root)) + '\ntodo_folder="Inbox/Todo"\n')
        self.real_week_id = fn.todo.week_id
        self.week = patch.object(fn.todo, 'week_id', side_effect=lambda day=None: self.real_week_id(day) if day else '2026-W44')
        self.week.start()

    def tearDown(self):
        self.week.stop()
        fn.CONFIG = self.old_config
        self.temp.cleanup()

    def test_one_task_both_views_rename_reorder_and_rollover(self):
        first = fn.create('work', 'Review 中文 / design', True)
        second = fn.create('work', 'Write report')
        path = Path(first['file'])
        self.assertEqual(len(fn.snapshot()['tasks']), 2)
        self.assertTrue(fn.find(first['id'])['pinned'])
        text = path.read_text().replace('Review 中文 / design', 'Renamed task')
        lines = text.splitlines()
        rows = [line for line in lines if '<!-- task:' in line]
        path.write_text('# Week\n\n## Inbox\n' + '\n'.join(reversed(rows)) + '\nUser prose [[Project]].\n')
        fn.change(first['id'], 'complete')
        self.assertTrue(fn.find(first['id'])['done'])
        self.assertFalse(fn.find(second['id'])['done'])
        stamp = fn.find(first['id'])['completed_at']
        fn.change(first['id'], 'complete')
        self.assertEqual(fn.find(first['id'])['completed_at'], stamp)
        fn.change(first['id'], 'reopen')
        self.assertIn('User prose [[Project]].', path.read_text())
        with patch.object(fn.todo, 'week_id', side_effect=lambda day=None: self.real_week_id(day) if day else '2026-W45'):
            task = fn.find(first['id'])
            self.assertTrue(task['pinned'])
            self.assertEqual(task['text'], 'Renamed task')
            self.assertIn('2026-W45', task['file'])
            fn.change(first['id'], 'unpin')
            self.assertFalse(fn.find(first['id'])['pinned'])
        self.assertTrue(fn.find(first['id'])['pinned'], 'historical week is untouched')

    def test_requested_time_never_counts_and_retries_do_not_duplicate(self):
        task = fn.create('work', '分析 & 评审?', request_id='create-1')
        same = fn.create('work', '分析 & 评审?', request_id='create-1')
        self.assertEqual(task['id'], same['id'])
        launch = fn.start(task['id'], 50, 'request-1')
        self.assertIn('duration=3000', launch['url'])
        self.assertIn('%26', launch['url'])
        self.assertEqual(fn.start(task['id'], 50, 'request-1')['url'], '')
        result = fn.find(task['id'])
        self.assertEqual((result['minutes'], result['pomodoros'], result['unconfirmed']), (0, 0, 1))
        self.assertEqual(result['started'], '')
        self.assertTrue(result['pinned'])
        path = Path(result['log_file'])
        path.write_text(path.read_text() + '\n## My notes\nPreserve this.\n')
        fn.start(task['id'], 25, 'request-2')
        self.assertEqual(fn.find(task['id'])['unconfirmed'], 2)
        self.assertTrue(path.read_text().endswith('## My notes\nPreserve this.\n'))
        # User confirms the actual session in Markdown, with explicit provenance.
        text = path.read_text().replace('| — | — | 0 | requested | raycast-link |',
            '| 2026-10-31T09:00:00+08:00 | 2026-10-31T09:30:00+08:00 | 25 | completed | manual |', 1)
        path.write_text(text)
        result = fn.find(task['id'])
        self.assertEqual((result['minutes'], result['pomodoros'], result['unconfirmed']), (25, 1, 1))
        with patch.object(fn.todo, 'week_id', side_effect=lambda day=None: self.real_week_id(day) if day else '2026-W45'):
            self.assertEqual(fn.find(task['id'])['minutes'], 25)
        fn.change(task['id'], 'complete')
        with self.assertRaisesRegex(ValueError, 'Reopen'):
            fn.start(task['id'], 25, 'request-3')

    def test_create_in_existing_groups_preserves_headings_and_vault_boundaries(self):
        personal = self.root / 'personal'
        personal.mkdir()
        with fn.CONFIG.open('a') as handle:
            handle.write('[[vaults]]\nname="personal"\npath=' + repr(str(personal)) + '\ntodo_folder="Inbox/Todo"\n')
        work, personal_vault = fn.configured()
        work_path = fn.todo.ensure_week(work)
        personal_path = fn.todo.ensure_week(personal_vault)
        original = ('# Week\n\n## CALLE   \nKeep this prose.\n'
                    '- [ ] Existing <!-- task:work-existing -->\n\n'
                    '### Nested\nChild context.\n\n'
                    '## DM\n- [x] Finished <!-- task:work-done -->\n\n'
                    '## Empty\n\n## CALLE\nRepeated heading prose.\n#not-a-heading\n')
        other = '# Week\n\n## Reading\n\n## CALLE\nPersonal context.\n'
        work_path.write_text(original)
        personal_path.write_text(other)
        meta = fn.snapshot()['vaults']
        self.assertEqual(meta, [
            {'name': 'work', 'sections': ['CALLE', 'Nested', 'DM', 'Empty', 'Inbox']},
            {'name': 'personal', 'sections': ['Reading', 'CALLE', 'Inbox']},
        ])
        for vault, section in [('work', 'Reading'), ('personal', 'Nested')]:
            with self.assertRaisesRegex(ValueError, 'group is no longer'):
                fn.create(vault, 'Wrong vault group', section=section)
        self.assertEqual(work_path.read_text(), original)
        self.assertEqual(personal_path.read_text(), other)
        created = []
        for section in ['CALLE', 'Nested', 'DM', 'Empty']:
            task = fn.create('work', 'New ' + section, request_id='create-' + section, section=section)
            self.assertEqual((task['section'], task['vault']), (section, 'work'))
            created.append(task)
        result = work_path.read_text()
        # Removing only the new rows recovers every existing byte, including heading whitespace.
        for task in created:
            result = result.replace(task['raw'] + '\n', '')
        self.assertEqual(result, original)
        first = created[0]
        self.assertLess(work_path.read_text().index(first['raw']), work_path.read_text().index('### Nested'))
        self.assertEqual(personal_path.read_text(), other)
        mine = fn.create('personal', 'My CALLE task', section='CALLE')
        self.assertEqual((mine['section'], mine['vault'], mine['file']), ('CALLE', 'personal', str(personal_path)))
        default = fn.create('work', 'Inbox default')
        self.assertEqual(default['section'], 'Inbox')
        self.assertEqual(work_path.read_text().count('## Inbox'), 1)
        self.assertEqual(fn.create('work', 'New CALLE', request_id='create-CALLE', section='CALLE')['id'], first['id'])
        self.assertEqual(work_path.read_text().count(first['id']), 1)
        # Retrying after a manual group rename still returns the original task, not a duplicate.
        work_path.write_text(work_path.read_text().replace('## CALLE', '## Renamed'))
        retry = fn.create('work', 'New CALLE', request_id='create-CALLE', section='CALLE')
        self.assertEqual((retry['id'], retry['section']), (first['id'], 'Renamed'))

    def test_invalid_or_removed_group_does_not_write_or_initialize_tasks(self):
        path = fn.todo.ensure_week(fn.configured()[0])
        original = '# Week\n\n## CALLE\n- [ ] No ID yet\n'
        path.write_text(original)
        for section in ['', '  ', None, 'CALLE\n## Injected', 'Inbox\r', 'Inbox\x00', 'Inbox\t',
                        'Inbox\x7f', 'Inbox\u2028Other', 'Inbox <!-- focus -->', 'Missing', 'Reading']:
            with self.subTest(section=section), self.assertRaises(ValueError):
                fn.create('work', 'Must not be created', section=section)
            self.assertEqual(path.read_text(), original)
            self.assertFalse(path.with_suffix('.md.before-unified-focus').exists())
        task = fn.create('work', 'Existing group', section='CALLE')
        path.write_text(path.read_text().replace('## CALLE', '## Renamed'))
        before = path.read_text()
        with self.assertRaisesRegex(ValueError, 'group is no longer'):
            fn.create('work', 'Outdated form selection', section='CALLE')
        self.assertEqual(path.read_text(), before)
        self.assertEqual(fn.find(task['id'])['section'], 'Renamed')

    def test_create_cli_accepts_section(self):
        env = dict(os.environ, TASK_BOARD_CONFIG=str(fn.CONFIG), XDG_CACHE_HOME=str(self.root / 'cache'))
        def cli(*args):
            result = subprocess.run([sys.executable, fn.__file__, *args], env=env,
                                    capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        default = cli('create', '--title', 'CLI default')['result']
        path = Path(default['file'])
        path.write_text(path.read_text() + '\n### CALLE\nProject notes.\n')
        data = cli('create', '--vault', 'work', '--section', 'CALLE', '--title', 'CLI grouped', '--request-id', 'cli-group')
        self.assertEqual(data['result']['section'], 'CALLE')
        self.assertIn('CALLE', data['vaults'][0]['sections'])
        self.assertEqual(cli('create', '--section', 'CALLE', '--title', 'CLI grouped', '--request-id', 'cli-group')['result']['id'], data['result']['id'])

    def test_move_is_one_atomic_write_and_preserves_task_and_history(self):
        task = fn.create('work', 'Move 中文 [Source][r1] @owner #source 📅 2026-11-09', True)
        fn.start(task['id'], 25, 'move-session')
        comment = fn.add_comment(task['id'], 'Keep my comment', 'move-comment')
        fn.change(task['id'], 'complete')
        task = fn.find(task['id'])
        path = Path(task['file'])
        raw = task['raw']
        original = ('# Week\n\n## Before\n- [ ] Before task <!-- task:before-task -->\n\n'
                    '## CALLE\n' + raw + '\n\n## Empty\n\n'
                    '## After\n- [ ] After task <!-- task:after-task -->\n\n'
                    '[r1]: https://example.com/source "Original source"\n')
        path.write_text(original)
        task = fn.find(task['id'])
        comment_path, log_path = Path(comment['file']), Path(task['log_file'])
        comment_bytes, log_bytes = comment_path.read_bytes(), log_path.read_bytes()
        for section in ['After', 'Before', 'Empty']:
            before = path.read_text()
            with patch.object(fn, 'replace', wraps=fn.replace) as replace:
                moved = fn.move(task['id'], section)
                self.assertEqual(replace.call_count, 1)
            current = fn.find(task['id'])
            self.assertEqual((current['section'], current['line']), (section, moved['line']))
            self.assertEqual(path.read_text().replace(raw + '\n', ''), before.replace(raw + '\n', ''))
            for field in ['id', 'raw', 'text', 'done', 'pinned', 'completed_at', 'tags', 'sources',
                          'due', 'comments', 'sessions', 'minutes', 'pomodoros', 'unconfirmed']:
                self.assertEqual(current[field], task[field], field)
            self.assertEqual(path.read_text().count(task['id']), 1)
            with patch.object(fn, 'replace', wraps=fn.replace) as replace:
                fn.move(task['id'], section)
                replace.assert_not_called()
        fn.move(task['id'], 'Inbox')
        self.assertEqual(fn.find(task['id'])['section'], 'Inbox')
        before = path.read_text()
        fn.move(task['id'], 'Inbox')
        self.assertEqual(path.read_text(), before)
        self.assertEqual(before.count('## Inbox'), 1)
        self.assertEqual(comment_path.read_bytes(), comment_bytes)
        self.assertEqual(log_path.read_bytes(), log_bytes)

    def test_move_rejects_unsafe_groups_or_list_context_without_any_write(self):
        vault = fn.configured()[0]
        path = fn.todo.ensure_week(vault)
        personal = self.root / 'personal'
        personal.mkdir()
        with fn.CONFIG.open('a') as handle:
            handle.write('[[vaults]]\nname="personal"\npath=' + repr(str(personal)) + '\ntodo_folder="Inbox/Todo"\n')
        other_path = fn.todo.ensure_week(fn.configured()[1])
        other_path.write_text('# Week\n\n## Personal only\n')
        raw = '- [ ] Move me <!-- task:move-target -->'
        original = '# Week\n\n## CALLE\n' + raw + '\n\n## DM\n- [ ] Uninitialized task\n'
        path.write_text(original)
        for section in ['', None, 'DM\n## Injected', 'DM\x00', 'DM\t', 'DM <!-- focus -->', 'Missing', 'Personal only']:
            with self.subTest(section=section), self.assertRaises(ValueError):
                fn.move('move-target', section)
            self.assertEqual(path.read_text(), original)
        for ident in ['missing-task', None, 'bad id']:
            with self.subTest(ident=ident), self.assertRaises(ValueError):
                fn.move(ident, 'DM')
            self.assertEqual(path.read_text(), original)
        self.assertFalse(path.with_suffix('.md.before-unified-focus').exists(), 'move must not initialize unrelated IDs')
        for row, suffix in [
            ('  ' + raw, '\n'),
            (raw, '\n  - [ ] Child <!-- task:child -->\n'),
            (raw, '\n  Continued details\n'),
            (raw, '\n\nUnclear prose ownership\n'),
        ]:
            text = '# Week\n\n## CALLE\n' + row + suffix + '\n## DM\n'
            path.write_text(text)
            with self.subTest(row=row, suffix=suffix), self.assertRaisesRegex(ValueError, 'Edit Markdown'):
                fn.move('move-target', 'DM')
            self.assertEqual(path.read_text(), text)
        path.write_text(original + raw + '\n')
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            fn.move('move-target', 'DM')
        self.assertEqual(path.read_text(), original + raw + '\n')

    def test_move_uses_latest_id_location_and_cas_keeps_concurrent_edit(self):
        task = fn.create('work', 'Target')
        path = Path(task['file'])
        path.write_text('# Added heading and prose before old line number\n\n' + path.read_text() + '\n## DM\n')
        moved = fn.move(task['id'], 'DM')
        self.assertEqual(fn.find(task['id'])['line'], moved['line'])
        original = path.read_text()
        real_replace = fn.replace
        def concurrent_edit(target, before, after):
            self.assertEqual(before, original)
            self.assertEqual(after.count(task['id']), 1)
            target.write_text(before + '\nConcurrent editor note\n')
            real_replace(target, before, after)
        with patch.object(fn, 'replace', side_effect=concurrent_edit) as replace:
            with self.assertRaisesRegex(ValueError, 'changed in your editor'):
                fn.move(task['id'], 'Inbox')
            replace.assert_called_once()
        self.assertEqual(path.read_text(), original + '\nConcurrent editor note\n')
        self.assertEqual(path.read_text().count(task['id']), 1)

    def test_move_cli_accepts_section_and_retries_without_duplicate(self):
        env = dict(os.environ, TASK_BOARD_CONFIG=str(fn.CONFIG), XDG_CACHE_HOME=str(self.root / 'cache'))
        def cli(*args, check=True):
            return subprocess.run([sys.executable, fn.__file__, *args], env=env,
                                  capture_output=True, text=True, check=check)
        task = json.loads(cli('create', '--title', 'CLI move').stdout)['result']
        path = Path(task['file'])
        path.write_text(path.read_text() + '\n## DM\n')
        data = json.loads(cli('move', '--id', task['id'], '--section', 'DM').stdout)
        self.assertEqual(data['result']['section'], 'DM')
        self.assertEqual(next(t for t in data['tasks'] if t['id'] == task['id'])['section'], 'DM')
        before = path.read_text()
        cli('move', '--id', task['id'], '--section', 'DM')
        self.assertEqual(path.read_text(), before)
        failed = cli('move', '--id', task['id'], '--section', 'Unknown', check=False)
        self.assertEqual(failed.returncode, 1)
        self.assertIn('group is no longer', json.loads(failed.stdout)['error'])
        self.assertEqual(path.read_text(), before)

    def test_move_cli_refresh_is_read_only_and_reports_committed_move_honestly(self):
        env = dict(os.environ, TASK_BOARD_CONFIG=str(fn.CONFIG), XDG_CACHE_HOME=str(self.root / 'cache'))
        def cli(*args):
            result = subprocess.run([sys.executable, fn.__file__, *args], env=env,
                                    capture_output=True, text=True)
            return result.returncode, json.loads(result.stdout)
        code, data = cli('create', '--title', 'CLI target')
        self.assertEqual(code, 0)
        task = data['result']
        path = Path(task['file'])
        raw = task['raw']
        original = '# Week\n\n## Inbox\n' + raw + '\n\n## DM\n'
        path.write_text(original)
        personal = self.root / 'personal'
        personal.mkdir()
        with fn.CONFIG.open('a') as handle:
            handle.write('[[vaults]]\nname="personal"\npath=' + repr(str(personal)) + '\ntodo_folder="Inbox/Todo"\n')
        legacy_path = self.root / 'Inbox/Focus/unmigrated.md'
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy = ('---\ntitle: "Keep legacy"\nfocus_id: untouched-legacy\nstatus: active\n'
                  'created: 2026-10-31T09:00:00+08:00\n---\n# Legacy note\n')
        legacy_path.write_text(legacy)
        code, data = cli('move', '--id', task['id'], '--section', 'DM')
        self.assertEqual(code, 0)
        self.assertEqual(data['result']['section'], 'DM')
        self.assertNotIn('refresh_error', data)
        self.assertEqual([t['id'] for t in data['tasks']], [task['id']])
        self.assertEqual(path.read_text().replace(raw + '\n', ''), original.replace(raw + '\n', ''))
        self.assertFalse((personal / 'Inbox/Todo').exists(), 'read-only refresh must not initialize other vaults')
        self.assertEqual(legacy_path.read_text(), legacy)
        for other_rows in [
            '- [ ] Missing ID\n',
            '- [ ] Duplicate one <!-- task:other-duplicate -->\n- [ ] Duplicate two <!-- task:other-duplicate -->\n',
        ]:
            before = original + '\n## Other\n' + other_rows
            path.write_text(before)
            code, data = cli('move', '--id', task['id'], '--section', 'DM')
            self.assertEqual(code, 0, 'a committed move is successful even if refresh cannot read unrelated tasks')
            self.assertNotIn('error', data)
            self.assertNotIn('tasks', data, 'failed refresh must not masquerade as an empty board')
            self.assertEqual(data['result']['section'], 'DM')
            self.assertIn('Task moved, but board refresh failed:', data['refresh_error'])
            self.assertEqual(path.read_text().replace(raw + '\n', ''), before.replace(raw + '\n', ''))
            self.assertIn(other_rows, path.read_text(), 'unrelated rows must not gain IDs or change')
            self.assertEqual(path.read_text().count(task['id']), 1)
            self.assertFalse(path.with_suffix('.md.before-unified-focus').exists())
        self.assertEqual(legacy_path.read_text(), legacy)

    def test_migrate_legacy_once_preserving_note_and_history(self):
        folder = self.root / 'Inbox/Focus'
        folder.mkdir(parents=True)
        path = folder / 'test--legacy.md'
        text = ('---\ntitle: "test"\nfocus_id: legacy-1\nstatus: active\ncreated: 2026-10-31T09:00:00+08:00\n---\n'
                '# test\n\n[[Project]] notes\n\n' + fn.HEADER + '\n' + fn.RULE + '\n'
                '| old-1 | 2026-10-31T09:00:00+08:00 | 2026-10-31T09:25:00+08:00 | 25 | completed | manual |\n')
        path.write_text(text)
        for _ in range(2):
            self.assertEqual(len(fn.snapshot()['tasks']), 1)
        task = fn.find('legacy-1')
        self.assertTrue(task['pinned'])
        self.assertIn('[Notes]', task['text'])
        self.assertEqual(task['minutes'], 25)
        self.assertEqual(path.read_text(), text)
        fn.change('legacy-1', 'complete')
        with patch.object(fn.todo, 'week_id', side_effect=lambda day=None: self.real_week_id(day) if day else '2026-W45'):
            self.assertEqual(fn.snapshot()['tasks'], [])
        self.assertEqual(path.read_text(), text)

    def test_invalid_and_duplicate_rows_fail_closed(self):
        task = fn.create('work', 'Invalid duration')
        fn.start(task['id'], 25, 'r1')
        path = Path(task['log_file'])
        text = path.read_text().replace('| — | — | 0 | requested | raycast-link |',
            '| 2026-10-31T09:00:00+08:00 | 2026-10-31T09:05:00+08:00 | 25 | completed | manual |')
        path.write_text(text)
        result = fn.snapshot()
        self.assertIn('cannot exceed', result['errors'][0])
        self.assertEqual(result['tasks'][0]['minutes'], 0)
        self.assertTrue(result['tasks'][0]['stats_error'])
        path.write_text(text.replace('| 25 | completed', '| 5 | completed'))
        row = next(line for line in path.read_text().splitlines() if line.startswith('| r1 |'))
        path.write_text(path.read_text() + row + '\n')
        self.assertIn('duplicate', fn.snapshot()['errors'][0])
        self.assertEqual(fn.find(task['id'])['minutes'], 0)

    def test_external_edit_and_duplicate_ids_do_not_clobber(self):
        task = fn.create('work', 'Concurrent editor')
        path = Path(task['file'])
        old = path.read_text()
        path.write_text(old + 'User edit\n')
        with self.assertRaisesRegex(ValueError, 'changed in your editor'):
            fn.replace(path, old, 'wrong replacement')
        self.assertTrue(path.read_text().endswith('User edit\n'))
        path.write_text(path.read_text() + f'- [ ] Duplicate <!-- task:{task["id"]} -->\n')
        with self.assertRaisesRegex(ValueError, 'duplicate task ID'):
            fn.change(task['id'], 'complete')

    def test_comments_roundtrip_retry_rename_and_rollover(self):
        task = fn.create('work', 'Discuss implementation', True)
        before = Path(task['file']).read_text()
        text = '完成第一轮评审 & next?\n\n- [ ] This is comment text, not a task\n## Heading\n> Quoted text\n[[Project]] | https://example.com'
        result = fn.add_comment(task['id'], text, 'comment-1')
        self.assertEqual(result['comment']['text'], text)
        self.assertTrue(result['comment']['created'])
        self.assertEqual(Path(task['file']).read_text(), before)
        self.assertEqual(len(fn.snapshot()['tasks']), 1)
        same = fn.add_comment(task['id'], text, 'comment-1')
        self.assertEqual(result, same)
        fn.add_comment(task['id'], 'Second comment', 'comment-2')
        self.assertEqual(len(fn.find(task['id'])['comments']), 2)
        comment_file = Path(result['file'])
        comment_file.write_text(comment_file.read_text() + '\nMy freeform notes [[Other]].\n')
        fn.add_comment(task['id'], 'Third comment', 'comment-3')
        self.assertIn('My freeform notes [[Other]].', comment_file.read_text())
        path = Path(task['file'])
        path.write_text(before.replace('Discuss implementation', 'Renamed task'))
        with patch.object(fn.todo, 'week_id', side_effect=lambda day=None: self.real_week_id(day) if day else '2026-W45'):
            current = fn.find(task['id'])
            self.assertEqual(current['text'], 'Renamed task')
            self.assertEqual(current['comments'][0]['text'], text)
            self.assertEqual(current['comments_file'], result['file'])
            fn.change(task['id'], 'complete')
            fn.add_comment(task['id'], 'Closed after review', 'comment-4')
            self.assertEqual(len(fn.find(task['id'])['comments']), 4)
        with self.assertRaisesRegex(ValueError, 'different text'):
            fn.add_comment(task['id'], 'Changed retry', 'comment-1')

    def test_comment_cli_uses_utf8_file_and_returns_saved_record(self):
        env = dict(os.environ, TASK_BOARD_CONFIG=str(fn.CONFIG), XDG_CACHE_HOME=str(self.root / 'cache'))
        def cli(*args):
            completed = subprocess.run([sys.executable, fn.__file__, *args], env=env,
                                       capture_output=True, text=True, check=True)
            self.assertTrue(completed.stdout.isascii(), 'transport must survive hs.task chunk boundaries')
            return json.loads(completed.stdout)
        created = cli('create', '--vault', 'work', '--title', 'CLI comments')['result']
        text = '保留原文 `code` $(not-a-command)\n\nSecond line [[Project]]'
        input_file = self.root / 'comment input.txt'
        input_file.write_text(text, encoding='utf-8')
        saved = cli('comment', '--id', created['id'], '--text-file', str(input_file), '--request-id', 'cli-1')['result']
        again = cli('comment', '--id', created['id'], '--text-file', str(input_file), '--request-id', 'cli-1')['result']
        self.assertEqual(saved, again)
        read = cli('comments', '--id', created['id'])
        self.assertEqual(len(read['comments']), 1)
        self.assertEqual(read['comments'][0]['text'], text)
        self.assertEqual(read['file'], saved['file'])

    def test_comments_invalid_input_and_malformed_file_are_not_overwritten(self):
        task = fn.create('work', 'Safe comments')
        for text in ['', '  ', 'x' * 4001, 'bad\x00text']:
            with self.assertRaises(ValueError):
                fn.add_comment(task['id'], text)
        result = fn.add_comment(task['id'], '<script>alert(1)</script>', 'c1')
        path = Path(result['file'])
        invalid = path.read_text().replace('<!-- comment:c1 -->', '<!-- comment:broken id -->')
        path.write_text(invalid)
        self.assertTrue(fn.find(task['id'])['comments_error'])
        with self.assertRaises(ValueError):
            fn.add_comment(task['id'], 'New comment')
        self.assertEqual(path.read_text(), invalid)

    def test_metadata_backup_and_validation(self):
        v = fn.configured()[0]
        path = fn.todo.ensure_week(v)
        original = '# Week\n\n## Project\n- [ ] Existing <!-- feishu:abc --> @tag\n'
        path.write_text(original)
        task = fn.snapshot()['tasks'][0]
        self.assertEqual(path.with_suffix('.md.before-unified-focus').read_text(), original)
        self.assertIn('<!-- feishu:abc -->', path.read_text())
        self.assertEqual(task['text'], 'Existing')
        fn.change(task['id'], 'pin')
        self.assertEqual(path.with_suffix('.md.before-unified-focus').read_text(), original)
        for title in ['', 'a\nb', 'a' * 241, 'bad <!-- task:x -->']:
            with self.assertRaises(ValueError):
                fn.create('work', title)


if __name__ == '__main__':
    unittest.main()
