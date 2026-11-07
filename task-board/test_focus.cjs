// Exercise request/state logic with a small DOM adapter. The actual drawer,
// focus, scrolling, and layout are covered by test_window.py in WKWebView.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../hammerspoon/task_board.lua'), 'utf8');
const html = source.split('local PAGE = [[\n')[1].split('\n]]')[0];
const script = html.split('<script>')[1].split('</script>')[0];
const elements = new Map(), listeners = {};
const document = {
  activeElement: null,
  querySelector() { return null; }, querySelectorAll() { return []; },
  getElementById: element,
  addEventListener(event, fn) { (listeners[event] ||= []).push(fn); },
  documentElement: {style: {setProperty() {}, removeProperty() {}}},
};
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: id === 'focus-minutes' ? '25' : id === 'focus-vault' ? 'work' : id === 'focus-section' ? 'Inbox' : '',
    get options() { return [...(this.innerHTML || '').matchAll(/value="([^"]*)"/g)].map(m => ({value:m[1]})); },
    textContent: '', innerHTML: '', disabled: false, hidden: false, dataset: {}, events: {}, attributes: {},
    focus() { document.activeElement = this; }, blur() { document.activeElement = null; },
    matches() { return false; }, contains() { return false; }, closest() { return null; },
    querySelector() { return null; }, querySelectorAll() { return []; }, scrollIntoView() {},
    classList: {add() {}, remove() {}, toggle() {}},
    style: {setProperty() {}, removeProperty() {}},
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
    getAttribute(name) { return this.attributes[name]; },
    addEventListener(event, handler) { (this.events[event] ||= []).push(handler); },
    fire(event, extra={}) { for (const handler of this.events[event] || []) handler({target:this, preventDefault() {}, stopPropagation() {}, ...extra}); },
  });
  return elements.get(id);
}
const messages = [];
const webkit = {messageHandlers: {board: {postMessage(body) { messages.push(JSON.parse(JSON.stringify(body))); }}}};
const window = {webkit};
const context = vm.createContext({window, webkit, document, URL, setInterval() {}, setTimeout() {}, requestAnimationFrame(fn) {fn();}});
vm.runInContext(script, context);
const evaluate = expression => vm.runInContext(expression, context);
// Source presentation must retain distinct evidence and meaningful prose.
const presentation = text => JSON.parse(evaluate(`JSON.stringify(taskPresentation(${JSON.stringify(text)}))`));
assert.deepEqual(presentation('Review [Plan](https://example.com/a_(b)) then [MR !311](https://example.com/mr/311)'), {
  title: 'Review Plan then MR !311', sources: [
    {label:'Plan',url:'https://example.com/a_(b)'},
    {label:'MR !311',url:'https://example.com/mr/311'}
  ]
});
assert.deepEqual(presentation('Review [飞书原消息](https://example.com/a) before deciding [飞书原消息](https://example.com/b)'), {
  title: 'Review before deciding', sources: [
    {label:'飞书原消息',url:'https://example.com/a'},
    {label:'飞书原消息',url:'https://example.com/b'}
  ]
});
assert.equal(presentation('Check (https://example.com/a).').sources[0].url, 'https://example.com/a');
assert.equal(presentation('Check https://example.com/a_(b).').sources[0].url, 'https://example.com/a_(b)');
for (const text of ['Keep ![diagram](https://example.com/image.png)', String.raw`Keep \[literal](https://example.com/a)`]) {
  assert.deepEqual(presentation(text), {title:text,sources:[]}, 'image and escaped Markdown must remain untouched');
}
function dispatch(event, target, extra={}) {
  for (const handler of listeners[event] || []) handler({target, preventDefault() {}, stopPropagation() {}, ...extra});
}
const task = {id:'task-1', text:'Review the proposal', vault:'work', done:false, pinned:true,
  minutes:35, pomodoros:1, unconfirmed:2, started:'2026-10-31T09:00:00+08:00', completed_at:'', sessions:[], comments:[]};
const snapshot = (tasks = [task], ack) => ({windows:[], todos:tasks, focus:{minutes:25}, focus_ack:ack,
  focus_notes:{tasks, vaults:[{name:'work'}, {name:'personal'}], errors:[]}});
