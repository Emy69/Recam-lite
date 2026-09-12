# Tests

Engine-level tests: no network, no ffmpeg, no GUI. Every test runs against a
temporary `data/` and `grabaciones/` folder, so running them never touches the
real configuration, channel list, log or recordings.

## Running them

```
.venv\Scripts\pip install -r requirements-dev.txt
test.bat
```

or `python -m pytest`, `python -m pytest tests/test_platforms.py -k master`, …

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
