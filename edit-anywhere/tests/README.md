# Edit Anywhere tests

All automated tests use an isolated cache under `/tmp`. They start a headless
Neovim Server and attach through a private PTY; they never open Ghostty, press
the user's hotkey, focus a window, or paste into another application.

Run the functional suite:

```sh
python3 edit-anywhere/tests/test_server.py
```

Run the required warm benchmark (three discarded warmups and twenty retained
samples):

```sh
python3 edit-anywhere/tests/benchmark.py attach --warmups 3 --samples 20
```

The benchmark exits `0` when p50 is at most 60 ms, p95 is at most 100 ms, the
Server PID stays fixed, every session cancels correctly, and RSS growth is at
most 30 MiB. Exit `1` means a performance threshold failed; exit `2` means the
test facility or a correctness invariant failed.

For the real shortcut-to-visible measurement, start the passive collector and
then perform all shortcut presses yourself:

```sh
python3 edit-anywhere/tests/benchmark.py e2e-start --warmups 3 --samples 20
```

The collector snapshots the existing session set when it starts, then only
reads newly created `metrics/hammerspoon.json` files. It never presses the
shortcut, focuses a window, opens Ghostty, or sends keys. The first three
sessions are warmups; the next twenty are retained. Each retained metric must
contain matching protocol/session/process identity plus
`hotkey_to_qt_focused_ms`, `hotkey_to_ui_ready_ms`, and `end_to_end_ms`, where
the last value equals the maximum of the first two.

Raw records and the nearest-rank p50/p95/max/failure summary are written under
`~/.cache/edit-anywhere/benchmarks/<run-id>/`. The E2E thresholds are 300 ms at
p50 and 400 ms at p95. Exit `0` passes, exit `1` means a metric or threshold
failed, and exit `2` means collection was interrupted, timed out, or did not
obtain the requested number of unique sessions. `--timeout 0` waits until
Ctrl-C; `--cache-root` observes an alternate cache. A discovered session gets
six seconds by default to publish complete Hammerspoon metrics; adjust this
with `--session-timeout` when diagnosing unusually slow failures.

The collector itself has a no-GUI synthetic smoke test:

```sh
python3 edit-anywhere/tests/benchmark.py e2e-start \
  --warmups 1 --samples 3 --timeout 5 --self-test
```
