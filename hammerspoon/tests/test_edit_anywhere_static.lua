local source_path = assert(arg[1], "edit_anywhere.lua path is required")

hs = {
  execute = function(command)
    if command == "/usr/bin/id -u" then return "501\n", true end
    return "", false
  end,
  host = {
    uuid = function() return "NEW-HAMMERSPOON-INSTANCE-0001" end,
  },
}

local module = assert(loadfile(source_path))()
local test = assert(module._test, "test hooks were not enabled")

local owner = {
  protocol_version = 1,
  hammerspoon_instance_uuid = "OLD-HAMMERSPOON-INSTANCE-0001",
  session_id = "20260901-193125-CA438737",
  nonce = "0123456789abcdefghijklmn",
  stage = "accepted",
  created_at_unix_ms = 1788262285000,
  lease_expires_at_unix_ms = 1788262288000,
}
local adopted = {
  id = owner.session_id,
  nonce = owner.nonce,
  owner_instance_uuid = owner.hammerspoon_instance_uuid,
}
assert(test.valid_owner(owner))
assert(test.owner_matches(owner, adopted), "adoption must first match the old owner UUID")

local rewritten = {}
for key, value in pairs(owner) do rewritten[key] = value end
rewritten.hammerspoon_instance_uuid = module._state.instance_uuid
adopted.owner_instance_uuid = module._state.instance_uuid
assert(test.owner_matches(rewritten, adopted), "post-adoption operations must match the new owner UUID")
assert(not test.owner_matches(owner, adopted), "the old owner UUID must stop matching after adoption")

local malformed = {}
for key, value in pairs(owner) do malformed[key] = value end
malformed.unexpected = true
assert(not test.valid_owner(malformed), "unknown owner fields must fail closed")

local accepted_decision = {
  protocol_version = 1,
  session_id = owner.session_id,
  nonce = owner.nonce,
  outcome = "accepted",
  reason = nil,
  fallback_allowed = false,
  writer = "server",
  server_uuid = "server-00000000-0000-0000-0000-000000000001",
  generation = 0,
  config_fingerprint = string.rep("a", 64),
  decided_at_unix_ms = owner.created_at_unix_ms + 1,
}
assert(test.valid_decision(accepted_decision, owner), "accepted decisions require a 64-character fingerprint")
accepted_decision.config_fingerprint = "server-v1"
assert(not test.valid_decision(accepted_decision, owner), "non-digest fingerprints must fail closed")

local source_file = assert(io.open(source_path, "rb"))
local source = assert(source_file:read("*a"))
source_file:close()