function action(name, id='task-1') {
  evaluate(`taskAction(board.todos.find(t => t.id === ${JSON.stringify(id)}), ${JSON.stringify(name)})`);
}
const input = element('focus-task');
window.update(snapshot([]));
window.update(snapshot());
assert.match(element('focus-rows').innerHTML, /Review the proposal/);
assert.match(element('main').innerHTML, /Review the proposal/);
assert.doesNotMatch(element('main').innerHTML, /<label class="task-label">/, 'title must not be a completion label');

// Creation survives refresh and composition; ack clears only the posted draft.
input.value = '完成评审 & 下一步?';
window.update(snapshot());
assert.equal(input.value, '完成评审 & 下一步?');
input.fire('compositionstart');
element('focus-form').fire('submit');
assert.equal(messages.filter(m => m.focus).length, 0);
input.fire('compositionend');
element('focus-form').fire('submit');
const created = messages.at(-1).focus;
assert.equal(created.action, 'create');
assert.equal(created.vault, 'work');
assert.equal(created.section, 'Inbox');
assert.equal(created.pin, false);
assert.ok(created.request_id);
const count = messages.length;
element('focus-form').fire('submit');
assert.equal(messages.length, count);
window.update(snapshot([task], {request_id:'old-request'}));
assert.equal(input.value, created.text);
input.value = 'Newer draft';
window.update(snapshot([task], {request_id:created.request_id}));
assert.equal(input.value, 'Newer draft');
element('focus-form').fire('submit');
window.update(snapshot([task], {request_id:messages.at(-1).focus.request_id}));
assert.equal(input.value, '');

// Create acknowledgements belong to the whole draft, including vault and group.
const vault = element('focus-vault'), section = element('focus-section'), createForm = element('focus-form');
const groupVaults = [{name:'work',sections:['Inbox','CALLE','Empty']},{name:'personal',sections:['Inbox','Home']}];
const groupSnapshot = (ack, vaults=groupVaults) => {
  const result = snapshot([task], ack);
  result.focus_notes.vaults = vaults;
  return result;
};
window.update(groupSnapshot());
assert.deepEqual(section.options.map(o => o.value), ['Inbox','CALLE','Empty']);
for (const changed of ['text','section','vault']) {
  vault.value = 'work'; vault.fire('change');
  section.value = 'Inbox'; section.fire('change');
  input.value = 'Same draft'; createForm.hidden = false;
  createForm.fire('submit');
  const sent = messages.at(-1).focus;
  assert.equal(sent.vault, 'work');
  assert.equal(sent.section, 'Inbox');
  if (changed === 'text') input.value = 'Changed text';
  if (changed === 'section') { section.value = 'Empty'; section.fire('change'); }
  if (changed === 'vault') { vault.value = 'personal'; vault.fire('change'); }
  const unsent = {text:input.value, vault:vault.value, section:section.value};
  window.update(groupSnapshot({request_id:sent.request_id}));
  assert.equal(input.value, unsent.text, `${changed} change must protect the current draft`);
  assert.equal(vault.value, unsent.vault);
  assert.equal(section.value, unsent.section);
  assert.equal(createForm.hidden, false);
  createForm.fire('submit');
  window.update(groupSnapshot({request_id:messages.at(-1).focus.request_id}));
  assert.equal(input.value, '');
  assert.equal(createForm.hidden, true);
}
vault.value = 'work'; vault.fire('change');
section.value = 'CALLE'; section.fire('change');
input.value = 'Keep my chosen group'; createForm.hidden = false;
window.update(groupSnapshot(undefined, [{name:'work',sections:['Inbox']},{name:'personal',sections:['Inbox','Home']}]));
assert.equal(section.value, 'CALLE', 'a removed group must remain selected for review');
assert.match(section.innerHTML, /unavailable/i);
assert.equal(element('focus-save').disabled, true);
const beforeInvalidGroup = messages.length;
createForm.fire('submit');
assert.equal(messages.length, beforeInvalidGroup, 'Enter cannot bypass missing-group validation');
section.value = 'Inbox'; section.fire('change');
assert.equal(element('focus-save').disabled, false);
createForm.fire('submit');
assert.equal(messages.at(-1).focus.section, 'Inbox');
window.update(groupSnapshot({request_id:messages.at(-1).focus.request_id}));
// Older snapshots lack heading metadata; include completed tasks in the fallback.
window.update(snapshot([{...task,section:'CALLE'}, {...task,id:'old-done',section:'Done only',done:true}]));
assert.ok(section.options.some(o => o.value === 'CALLE'));
assert.ok(section.options.some(o => o.value === 'Done only'));
window.update(snapshot());

