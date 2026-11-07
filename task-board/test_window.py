"""Real WKWebView/NSWindow checks through the helper's pipes; no UI automation.

Run after scripts/install_task_board.sh on macOS. Windows appear without focus.
"""
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXE = Path.home() / ".local/share/task-board/Task Board.app/Contents/MacOS/task-board-window"


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.process = subprocess.Popen([str(EXE)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.events = queue.Queue()
        def read_events():
            for line in self.process.stdout:
                self.events.put(json.loads(line))
        self.reader = threading.Thread(target=read_events, daemon=True)
        self.reader.start()
        self.wait_for("started")

    def tearDown(self):
        if not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            self.fail("window process survived supervisor EOF")
        finally:
            self.reader.join(timeout=1)
            self.process.stdout.close()
            self.process.stderr.close()
        self.assertEqual(self.process.returncode, 0)

    def wait_for(self, event, predicate=lambda e: True):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            item = self.events.get(timeout=max(.01, deadline - time.monotonic()))
            self.assertNotEqual(item["event"], "error", item)
            if item["event"] == event and predicate(item):
                return item
        self.fail(f"missing event: {event}")

    def send(self, message, fragmented=False):
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode()
        if fragmented:
            # Includes splits inside UTF-8 characters and a > pipe-buffer page.
            for offset in range(0, len(data), 113):
                self.process.stdin.write(data[offset:offset + 113])
                self.process.stdin.flush()
        else:
            self.process.stdin.write(data)
            self.process.stdin.flush()

    def test_standard_window_and_existing_page(self):
        source = (ROOT / "hammerspoon/task_board.lua").read_text()
        html = source.split("local PAGE = [[\n", 1)[1].split("\n]]", 1)[0]
        frame = {"x": 45, "y": 55, "w": 640, "h": 700}
        self.send({"command": "load", "html": html, "frame": frame}, fragmented=True)
        self.assertEqual(self.wait_for("frame")["frame"], frame)
        self.wait_for("message", lambda e: e["body"].get("ready"))
        self.send({"command": "update", "board": {"windows": [], "todos": [
            {"text": "标准窗口 🐈", "vault": "test", "line": 1, "tags": [], "sources": [], "done": False}
        ]}})
        self.wait_for("rendered")
        self.send({"command": "inspect"})
        status = self.wait_for("status")
        self.assertEqual(status["class"], "NSWindow")
        self.assertEqual(status["subrole"], "AXStandardWindow")
        self.assertEqual(status["level"], 0)
        self.assertTrue(status["visible"])
        self.assertTrue(status["pageReady"])
        self.assertEqual(status["page"], {"cards": 0, "todos": 1, "hasUpdate": True})
        # A subsequent snapshot must replace the old DOM, not accumulate rows.
        self.send({"command": "update", "board": {"windows": [], "todos": []}})
        self.wait_for("rendered")
        self.send({"command": "inspect"})
        self.assertEqual(self.wait_for("status")["page"]["todos"], 0)

    def test_task_drawer_sources_and_completion_in_webkit(self):
        source = (ROOT / "hammerspoon/task_board.lua").read_text()
        html = source.split("local PAGE = [[\n", 1)[1].split("\n]]", 1)[0]
        html += """<script>
        const updatePage = window.update;
        const sentActions = [];
        const sendMessage = webkit.messageHandlers.board.postMessage.bind(webkit.messageHandlers.board);
        webkit.messageHandlers.board.postMessage = body => {
          if (body.focus) sentActions.push(body.focus);
          sendMessage(body);
        };
        window.update = data => {
          updatePage(data);
          const report = {};
          if (data.phase === 'open') {
            const opener = document.querySelector('.todos .task-title');
            const columnWidth = document.querySelector('.todo-group').getBoundingClientRect().width;
            opener.click();
            const drawer = document.getElementById('task-drawer');
            report.titleOnlyOpens = !drawer.hidden && sentActions.length === 0;
            report.singleDrawer = document.querySelectorAll('#task-drawer:not([hidden])').length;
            report.labelled = drawer.getAttribute('role') === 'dialog' && !!document.getElementById(drawer.getAttribute('aria-labelledby'));
            report.sources = [...document.querySelectorAll('#drawer-sources a[href]')].map(a => a.getAttribute('href'));
            report.sourcesInitiallyCollapsed = !document.querySelector('#drawer-sources details').open;
            document.querySelector('.todos [data-section="sources"]').click();
            report.sourceShortcutExpands = document.querySelector('#drawer-sources details').open;
            updatePage(data);
            report.sourceExpansionSurvivesRefresh = document.querySelector('#drawer-sources details').open;
            document.getElementById('drawer-close').click();
            report.returnsToSourceShortcut = document.activeElement?.matches('[data-detail="shared-1"][data-section="sources"]');
            updatePage(data);
            report.emptyEditorHasNoDraftBadge = !document.querySelector('.todos').textContent.includes(' · draft');
            document.querySelector('.todos .task-title').click();
            report.compactFocus = !!document.querySelector('#focus-rows .focus-row') && !document.querySelector('#focus-rows .todo, #focus-rows a[href], #focus-rows textarea');
            report.columnNotSqueezed = document.querySelector('.todo-group').getBoundingClientRect().width === columnWidth;
            report.noOverflow = document.documentElement.scrollWidth <= window.innerWidth;
            document.getElementById('drawer-close').dispatchEvent(new KeyboardEvent('keydown', {key:'Escape',bubbles:true}));
            report.escapeCloses = drawer.hidden;
            report.focusReturned = document.activeElement?.matches('.task-title[data-detail="shared-1"]');
            document.querySelector('.todos input[type="checkbox"]').click();
            report.onlyCheckboxMutates = sentActions.length === 1 && sentActions[0].action === 'complete';
          } else {
            report.openRows = document.querySelectorAll('.todo').length;
            document.querySelector('[data-show-completed]').click();
            report.doneRows = document.querySelectorAll('.todo.done').length;
          }
          webkit.messageHandlers.board.postMessage({test: report});
        };
        </script>"""
        self.send({"command": "load", "html": html, "frame": {"x": 45, "y": 55, "w": 480, "h": 700}})
        self.wait_for("message", lambda e: e["body"].get("ready"))
        task = {"id": "shared-1", "text": "Review 中文 task [飞书原消息](https://example.com/a) [飞书原消息](https://example.com/b) [MR !311](https://example.com/mr/311)", "vault": "test", "pinned": True,
                "done": False, "tags": [], "sources": [], "sessions": [], "comments": [], "minutes": 0, "pomodoros": 0}
        self.send({"command": "update", "board": {"windows": [], "todos": [task], "phase": "open"}})
        request = self.wait_for("message", lambda e: "focus" in e["body"])["body"]["focus"]
        self.assertEqual((request["action"], request["id"]), ("complete", "shared-1"))
        report = self.wait_for("message", lambda e: "test" in e["body"])["body"]["test"]
        self.assertEqual(report, {"titleOnlyOpens": True, "singleDrawer": 1, "labelled": True,
            "sources": ["https://example.com/a", "https://example.com/b", "https://example.com/mr/311"],
            "compactFocus": True, "columnNotSqueezed": True, "noOverflow": True,
            "sourcesInitiallyCollapsed": True, "sourceShortcutExpands": True, "sourceExpansionSurvivesRefresh": True,
            "returnsToSourceShortcut": True, "emptyEditorHasNoDraftBadge": True,
            "escapeCloses": True, "focusReturned": True, "onlyCheckboxMutates": True})
        task["done"] = True
        self.send({"command": "update", "board": {"windows": [], "todos": [task], "phase": "done", "focus_ack": {"request_id": request["request_id"]}}})
        report = self.wait_for("message", lambda e: "test" in e["body"])["body"]["test"]
        self.assertEqual(report, {"openRows": 0, "doneRows": 1})

    def test_comments_keep_editor_drafts_and_composition_in_drawer(self):
        source = (ROOT / "hammerspoon/task_board.lua").read_text()
        html = source.split("local PAGE = [[\n", 1)[1].split("\n]]", 1)[0]
        html += """<script>
        const originalUpdate = window.update;
        let commentField;
        const edit = text => {
          commentField.value = text;
          commentField.dispatchEvent(new Event('input', {bubbles:true}));
        };
        const openTask = id => document.querySelector('.todos .task-title[data-detail="' + id + '"]').click();
        window.update = data => {
          originalUpdate(data);
          const report = {};
          if (data.phase === 'write') {
            openTask('comment-task');
            commentField = document.querySelector('#drawer-comments textarea[data-comment]');
            commentField.focus();
            edit('Review completed\\nNext step');
            commentField.dispatchEvent(new CompositionEvent('compositionstart', {bubbles:true}));
            originalUpdate(data);
            report.editorPreserved = document.activeElement === commentField && commentField.isConnected;
            report.draftPreserved = commentField.value === 'Review completed\\nNext step';
            commentField.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape',isComposing:true,bubbles:true}));
            report.imeEscapePreservesDrawer = !document.getElementById('task-drawer').hidden;
            commentField.dispatchEvent(new CompositionEvent('compositionend', {bubbles:true}));
            commentField.closest('form').requestSubmit();
            report.saving = commentField.closest('form').querySelector('button').disabled;
            edit('Newer draft');
          } else if (data.phase === 'ack') {
            report.newerDraftPreserved = commentField.value === 'Newer draft' && commentField.isConnected;
            report.forms = document.querySelectorAll('[data-comment-form]').length;
            report.comments = document.querySelectorAll('#drawer-comments .comment-body').length;
            report.escaped = !document.querySelector('.comment-body img');
            document.getElementById('drawer-close').click();
            openTask('other-task');
            const otherField = document.querySelector('#drawer-comments textarea');
            report.otherDraftEmpty = otherField.value === '';
            otherField.value = 'Other task draft';
            otherField.dispatchEvent(new Event('input', {bubbles:true}));
            openTask('comment-task');
            commentField = document.querySelector('#drawer-comments textarea');
            report.draftAfterSwitch = commentField.value === 'Newer draft';
            commentField.closest('form').requestSubmit();
          } else if (data.phase === 'failure') {
            report.failedDraftPreserved = commentField.value === 'Newer draft';
            report.errorVisible = document.querySelector('#drawer-comments').textContent.includes('Editor conflict');
            commentField.closest('form').requestSubmit();
          } else {
            report.savedDraftCleared = commentField.value === '';
            openTask('other-task');
            report.otherDraftPreserved = document.querySelector('#drawer-comments textarea').value === 'Other task draft';
            report.noOverflow = document.documentElement.scrollWidth <= window.innerWidth;
          }
          webkit.messageHandlers.board.postMessage({test:report});
        };
        </script>"""
        self.send({"command": "load", "html": html, "frame": {"x": 45, "y": 55, "w": 480, "h": 700}})
        self.wait_for("message", lambda e: e["body"].get("ready"))
        task = {"id": "comment-task", "text": "Test comments", "vault": "test", "pinned": True,
                "done": False, "tags": [], "sources": [], "comments": []}
        other_task = {**task, "id": "other-task", "text": "Another task", "pinned": False}
        def update(phase, ack=None):
            self.send({"command": "update", "board": {"windows": [], "todos": [task, other_task], "phase": phase, "focus_ack": ack}})
        update("write")
        request = self.wait_for("message", lambda e: "focus" in e["body"])["body"]["focus"]
        self.assertEqual((request["action"], request["text"]), ("comment", "Review completed\nNext step"))
        report = self.wait_for("message", lambda e: "test" in e["body"])["body"]["test"]
        self.assertEqual(report, {"editorPreserved": True, "draftPreserved": True, "imeEscapePreservesDrawer": True, "saving": True})
        task["comments"] = [{"id": "c1", "created": "2026-10-31T09:00:00+08:00", "text": "<img src=x> 中文"}]
        update("ack", {"request_id": request["request_id"]})
        request = self.wait_for("message", lambda e: "focus" in e["body"])["body"]["focus"]
        self.assertEqual(request["text"], "Newer draft")
        report = self.wait_for("message", lambda e: "test" in e["body"])["body"]["test"]
        self.assertEqual(report, {"newerDraftPreserved": True, "forms": 1, "comments": 1,
                                  "escaped": True, "otherDraftEmpty": True, "draftAfterSwitch": True})
        update("failure", {"request_id": request["request_id"], "error": "Editor conflict"})
        request = self.wait_for("message", lambda e: "focus" in e["body"])["body"]["focus"]
        report = self.wait_for("message", lambda e: "test" in e["body"])["body"]["test"]
        self.assertEqual(report, {"failedDraftPreserved": True, "errorVisible": True})
        update("saved", {"request_id": request["request_id"]})
        report = self.wait_for("message", lambda e: "test" in e["body"])["body"]["test"]
        self.assertEqual(report, {"savedDraftCleared": True, "otherDraftPreserved": True, "noOverflow": True})

    def test_search_and_project_columns_keep_scroll_and_identity(self):
        source = (ROOT / "hammerspoon/task_board.lua").read_text()
        html = source.split("local PAGE = [[\n", 1)[1].split("\n]]", 1)[0]
        html += """<script>
        const updatePage = window.update;
        window.update = data => {
          updatePage(data);
          const columns = document.querySelector('.todos');
          const groups = [...columns.querySelectorAll('.todo-group')];
          const report = {
            groupCount: groups.length,
            sameColumn: groups.every(g => new Set([...g.querySelectorAll('.todo')].map(t => Math.round(t.getBoundingClientRect().left))).size === 1),
            orderedColumns: groups.every((g,i) => !i || g.getBoundingClientRect().left > groups[i-1].getBoundingClientRect().left),
            noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth
          };
          columns.scrollLeft = 120;
          groups[0].scrollTop = 90;
          const left = columns.scrollLeft, top = groups[0].scrollTop;
          updatePage(data);
          report.scrollPreserved = document.querySelector('.todos').scrollLeft === left && document.querySelector('.todo-group').scrollTop === top && left > 0 && top > 0;
          const search = document.getElementById('task-search');
          search.focus();search.value = 'DM needle';search.dispatchEvent(new Event('input'));
          updatePage(data);
          report.searchPreserved = document.activeElement === search && search.value === 'DM needle';
          report.match = [...document.querySelectorAll('.todos .todo')].map(t => t.dataset.task);
          document.querySelector('.todos .todo input').click();
          webkit.messageHandlers.board.postMessage({test:report});
        };
        </script>"""
        self.send({"command": "load", "html": html, "frame": {"x": 45, "y": 55, "w": 480, "h": 700}})
        self.wait_for("message", lambda e: e["body"].get("ready"))
        tasks = [{"id": f"calle-{i}", "text": "Review implementation and follow up", "section": "CALLE", "vault": "work", "done": False} for i in range(20)]
        tasks += [{"id": "dm-target", "text": "Needle in another column", "section": "DM", "vault": "work", "done": False},
                  {"id": "personal-calle", "text": "Read notes", "section": "CALLE", "vault": "personal", "done": False}]
        self.send({"command": "update", "board": {"windows": [], "todos": tasks}})
        request = self.wait_for("message", lambda e: "focus" in e["body"])["body"]["focus"]
        self.assertEqual((request["id"], request["action"]), ("dm-target", "complete"))
        report = self.wait_for("message", lambda e: "test" in e["body"])["body"]["test"]
        self.assertEqual(report, {"groupCount": 3, "sameColumn": True, "orderedColumns": True,
                                  "noPageOverflow": True, "scrollPreserved": True, "searchPreserved": True, "match": ["dm-target"]})

    def test_wide_layout_drawer_refresh_and_tmux_collapse(self):
        source = (ROOT / "hammerspoon/task_board.lua").read_text()
        html = source.split("local PAGE = [[\n", 1)[1].split("\n]]", 1)[0]
        html += """<script>
        const updatePage = window.update;
        window.update = data => {
          updatePage(data);
          const columns = document.querySelector('.todos');
          const groups = [...columns.querySelectorAll('.todo-group')];
          const originalWidths = groups.map(g => g.getBoundingClientRect().width);
          const report = {
            noPageOverflow: document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight,
            taskAreaDominates: columns.getBoundingClientRect().height >= innerHeight * .5,
            tmuxAtBottom: document.querySelector('.tmux-block').getBoundingClientRect().top >= columns.getBoundingClientRect().bottom - 1,
            multipleFocusTasks: document.querySelectorAll('.focus-row').length === 3
          };
          document.querySelector('.todos .task-title').click();
          updatePage(data);
          const drawer = document.getElementById('task-drawer');
          const rect = drawer.getBoundingClientRect();
          report.drawerSurvivesRefresh = !drawer.hidden && drawer.dataset.task === 'layout-0';
          report.drawerFits = rect.left >= 0 && rect.right <= innerWidth && rect.bottom <= innerHeight && rect.width >= 360;
          report.columnsStayReadable = [...document.querySelectorAll('.todo-group')].every((g,i) => g.getBoundingClientRect().width === originalWidths[i] && originalWidths[i] >= 300);
          document.getElementById('drawer-close').click();
          report.focusReturnsAfterRefresh = document.activeElement?.matches('.task-title[data-detail="layout-0"]');
          document.getElementById('tmux-toggle').click();
          updatePage(data);
          report.localExpandSurvivesSnapshot = document.getElementById('tmux-toggle').getAttribute('aria-expanded') === 'true';
          document.getElementById('tmux-toggle').click();
          report.collapses = document.getElementById('tmux-toggle').getAttribute('aria-expanded') === 'false';
          webkit.messageHandlers.board.postMessage({test:report});
        };
        </script>"""
        self.send({"command": "load", "html": html, "frame": {"x": 45, "y": 55, "w": 1200, "h": 900}})
        self.wait_for("message", lambda e: e["body"].get("ready"))
        tasks = [{"id": f"layout-{i}", "text": "Review implementation and follow up", "section": "CALLE" if i < 20 else "DM", "vault": "work", "done": False, "pinned": i < 3} for i in range(30)]
        self.send({"command": "update", "board": {"windows": [], "todos": tasks, "tmux_collapsed": True}})
        report = self.wait_for("message", lambda e: "test" in e["body"])["body"]["test"]
        self.assertEqual(report, {"noPageOverflow": True, "taskAreaDominates": True, "tmuxAtBottom": True,
            "multipleFocusTasks": True, "drawerSurvivesRefresh": True, "drawerFits": True,
            "columnsStayReadable": True, "focusReturnsAfterRefresh": True,
            "localExpandSurvivesSnapshot": True, "collapses": True})

    def test_drawer_pending_actions_and_background_focus(self):
        source = (ROOT / "hammerspoon/task_board.lua").read_text()
        html = source.split("local PAGE = [[\n", 1)[1].split("\n]]", 1)[0]
        html += """<script>
        const updatePage = window.update;
        window.update = data => {
          updatePage(data);
          const drawer = document.getElementById('task-drawer');
          const report = {};
          document.querySelector('.todos .task-title').click();
          if (data.phase === 'start') {
            const button = document.querySelector('#drawer-summary [data-action="start"]');
            button.focus(); button.click();
            report.pendingKeepsUsableFocus = !drawer.hidden && drawer.contains(document.activeElement) && !document.activeElement.disabled;
            const search = document.getElementById('task-search');
            search.focus();
            report.backgroundFocusCloses = drawer.hidden && document.activeElement === search;
          } else {
            const button = document.querySelector('#drawer-summary [data-action="open"]');
            button.focus();
            updatePage({...data, todos:[{...data.todos[0],text:'Refreshed title'}]});
            report.refreshRestoresActionFocus = !drawer.hidden && document.activeElement?.dataset.action === 'open' && document.activeElement.isConnected;
            const backgroundButton = document.querySelector('.todos input[data-complete]');
            backgroundButton.focus();
            report.backgroundControlKeepsFocus = drawer.hidden && document.activeElement === backgroundButton;
            document.querySelector('.todos .task-title').click();
            const field = document.querySelector('#drawer-comments textarea');
            field.value = '   ';
            field.dispatchEvent(new Event('input', {bubbles:true}));
            document.getElementById('drawer-close').click();
            updatePage(data);
            report.whitespaceIsNotDraft = !document.querySelector('.todos').textContent.includes(' · draft');
            document.querySelector('.todos [data-section="sources"]').click();
            const sourceLink = document.querySelector('#drawer-sources a');
            sourceLink.focus();
            updatePage({...data, todos:[{...data.todos[0],text:data.todos[0].text + ' [Extra](https://example.com/extra)'}]});
            report.sourceFocusSurvivesReplacement = document.activeElement?.href === sourceLink.href && document.activeElement.isConnected;
            document.querySelector('#drawer-sources summary').focus();
            updatePage(data);
            report.summaryFocusSurvivesReplacement = document.activeElement?.matches('#drawer-sources summary');
            document.querySelector('#drawer-comments textarea').focus();
            updatePage({...data, todos:[]});
            report.missingTaskHasUsableFocus = !drawer.hidden && document.activeElement?.id === 'drawer-close' && document.getElementById('drawer-title').textContent === 'Task unavailable';
          }
          webkit.messageHandlers.board.postMessage({test:report});
        };
        </script>"""
        self.send({"command": "load", "html": html, "frame": {"x": 45, "y": 55, "w": 480, "h": 700}})
        self.wait_for("message", lambda e: e["body"].get("ready"))
        task = {"id": "action-task", "text": "Start a focus session [Source](https://example.com/source)", "vault": "test", "done": False, "pinned": False}
        self.send({"command": "update", "board": {"windows": [], "todos": [task], "phase": "start"}})
        request = self.wait_for("message", lambda e: "focus" in e["body"])["body"]["focus"]
        self.assertEqual((request["action"], request["id"]), ("start", "action-task"))
        report = self.wait_for("message", lambda e: "test" in e["body"])["body"]["test"]
        self.assertEqual(report, {"pendingKeepsUsableFocus": True, "backgroundFocusCloses": True})
        self.send({"command": "update", "board": {"windows": [], "todos": [task], "phase": "ack", "focus_ack": {"request_id": request["request_id"]}}})
        report = self.wait_for("message", lambda e: "test" in e["body"])["body"]["test"]
        self.assertEqual(report, {"refreshRestoresActionFocus": True, "backgroundControlKeepsFocus": True, "whitespaceIsNotDraft": True,
            "sourceFocusSurvivesReplacement": True, "summaryFocusSurvivesReplacement": True, "missingTaskHasUsableFocus": True})

    def test_create_task_groups_refresh_and_ack_safety(self):
        source = (ROOT / "hammerspoon/task_board.lua").read_text()
        html = source.split("local PAGE = [[\n", 1)[1].split("\n]]", 1)[0]
        html += """<script>
        const originalUpdate = window.update;
        const createRequests = [];
        const sendMessage = webkit.messageHandlers.board.postMessage.bind(webkit.messageHandlers.board);
        webkit.messageHandlers.board.postMessage = body => {
          if (body.focus?.action === 'create') createRequests.push(body.focus);
          sendMessage(body);
        };
        window.update = data => {
          originalUpdate({...data,focus_notes:{...data.focus_notes,vaults:[data.focus_notes.vaults[1]]}});
          const firstConfiguredVault = document.getElementById('focus-vault').value;
          originalUpdate(data);
          const input = document.getElementById('focus-task');
          const vault = document.getElementById('focus-vault');
          const section = document.getElementById('focus-section');
          const form = document.getElementById('focus-form');
          const save = document.getElementById('focus-save');
          const choose = (field, value) => { field.value = value; field.dispatchEvent(new Event('change', {bubbles:true})); };
          const type = text => { input.value = text; input.dispatchEvent(new Event('input', {bubbles:true})); };
          const ack = error => originalUpdate({...data,focus_ack:{request_id:createRequests.at(-1).request_id,...(error ? {error} : {})}});
          const options = () => [...section.options].map(o => o.value);
          document.querySelector('[data-add]').click();
          choose(vault, 'work');
          const report = {firstConfiguredVault, singleEntry: document.querySelectorAll('[data-add]').length === 1, defaultGroup: section.value};
          choose(section, 'CALLE'); section.focus();
          originalUpdate(data);
          report.refreshPreservesSelection = section.value === 'CALLE' && document.activeElement === section;
          const search = document.getElementById('task-search');
          search.value = 'no matching task'; search.dispatchEvent(new Event('input'));
          document.querySelector('[data-show-completed]').click();
          report.allGroupsRemainAvailable = ['CALLE','Completed','Empty'].every(group => options().includes(group)) && section.value === 'CALLE';
          choose(vault, 'personal');
          report.vaultChangeResets = section.value === 'Inbox' && options().includes('Home') && !options().includes('CALLE');
          choose(vault, 'work'); choose(section, 'CALLE'); type('New task');
          form.requestSubmit();
          report.initialRequest = {vault:createRequests.at(-1).vault,section:createRequests.at(-1).section,pin:createRequests.at(-1).pin};
          choose(section, 'Empty'); ack();
          report.changedGroupDraftSurvives = input.value === 'New task' && section.value === 'Empty' && !form.hidden;
          choose(section, 'Inbox'); form.requestSubmit();
          choose(vault, 'personal'); ack();
          report.changedVaultDraftSurvives = input.value === 'New task' && vault.value === 'personal' && section.value === 'Inbox' && !form.hidden;
          form.requestSubmit(); type('Newer draft'); ack();
          report.changedTextDraftSurvives = input.value === 'Newer draft' && !form.hidden;
          form.requestSubmit(); ack('Write conflict');
          report.failedWritePreservesDraft = input.value === 'Newer draft' && !form.hidden && !save.disabled;
          const beforeComposition = createRequests.length;
          input.dispatchEvent(new CompositionEvent('compositionstart', {bubbles:true}));
          form.requestSubmit();
          report.compositionBlocksSubmit = createRequests.length === beforeComposition;
          input.dispatchEvent(new CompositionEvent('compositionend', {bubbles:true}));
          form.requestSubmit(); ack();
          report.matchingAckClears = input.value === '' && form.hidden;
          document.querySelector('[data-add]').click();
          choose(vault, 'work'); choose(section, 'CALLE'); type('Group removed while editing');
          const missing = {...data, focus_notes:{...data.focus_notes,vaults:[{name:'work',sections:['Inbox','Empty']},{name:'personal',sections:['Inbox','Home']}]}};
          originalUpdate(missing);
          report.missingGroupRetained = section.value === 'CALLE' && [...section.options].some(o => o.value === 'CALLE' && /unavailable/i.test(o.textContent));
          const beforeInvalid = createRequests.length;
          report.missingGroupDisablesSave = save.disabled;
          form.requestSubmit();
          report.missingGroupBlocksSubmit = createRequests.length === beforeInvalid;
          choose(section, 'Inbox');
          report.validGroupEnablesSave = !save.disabled;
          form.requestSubmit();
          report.recoveredRequest = {vault:createRequests.at(-1).vault,section:createRequests.at(-1).section,text:createRequests.at(-1).text};
          originalUpdate({...missing,focus_ack:{request_id:createRequests.at(-1).request_id}});
          document.querySelector('[data-add]').click(); type('Vault removed while editing');
          originalUpdate({...missing,focus_notes:{...missing.focus_notes,vaults:[{name:'personal',sections:['Inbox','Home']}]}});
          report.missingVaultRetained = vault.value === 'work' && [...vault.options].some(o => o.value === 'work' && /unavailable/i.test(o.textContent));
          report.missingVaultDisablesSave = save.disabled;
          const beforeMissingVault = createRequests.length;
          form.requestSubmit();
          report.missingVaultBlocksSubmit = createRequests.length === beforeMissingVault;
          choose(vault, 'personal');
          report.newVaultEnablesSave = !save.disabled && section.value === 'Inbox';
          report.noPageOverflow = document.documentElement.scrollWidth <= innerWidth;
          webkit.messageHandlers.board.postMessage({test:report});
        };
        </script>"""
        self.send({"command": "load", "html": html, "frame": {"x": 45, "y": 55, "w": 480, "h": 700}})
        self.wait_for("message", lambda e: e["body"].get("ready"))
        tasks = [{"id": "group-task", "text": "An open task", "vault": "work", "section": "CALLE", "done": False},
                 {"id": "completed-group", "text": "An old task", "vault": "work", "section": "Completed", "done": True}]
        vaults = [{"name": "work", "sections": ["Inbox", "CALLE", "Completed", "Empty"]},
                  {"name": "personal", "sections": ["Inbox", "Home"]}]
        self.send({"command": "update", "board": {"windows": [], "todos": tasks, "focus_notes": {"tasks": tasks, "vaults": vaults, "errors": []}}})
        report = self.wait_for("message", lambda e: "test" in e["body"])["body"]["test"]
        self.assertEqual(report, {"firstConfiguredVault": "personal", "singleEntry": True, "defaultGroup": "Inbox", "refreshPreservesSelection": True,
            "allGroupsRemainAvailable": True, "vaultChangeResets": True,
            "initialRequest": {"vault": "work", "section": "CALLE", "pin": False},
            "changedGroupDraftSurvives": True, "changedVaultDraftSurvives": True, "changedTextDraftSurvives": True,
            "failedWritePreservesDraft": True, "compositionBlocksSubmit": True, "matchingAckClears": True,
            "missingGroupRetained": True, "missingGroupDisablesSave": True, "missingGroupBlocksSubmit": True,
            "validGroupEnablesSave": True,
            "recoveredRequest": {"vault": "work", "section": "Inbox", "text": "Group removed while editing"},
            "missingVaultRetained": True, "missingVaultDisablesSave": True, "missingVaultBlocksSubmit": True,
            "newVaultEnablesSave": True, "noPageOverflow": True})

    def test_due_order_stays_within_columns_and_checkbox_keeps_identity(self):
        source = (ROOT / "hammerspoon/task_board.lua").read_text()
        html = source.split("local PAGE = [[\n", 1)[1].split("\n]]", 1)[0]
        # Singapore 00:30 is still the previous UTC calendar date.
        html = html.replace("<script>", """<script>
        const NativeDate = Date;
        window.Date = class extends NativeDate {
          constructor(...args) { super(...(args.length ? args : ['2026-11-04T16:30:00Z'])); }
          getFullYear() { return 2026; }
          getMonth() { return 10; }
          getDate() { return 5; }
        };
        </script><script>""", 1)
        html += """<script>
        const originalUpdate = window.update;
        window.update = data => {
          const originalTasks = JSON.stringify(data.todos);
          originalUpdate(data);
          const rows = () => [...document.querySelectorAll('.todos .todo')].map(t => t.dataset.task);
          const report = {
            groups: [...document.querySelectorAll('.todo-group')].map(g => JSON.parse(g.dataset.group)[1]),
            openRows: rows(),
            focusRows: [...document.querySelectorAll('.focus-row')].map(t => t.dataset.task),
            dueToday: document.querySelector('[data-task="calle-today"] .row-meta').textContent.trim(),
            yesterdayIsOverdue: !!document.querySelector('[data-task="calle-yesterday"] .overdue')
          };
          document.querySelector('[data-show-completed]').click();
          report.withCompleted = rows();
          const search = document.getElementById('task-search');
          search.value = 'CALLE deadline'; search.dispatchEvent(new Event('input'));
          originalUpdate(data);
          report.afterSearch = rows();
          document.querySelector('.todos .todo input[data-complete]').click();
          report.sourceUnchanged = JSON.stringify(data.todos) === originalTasks;
          webkit.messageHandlers.board.postMessage({test:report});
        };
        </script>"""
        self.send({"command": "load", "html": html, "frame": {"x": 45, "y": 55, "w": 640, "h": 700}})
        self.wait_for("message", lambda e: e["body"].get("ready"))
        def task(task_id, section="CALLE", due=None, done=False, pinned=False):
            return {"id": task_id, "section": section, "due": due, "done": done, "pinned": pinned,
                    "text": "Deadline task", "vault": "work"}
        tasks = [task("dm-later", "DM", "2026-12-01"), task("calle-none-1", pinned=True),
                 task("calle-tie-1", due="2026-11-07", pinned=True), task("calle-done-1", due="2027-01-01", done=True),
                 task("calle-yesterday", due="2026-11-04", pinned=True), task("calle-today", due="2026-11-05"),
                 task("calle-tie-2", due="2026-11-07"), task("dm-overdue", "DM", "2026-11-02"),
                 task("calle-none-2"), task("calle-done-2", due="2025-01-01", done=True)]
        self.send({"command": "update", "board": {"windows": [], "todos": tasks}})
        request = self.wait_for("message", lambda e: "focus" in e["body"])["body"]["focus"]
        self.assertEqual((request["action"], request["id"]), ("complete", "calle-yesterday"))
        report = self.wait_for("message", lambda e: "test" in e["body"])["body"]["test"]
        calle_open = ["calle-yesterday", "calle-today", "calle-tie-1", "calle-tie-2", "calle-none-1", "calle-none-2"]
        calle_all = calle_open + ["calle-done-1", "calle-done-2"]
        self.assertEqual(report, {"groups": ["DM", "CALLE"], "openRows": ["dm-overdue", "dm-later"] + calle_open,
            "focusRows": ["calle-none-1", "calle-tie-1", "calle-yesterday"],
            "dueToday": "Due today", "yesterdayIsOverdue": True,
            "withCompleted": ["dm-overdue", "dm-later"] + calle_all,
            "afterSearch": calle_all, "sourceUnchanged": True})

    def test_move_task_group_keeps_identity_and_comment_draft(self):
        source = (ROOT / "hammerspoon/task_board.lua").read_text()
        html = source.split("local PAGE = [[\n", 1)[1].split("\n]]", 1)[0]
        html += """<script>
        const originalUpdate = window.update;
        const requests = [];
        const sendMessage = webkit.messageHandlers.board.postMessage.bind(webkit.messageHandlers.board);
        webkit.messageHandlers.board.postMessage = body => {
          if (body.focus) requests.push(body.focus);
          sendMessage(body);
        };
        window.update = data => {
          originalUpdate(data);
          const open = id => document.querySelector('.todos .task-title[data-detail="' + id + '"]').click();
          const group = document.getElementById('task-group');
          const form = document.getElementById('drawer-group');
          const save = document.getElementById('task-group-save');
          const choose = value => { group.value = value; group.dispatchEvent(new Event('change', {bubbles:true})); };
          const options = () => [...group.options].map(o => o.value);
          open('move-task');
          const report = {initialGroup:group.value, sameGroupDisabled:save.disabled,
            workGroupsAvailable:['Inbox','CALLE','DM','Empty','Completed'].every(name => options().includes(name)) && !options().includes('Home')};
          form.requestSubmit();
          report.sameGroupDoesNotSubmit = requests.length === 0;
          choose('DM');
          let editor = document.querySelector('#drawer-comments textarea');
          editor.value = 'Keep this comment draft'; editor.dispatchEvent(new Event('input', {bubbles:true}));
          document.getElementById('drawer-close').click(); open('move-task');
          report.reopenKeepsSelection = group.value === 'DM';
          open('personal-task');
          report.personalGroupsIsolated = options().includes('Home') && !options().includes('DM');
          choose('Home'); open('move-task');
          report.switchKeepsSelection = group.value === 'DM';
          editor = document.querySelector('#drawer-comments textarea');
          const groupNode = group;
          originalUpdate(data);
          report.refreshKeepsControls = groupNode.isConnected && group.value === 'DM' && editor === document.querySelector('#drawer-comments textarea') && editor.value === 'Keep this comment draft';
          report.selectionIsLocal = requests.length === 0;
          group.focus(); form.requestSubmit();
          report.pendingGroupKeepsUsableFocus = document.activeElement?.id === 'drawer-close';
          const first = requests.at(-1);
          report.request = {action:first.action,id:first.id,section:first.section};
          const pendingCount = requests.length;
          form.requestSubmit();
          document.querySelector('#drawer-summary [data-action="start"]').click();
          report.pendingBlocksDuplicatesAndOtherActions = requests.length === pendingCount && save.disabled && save.textContent.includes('Moving');
          originalUpdate({...data,focus_ack:{request_id:first.request_id,error:'Cannot move task'}});
          report.failedMoveKeepsDraft = group.value === 'DM' && !save.disabled && document.getElementById('task-group-status').textContent.includes('Cannot move task');
          form.requestSubmit();
          const retried = requests.at(-1);
          report.retryHasFreshRequest = retried.request_id !== first.request_id && retried.section === 'DM';
          const moved = {...data,todos:data.todos.map(t => t.id === 'move-task' ? {...t,section:'DM'} : t),focus_ack:{request_id:retried.request_id}};
          originalUpdate(moved);
          report.successReadsNewGroup = group.value === 'DM' && save.disabled;
          report.drawerKeepsTaskIdentity = !document.getElementById('task-drawer').hidden && document.getElementById('task-drawer').dataset.task === 'move-task';
          report.commentEditorSurvivesMove = editor === document.querySelector('#drawer-comments textarea') && editor.isConnected && editor.value === 'Keep this comment draft';
          report.movedColumn = JSON.parse(document.querySelector('.todos [data-task="move-task"]').closest('.todo-group').dataset.group);
          report.otherVaultColumn = JSON.parse(document.querySelector('.todos [data-task="personal-task"]').closest('.todo-group').dataset.group);
          choose('Empty');
          originalUpdate({...moved,focus_notes:{...moved.focus_notes,vaults:[{name:'work',sections:['Inbox','CALLE','DM','Completed']},{name:'personal',sections:['Inbox','CALLE','Home']}]}});
          const beforeInvalid = requests.length;
          report.removedTargetRequiresSelection = group.value === 'Empty' && [...group.options].some(o => o.value === 'Empty' && /unavailable/i.test(o.textContent)) && save.disabled;
          form.requestSubmit();
          report.removedTargetDoesNotSubmit = requests.length === beforeInvalid;
          choose('CALLE'); report.validTargetCanRetry = !save.disabled;
          webkit.messageHandlers.board.postMessage({test:report});
        };
        </script>"""
        self.send({"command": "load", "html": html, "frame": {"x": 45, "y": 55, "w": 640, "h": 700}})
        self.wait_for("message", lambda e: e["body"].get("ready"))
        tasks = [{"id": "move-task", "text": "Move this task", "vault": "work", "section": "CALLE", "done": False},
                 {"id": "personal-task", "text": "Keep this personal task", "vault": "personal", "section": "CALLE", "done": False},
                 {"id": "completed-task", "text": "Already done", "vault": "work", "section": "Completed", "done": True}]
        vaults = [{"name": "work", "sections": ["Inbox", "CALLE", "DM", "Empty", "Completed"]},
                  {"name": "personal", "sections": ["Inbox", "CALLE", "Home"]}]
        self.send({"command": "update", "board": {"windows": [], "todos": tasks, "focus_notes": {"tasks": tasks, "vaults": vaults, "errors": []}}})
        report = self.wait_for("message", lambda e: "test" in e["body"])["body"]["test"]
        self.assertEqual(report, {"initialGroup": "CALLE", "sameGroupDisabled": True, "workGroupsAvailable": True,
            "sameGroupDoesNotSubmit": True, "reopenKeepsSelection": True, "personalGroupsIsolated": True,
            "switchKeepsSelection": True, "refreshKeepsControls": True, "selectionIsLocal": True,
            "request": {"action": "move", "id": "move-task", "section": "DM"},
            "pendingGroupKeepsUsableFocus": True, "pendingBlocksDuplicatesAndOtherActions": True, "failedMoveKeepsDraft": True, "retryHasFreshRequest": True,
            "successReadsNewGroup": True, "drawerKeepsTaskIdentity": True, "commentEditorSurvivesMove": True,
            "movedColumn": ["work", "DM"], "otherVaultColumn": ["personal", "CALLE"],
            "removedTargetRequiresSelection": True, "removedTargetDoesNotSubmit": True, "validTargetCanRetry": True})

    def test_webkit_action_bridge(self):
        actions = [{"edit": True}, {"todo": {"vault": "中文", "line": 3.0}},
                   {"dismiss": {"vault": "中文", "line": 3}},
                   {"snooze": {"vault": "中文", "line": 3}},
                   {"gap": 42}, {"gap": False}, {"url": "https://example.com"},
                   {"id": "@1", "session": "work"}]
        html = "<script>for (const body of " + json.dumps(actions) + ") webkit.messageHandlers.board.postMessage(body)</script>"
        self.send({"command": "load", "html": html,
                   "frame": {"x": 10, "y": 10, "w": 600, "h": 500}})
        for action in actions:
            self.assertEqual(self.wait_for("message")["body"], action)


if __name__ == "__main__":
    unittest.main()