local function function_body(name, next_name)
  local start_marker = "local function " .. name
  local start_at = assert(source:find(start_marker, 1, true), "missing function " .. name)
  local end_at
  if next_name then
    end_at = assert(source:find("local function " .. next_name, start_at + #start_marker, true), "missing function " .. next_name)
  else
    end_at = #source + 1
  end
  return source:sub(start_at, end_at - 1)
end

local function contains(body, text, message)
  assert(body:find(text, 1, true), message or ("missing: " .. text))
end

local function excludes(body, text, message)
  assert(not body:find(text, 1, true), message or ("unexpected: " .. text))
end

local terminal = function_body("reconcile_terminal_owner", "reconcile_rejected_owner")
contains(terminal, "owner_instance_uuid = owner.hammerspoon_instance_uuid", "terminal cleanup must bind the old owner UUID")
contains(terminal, 'delivery.status == "paste_intent"', "paste_intent must be reconciled explicitly")
contains(terminal, 'write_delivery(session, "clipboard_only"', "paste_intent recovery must become clipboard-only")
excludes(terminal, "hs.eventtap.keyStroke", "reload reconciliation must never replay paste")

local rejected = function_body("reconcile_rejected_owner", "valid_server_health")
contains(rejected, "owner_instance_uuid = owner.hammerspoon_instance_uuid", "rejected cleanup must bind the old owner UUID")
contains(rejected, "paths.result", "a rejected decision with a contradictory result must retain the lock")
contains(rejected, "paths.delivery", "a rejected decision with delivery state must retain the lock")

local active = function_body("adopt_active_owner", "reclaim_unpublished_claim")
contains(active, "health.server_uuid ~= decision.server_uuid", "active adoption must match server UUID")
contains(active, "health.generation ~= decision.generation", "active adoption must match generation")
contains(active, "health.config_fingerprint ~= decision.config_fingerprint", "active adoption must match fingerprint")
contains(active, "health.active_session ~= owner.session_id", "active adoption must match active session")
contains(active, "owner_instance_uuid = owner.hammerspoon_instance_uuid", "active adoption must CAS from the old owner UUID")
contains(active, "update_owner(session, stage)", "active adoption must atomically rewrite the owner")
excludes(active, "release_owner", "a dead or mismatched generation must not release the lock")

local show_terminal = function_body("show_quick_terminal", "clamp")
contains(show_terminal, "terminal:isVisible()", "resume must inspect the existing Quick Terminal")
contains(show_terminal, "terminal:focus()", "a visible Quick Terminal must be focused, not toggled away")

local placement = function_body("place_quick_terminal", "observe_quick_terminal")
contains(placement, "visible.w * 0.46, 1180", "Quick Terminal width must stay compact on large displays")
contains(placement, "visible.h * 0.44, 680", "Quick Terminal height must stay compact on large displays")

local timer_scheduler = function_body("schedule_session_timer", "stop_session_activity")
contains(timer_scheduler, "session[name] = hs.timer.doAfter", "one-shot timers must remain strongly referenced")
contains(timer_scheduler, "state.sessions[session.id] ~= session", "late timer callbacks must reject stale sessions")

local stop_activity = function_body("stop_session_activity", "release_owner")
for _, timer_name in ipairs({
  "writeback_focus_timer", "writeback_select_timer", "writeback_ack_timer", "writeback_watchdog",
}) do
  contains(stop_activity, timer_name, timer_name .. " must be stopped during cleanup")
end
contains(stop_activity, "session.dispatch_pending = false", "session cleanup must release the dispatch gate")

local dispatch = function_body("dispatch", "publish_request")
contains(dispatch, "if session.dispatch_pending then", "repeated resume requests must be coalesced")
contains(dispatch, "session.dispatch_pending = true", "dispatch must close its single-flight gate before writing the FIFO")
contains(dispatch, "session.dispatch_pending = false", "dispatch failures and timeout must reopen the gate")

local focus_source = function_body("focus_source", "finish_session")
contains(focus_source, 'schedule_session_timer(session, "writeback_focus_timer"', "focus retry timer must be retained")
contains(focus_source, 'writeback_session_is_current(session, "terminal")', "focus retries must validate session ownership")

local commit_result = function_body("commit_result", "handle_result")
contains(commit_result, 'schedule_session_timer(session, "writeback_watchdog"', "writeback must have a watchdog")
contains(commit_result, 'schedule_session_timer(session, "writeback_select_timer"', "selection timer must be retained")
contains(commit_result, 'schedule_session_timer(session, "writeback_ack_timer"', "paste acknowledgement timer must be retained")
contains(commit_result, 'fallback_to_clipboard(session, "WRITEBACK_TIMEOUT"', "watchdog must fail safe to clipboard")
contains(commit_result, 'writeback_session_is_current(session, "paste_intent")', "writeback callbacks must validate ownership")

local begin_edit = function_body("begin_edit", nil)
contains(begin_edit, "focused_input_text(app)", "focused editable controls must use direct accessibility reads")
contains(begin_edit, "direct_contents ~= nil", "an empty accessibility value must still open an editor")
contains(begin_edit, "hs.pasteboard.callbackWhenChanged", "clipboard capture must remain as a compatibility fallback")

local claimed = function_body("reclaim_unpublished_claim", nil)
contains(claimed, "owner.lease_expires_at_unix_ms + RECLAIM_GRACE_MS", "claimed recovery must wait for lease and grace")
contains(claimed, "health.active_session ~= nil", "claimed recovery requires a proven-empty server")
contains(claimed, "owner_instance_uuid = owner.hammerspoon_instance_uuid", "claimed recovery must release only the old owner")

local empty_lock = function_body("reclaim_empty_frontend_lock", "reconcile_owner")
contains(empty_lock, "directory_is_empty(FRONTEND_LOCK_DIR)", "only an actually empty frontend lock may be reclaimed")
contains(empty_lock, "attributes.modification * 1000 + RECLAIM_GRACE_MS", "empty lock recovery must wait for grace")
contains(empty_lock, "health.active_session ~= nil", "empty lock recovery requires a proven-empty server")

local orphaned = function_body("reclaim_orphaned_session", "reconcile_owner")
contains(orphaned, "health.server_uuid == decision.server_uuid", "same-generation recovery must match the server UUID")
contains(orphaned, "health.generation == decision.generation", "same-generation recovery must match the generation")
contains(orphaned, "health.config_fingerprint == decision.config_fingerprint", "same-generation recovery must match the config")
contains(orphaned, "health.generation > decision.generation", "a newer server generation may reclaim an older orphan")
contains(orphaned, "not same_generation and not superseded_generation", "orphan recovery must prove server continuity")
contains(orphaned, "health.active_session ~= nil", "orphan recovery requires a proven-empty server")
contains(orphaned, 'health.state:lower() ~= "idle"', "orphan recovery requires an idle server")
contains(orphaned, "session.paths.delivery", "orphan recovery must reject contradictory delivery state")
contains(orphaned, "release_owner(session)", "a proven orphan must release the exact owner")
contains(orphaned, "owner_instance_uuid = owner.hammerspoon_instance_uuid", "reload recovery must bind the old owner")

local reconciliation = function_body("reconcile_owner", "handle_existing_owner")
contains(reconciliation, 'server_json("open", owner.session_id, owner.nonce)', "missing decisions must use idempotent open")
excludes(reconciliation, 'server_json("ensure"', "reload reconciliation must not start or fallback implicitly")

local recovery = function_body("server_process_alive", nil)
contains(recovery, 'return false, "SERVER_STILL_RUNNING"', "manual recovery must refuse a live server")
contains(recovery, "release_owner(session)", "manual recovery must release the exact validated owner")
contains(recovery, "session.paths.recovery", "manual recovery must retain the recovery shadow when present")

print("edit_anywhere static reconciliation checks: ok")