// Duration and stable task IDs survive polling; pending requests cannot double-launch.
element('focus-minutes').value = '50';
element('focus-minutes').fire('change');
window.update(snapshot());
assert.equal(element('focus-minutes').value, '50');
action('start');
let request = messages.at(-1).focus;
assert.equal(request.id, 'task-1');
assert.equal(request.minutes, 50);
const beforeRetry = messages.length;
action('start');
assert.equal(messages.length, beforeRetry);
window.update(snapshot([task], {request_id:request.request_id}));
action('unpin');
request = messages.at(-1).focus;
window.update(snapshot([{...task, pinned:false}], {request_id:request.request_id}));
assert.doesNotMatch(element('focus-rows').innerHTML, /Review the proposal/);
assert.match(element('main').innerHTML, /Review the proposal/);
action('complete');
request = messages.at(-1).focus;
assert.equal(request.action, 'complete');
window.update(snapshot([{...task, done:true}], {request_id:request.request_id}));
assert.doesNotMatch(element('main').innerHTML, /Review the proposal/);
assert.doesNotMatch(element('focus-rows').innerHTML, /Review the proposal/);
evaluate('showCompleted = true; render()');
assert.match(element('main').innerHTML, /Review the proposal/);
assert.match(element('main').innerHTML, /aria-label="Reopen Review/);

// Comment request/ack logic preserves unsent drafts and exact multiline text.
window.update(snapshot());
const field = {dataset:{comment:'task-1'}, value:'第一行\n第二行', closest() {return this;}};
const form = {dataset:{commentForm:'task-1'}, closest() {return this;}};
const submitComment = () => dispatch('submit', form);
dispatch('input', field);
dispatch('compositionstart', field);
const beforeComposition = messages.length;
submitComment();
assert.equal(messages.length, beforeComposition);
dispatch('compositionend', field);
submitComment();
const commentRequest = messages.at(-1).focus;
assert.equal(commentRequest.action, 'comment');
assert.equal(commentRequest.text, '第一行\n第二行');
submitComment();
assert.equal(messages.at(-1).focus.request_id, commentRequest.request_id);
field.value = 'Newer comment draft';
dispatch('input', field);
const commentedTask = {...task, comments:[{id:'c1', created:'2026-10-31T09:00:00+08:00', text:'<img src=x onerror=alert(1)> 中文'}]};
window.update(snapshot([commentedTask], {request_id:commentRequest.request_id}));
assert.equal(evaluate('commentDrafts.get("task-1")'), 'Newer comment draft');
submitComment();
const failed = messages.at(-1).focus;
window.update(snapshot([commentedTask], {request_id:failed.request_id,error:'Editor conflict'}));
assert.equal(evaluate('commentDrafts.get("task-1")'), 'Newer comment draft');
assert.equal(evaluate('commentErrors.get("task-1")'), 'Editor conflict');
submitComment();
window.update(snapshot([commentedTask], {request_id:messages.at(-1).focus.request_id}));
assert.equal(evaluate('commentDrafts.get("task-1")') || '', '');

// An ack during a new composition must not clear the editor under the IME.
field.value = 'Saved while composing';
dispatch('input', field);
submitComment();
const composingRequest = messages.at(-1).focus;
dispatch('compositionstart', field);
window.update(snapshot([commentedTask], {request_id:composingRequest.request_id}));
assert.equal(evaluate('commentDrafts.get("task-1")'), 'Saved while composing');
dispatch('compositionend', field);

// Search matches metadata/comments, ANDs terms, respects IME, and mutates by ID.
evaluate('showCompleted = false');
const searchTasks = [
  {...task,id:'calle-1',section:'CALLE',text:'Review design',tags:['call'],comments:[]},
  {...task,id:'dm-1',section:'DM',text:'Ship feature',pinned:false,comments:[{text:'等待评审'}]},
  {...task,id:'calle-2',section:'CALLE',text:'Other task',pinned:false,comments:[]},
  {...task,id:'personal-1',vault:'personal',section:'CALLE',text:'Read book',pinned:false,comments:[]},
  {...task,id:'done-1',section:'DM',text:'Archived task',done:true,pinned:false,comments:[]}
];
window.update(snapshot(searchTasks));
assert.equal((element('main').innerHTML.match(/class="todo-group"/g)||[]).length, 3);
const search = element('task-search');
search.value='cAlLe review';search.fire('input');
assert.match(element('main').innerHTML,/data-task="calle-1"/);
assert.doesNotMatch(element('main').innerHTML,/data-task="(?:dm-1|calle-2|personal-1)"/);
window.update(snapshot(searchTasks));
assert.equal(search.value,'cAlLe review');
assert.match(element('main').innerHTML,/1 of 4/);
search.value='等待';search.fire('input');
assert.match(element('main').innerHTML,/data-task="dm-1"/);
assert.doesNotMatch(element('focus-rows').innerHTML,/data-task=/);
action('complete', 'dm-1');
assert.equal(messages.at(-1).focus.id,'dm-1');
search.value='no-such-task';search.fire('compositionstart');search.fire('input');
assert.match(element('main').innerHTML,/data-task="dm-1"/);
search.fire('compositionend');
assert.match(element('main').innerHTML,/No matching tasks/);
search.value='Archived';search.fire('input');
assert.doesNotMatch(element('main').innerHTML,/data-task="done-1"/);
evaluate('showCompleted = true; render()');
assert.match(element('main').innerHTML,/data-task="done-1"/);
element('task-search-clear').fire('click');
assert.equal(search.value,'');
assert.equal((element('main').innerHTML.match(/class="todo-group"/g)||[]).length,3);
// Due-date ordering is a view of each column, never a rewrite of task order.
const dueTasks = [
  {...task,id:'dm-later',section:'DM',text:'Deadline task',due:'2026-12-01',pinned:false},
  {...task,id:'calle-none-1',section:'CALLE',text:'Deadline task'},
  {...task,id:'calle-tie-1',section:'CALLE',text:'Deadline task',due:'2026-11-07'},
  {...task,id:'calle-done-1',section:'CALLE',text:'Deadline task',due:'2027-01-01',done:true,pinned:false},
  {...task,id:'calle-overdue',section:'CALLE',text:'Deadline task',due:'2026-11-01'},
  {...task,id:'calle-today',section:'CALLE',text:'Deadline task',due:'2026-11-05',pinned:false},
  {...task,id:'calle-tie-2',section:'CALLE',text:'Deadline task',due:'2026-11-07',pinned:false},
  {...task,id:'dm-overdue',section:'DM',text:'Deadline task',due:'2026-11-02',pinned:false},
  {...task,id:'calle-none-2',section:'CALLE',text:'Deadline task',pinned:false},
  {...task,id:'calle-done-2',section:'CALLE',text:'Deadline task',due:'2025-01-01',done:true,pinned:false}
];
const originalDueTasks = JSON.stringify(dueTasks);
const idsIn = id => [...element(id).innerHTML.matchAll(/data-task="([^"]+)"/g)].map(m => m[1]);
// Model 00:30 in Singapore: the local calendar date is already the next UTC day.
context.Date = class extends Date {
  constructor(...args) { super(...(args.length ? args : ['2026-11-04T16:30:00Z'])); }
  getFullYear() { return 2026; }
  getMonth() { return 10; }
  getDate() { return 5; }
};
evaluate('showCompleted = false');
window.update(snapshot(dueTasks));
assert.equal(evaluate('today()'), '2026-11-05');
assert.deepEqual(idsIn('main'), ['dm-overdue','dm-later','calle-overdue','calle-today','calle-tie-1','calle-tie-2','calle-none-1','calle-none-2']);
assert.deepEqual(idsIn('focus-rows'), ['calle-none-1','calle-tie-1','calle-overdue'], 'Focus keeps the user task order');
assert.match(element('main').innerHTML, /Due today/);
evaluate('showCompleted = true; render()');
assert.deepEqual(idsIn('main'), ['dm-overdue','dm-later','calle-overdue','calle-today','calle-tie-1','calle-tie-2','calle-none-1','calle-none-2','calle-done-1','calle-done-2']);
search.value = 'CALLE deadline'; search.fire('input');
assert.deepEqual(idsIn('main'), ['calle-overdue','calle-today','calle-tie-1','calle-tie-2','calle-none-1','calle-none-2','calle-done-1','calle-done-2']);
assert.equal(JSON.stringify(dueTasks), originalDueTasks, 'rendering/filtering must not mutate source tasks');
context.Date = Date;
// Moving shares the stable-ID action lock and acknowledges only its own request.
const movingTask = {...task,section:'CALLE'};
const moveSnapshot = (ack, moved=false) => {
  const result = snapshot([{...movingTask,section:moved ? 'DM' : 'CALLE'}], ack);
  result.focus_notes.vaults = [{name:'work',sections:['Inbox','CALLE','DM']}];
  return result;
};
window.update(moveSnapshot());
const moveForm = element('drawer-group'), moveGroup = element('task-group');
moveForm.dataset.task = 'task-1';
const beforeMoves = messages.length;
for (const target of ['CALLE','Other vault group']) {
  moveGroup.value = target; moveGroup.fire('change'); moveForm.fire('submit');
}
assert.equal(messages.length, beforeMoves, 'same or unavailable groups cannot trigger a move');
moveGroup.value = 'DM'; moveGroup.fire('change'); moveForm.fire('submit');
const {request_id:moveRequest, ...movePayload} = messages.at(-1).focus;
assert.deepEqual(movePayload, {action:'move',id:'task-1',section:'DM'});
const whileMoving = messages.length;
moveForm.fire('submit'); action('start');
assert.equal(messages.length, whileMoving, 'moves block duplicate and competing task actions');
window.update(moveSnapshot({request_id:'unrelated'}));
assert.equal(evaluate('pendingTasks.get("task-1")'), moveRequest);
window.update(moveSnapshot({request_id:moveRequest,error:'Move conflict'}));
assert.equal(evaluate('groupDrafts.get("task-1").section'), 'DM');
assert.equal(evaluate('groupDrafts.get("task-1").error'), 'Move conflict');
assert.equal(evaluate('pendingTasks.has("task-1")'), false);
moveForm.fire('submit');
const retriedMove = messages.at(-1).focus;
assert.notEqual(retriedMove.request_id, moveRequest);
window.update(moveSnapshot({request_id:retriedMove.request_id}, true));
assert.equal(evaluate('groupDrafts.has("task-1")'), false);
assert.equal(evaluate('pendingTasks.has("task-1")'), false);
evaluate('syncTaskGroup(board.todos[0])');
assert.equal(moveGroup.value, 'DM');
assert.equal(element('task-group-save').disabled, true);
// A later move ack cannot overwrite a comment result deferred by the IME.
field.value = 'Comment submitted before moving'; dispatch('input', field); submitComment();
const deferredComment = messages.at(-1).focus;
moveGroup.value = 'CALLE'; moveGroup.fire('change'); moveForm.fire('submit');
const concurrentMove = messages.at(-1).focus;
assert.equal(deferredComment.action, 'comment');
assert.equal(concurrentMove.action, 'move');
dispatch('compositionstart', field);
field.value = 'New comment draft during composition'; dispatch('input', field);
window.update(moveSnapshot({request_id:deferredComment.request_id}, true));
assert.equal(evaluate('pendingComments.has("task-1")'), true, 'IME defers changing the live editor');
window.update(moveSnapshot({request_id:concurrentMove.request_id}));
dispatch('compositionend', field);
assert.equal(evaluate('pendingComments.has("task-1")'), false, 'consume the stored comment ack after a later move ack');
assert.equal(evaluate('pendingTasks.has("task-1")'), false);
assert.equal(evaluate('commentDrafts.get("task-1")'), 'New comment draft during composition');
console.log('PASS: stable-ID actions, request deduplication, creation/comment drafts, IME, failed writes, search, stable due ordering, local dates and group moves');
