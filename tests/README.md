# Tests

No network, no ffmpeg, no browser. Most of them are engine-level;
`test_ui_pages.py` builds the real tabs against NiceGUI's simulated client and
`test_release.py` checks the build itself. Every test runs against a temporary
`data/` and `grabaciones/` folder, so running them never touches the real
configuration, channel list, log or recordings.

## Running them

```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest
```

or `python -m pytest tests/test_platforms.py -k master`, … to narrow it down.

## Layout

| File | Covers |
|---|---|
| `conftest.py` | isolation fixtures, fake HTTP client, sample channels |
| `test_config.py` | settings load/save/validation, name templates, path sanitizing |
| `test_models.py` | channel status machine and the live/offline timeline |
| `test_platforms.py` | URL detection, liveness polling, the 429 throttle, the master-playlist builder, the record command |
| `test_recorder.py` | capture paths, remux, thumbnails, duration/audio probes, finalize |
| `test_library.py` | scanning, metadata cache, rename, delete, ts→mp4 |
| `test_monitor.py` | the watch cycle, auto-record rules, cooldowns, failure handling |
| `test_status_cli.py` | `data/status.json`, cross-process orders, every CLI subcommand |
| `test_tui.py` | dashboard keyboard handling |
| `test_logbook.py` | the log file |
| `test_throttle_budget.py` | the request budget: what a pass may send, and the rotation |
| `test_ui_pages.py` | the Panel, Library and Settings tabs, the dialogs, both languages |
| `test_release.py` | the release gate: see below |

## Before a release

`test_release.py` is the pre-upload checklist, as tests. It fails when:

- another site slips into `ENABLED_PLATFORMS`, or a disabled one becomes
  addable again — this build records Chaturbate only
- an enabled platform is only half-wired (no colour, no tile tag, no poll)
- `__version__` is bumped but a hard-coded version is left behind in the README
  or the CLI banner (three spots today)
- the code imports a package `build_exe.py` does not freeze, or imports one it
  excludes — an exe that only dies on someone else's machine
- a `t()` string loses one of its two languages, or the two halves disagree on
  their `{}` placeholders, which throws for Spanish users only
- a `breakpoint()` survives into a shipped file

## Bugs these tests caught, and where the regression lives

| Bug | Test that would catch it again |
|---|---|
| A setting of the wrong type or out of range reached the engine and killed the watch loop | `test_config.py::test_load_coerces_numeric_settings`, `::test_load_clamps_settings_that_are_out_of_range`, `::test_load_keeps_the_default_for_a_setting_of_the_wrong_type` |
| Converting a `.ts` overwrote an unrelated `.mp4` with the same stem | `test_library.py::test_convert_ts_does_not_overwrite_another_recording`, `::test_free_path_steps_aside_instead_of_overwriting` |
| Renaming `clip.ts.mp4` dropped the `.ts`, hiding a file browsers cannot play | `test_library.py::test_rename_keeps_the_raw_capture_marker`, `::test_full_suffix` |
| A blank rename silently produced `_.mp4` | `test_library.py::test_rename_refuses_an_empty_name` |
| A lookalike domain (`not-chaturbate.com`) was accepted as a channel | `test_platforms.py::test_detect_rejects_lookalike_domains` |
| The metadata cache was pruned by string prefix, wiping a sibling folder's entries | `test_library.py::test_scan_only_prunes_its_own_folder` |
| Negative input rendered as `-1:59:55` | `test_tools.py::test_human_helpers_clamp_negative_input` |
| A channel named `con`/`aux`/`nul` mapped to a folder Windows refuses to create | `test_config.py::test_sanitize_segment_guards_windows_device_names` |
| Two orders queued in the same clock tick shared a filename, so one was lost | `test_status_cli.py::test_orders_sent_in_the_same_instant_are_all_kept` |
| A poll the throttle refused fell through to a capture attempt, which resolved the same endpoint through the priority lane: the request rate grew with the channel count (42/min at 21 channels, 100/min at 50) instead of staying flat, and earned the 429s the throttle exists to avoid | `test_throttle_budget.py::test_a_poll_the_throttle_refused_sends_nothing_at_all`, `::test_one_pass_never_outruns_the_throttle_window` |
| A refused poll was stamped as checked, so the same bottom-of-the-list channels were turned away every pass and never polled at all | `test_throttle_budget.py::test_the_channels_turned_away_lead_the_next_pass` |
| A refused poll repainted a channel known to be live as UNKNOWN | `test_throttle_budget.py::test_a_refused_poll_leaves_the_channel_as_it_was` |
