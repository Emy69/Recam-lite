# Recam

**v0.0.1**

> **⚠ Test build.** This is an early test version and it records **Chaturbate only** for now. Expect rough edges — anything you can report back (bugs, confusing bits, ideas) genuinely helps development move forward. Thank you for testing!

A desktop app that watches **Chaturbate** channels, records their streams automatically the moment they go live, and helps you organize the results: live preview while recording, thumbnails, in-app playback with resume, renaming, filtering, and a recycle-bin-safe delete. The interface is NiceGUI in a native window, but the engine runs just as well without it. (Support for more platforms is already inside the engine and planned for later builds.)

> Personal use only. These platforms' terms of service do not allow recording or redistributing content; the files stay on your disk.

## Requirements

- Windows with Python 3.11+
- ffmpeg: either on the PATH (`winget install Gyan.FFmpeg`) or downloaded with one click from Settings → Tools, which tells you when it is missing

## Install

```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## Usage

Run `.venv\Scripts\pythonw.exe app.py` for the native window, or `python app.py`, which also serves http://127.0.0.1:8211.

- **Panel** — paste a channel URL and press Add. With *Auto* on it starts recording the moment the channel goes live; you can also force it with *Record now*. Channels show as a grid of tiles sorted by usefulness (recording first, then live), the recording ones with a live preview frame. An activity feed shows what happened while you were away.
- **Library** — recordings grouped into one collapsible section per profile, as thumbnail tiles with duration badges. Click a tile to play in the built-in player (±10 s skips, playback speed, remembers your volume and where you left off). Multi-select sends several recordings to the recycle bin at once. Raw `.ts` captures (interrupted recordings) convert to MP4 with one click.
- **Settings** — destination folder, quality, poll interval, file name template, interface language (English/Spanish), start with Windows, LAN access (watch the panel from your phone) and one-click updates of yt-dlp/streamlink.

Minimizing minimizes normally. The window **X** asks whether to hide the app to the tray (it keeps recording in the background) or quit for real — quitting finalizes captures in flight into clean MP4s.

## Headless (server / bot / CLI)

The engine needs no interface. With `python -m recam.cli`:

```
python -m recam.cli dashboard           # interactive terminal panel
python -m recam.cli run                 # daemon; Ctrl+C finalizes captures and exits
python -m recam.cli now                 # what is recording, from another terminal
python -m recam.cli stop <user>         # stop a capture in flight (or 'all')
python -m recam.cli add <url>           # add a channel
python -m recam.cli auto <user> off     # toggle auto-record
python -m recam.cli remove <user>       # remove a channel
python -m recam.cli list                # list the channels
python -m recam.cli status              # who is live right now
python -m recam.cli offset <ms>         # fine audio nudge (normally unnecessary)
```

`now` and `stop` talk to whichever process is recording through `data/`, so they work from another terminal while the GUI or the daemon stays up.

The **dashboard** is the Panel in terminal form, in real time:

| Key | Action |
|---|---|
| ↑ ↓ / 1-9 | select a channel |
| **+** (or n) | add a channel by pasting its URL |
| Del / ⌫ | remove the selected channel |
| space | toggle auto-record for the selection |
| A | toggle auto-record for everyone |
| r | record the selection now |
| s | stop the selection's capture |
| x | stop every capture |
| v | toggle global monitoring |
| q | quit (finalizes whatever is recording) |

Do not leave `dashboard` and `run` open at the same time: both would record the same channels.

## How it works

An asyncio service checks every X seconds who is live against each site's public APIs. If one does not answer, it tries anyway and lets the recorder decide.

Requests to the same site are spaced out (no bursts with many channels), and if a 429 still arrives the app backs off automatically with a growing wait — insisting only extends the punishment.

The capture goes to a `.ts`, which survives a power cut or a hard close: ffmpeg downloads the HLS directly and muxes audio and video as-is, no re-encoding. (The engine also carries support for other platforms, disabled in this build.)

While recording, a recent frame is pulled from the growing file every ~15 seconds as a live preview. When it ends: remux to MP4 (also a pure copy), thumbnail, and into the library.

**Audio sync.** Cam sites publish audio and video as two separate HLS playlists. Handing them to ffmpeg as two inputs makes it zero-shift each one independently: it opens the video first, spends a couple of seconds probing it, and by then the audio's live edge has moved on — the audio lands that far ahead, a different amount every capture. The fix is a **single input**: a small local master playlist with the chosen video variant and its audio rendition, so ffmpeg applies one common shift and the source's shared timeline survives intact (verified by cross-correlation: from 1.6 s of audio lead to frame-exact alignment). The manual nudge in Settings remains as a fine-tune and applies at MP4 conversion, shifting timestamps without touching samples.

Recorder processes are tied to a Windows job object: if the app dies hard, the OS kills them instead of leaving ffmpeg recording as an orphan.

## Where everything lives

- `grabaciones/<streamer>/` — the videos (configurable in Settings).
- `data/` — configuration, channel list, library cache and `recam.log`.

Neither goes into the repository.

## Author & feedback

Made by **Emy69**. This is an early test build — follow the project and send feedback:

- Patreon: https://www.patreon.com/c/emy69
- Discord: https://discord.com/invite/ku8gSPsesh
- GitHub: https://github.com/Emy69
- X: https://x.com/dev_emy
- Buy Me a Coffee: https://buymeacoffee.com/emy_69
