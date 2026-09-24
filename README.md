# Recam

**v0.2.0** · [Download for Windows](https://github.com/Emy69/Recam-lite/releases/latest) · [Changelog](CHANGELOG.md)

Recam watches Chaturbate channels and records them the moment they go live, then
keeps the results organised: thumbnails, in-app playback that remembers where you
left off, renaming, filtering, and a delete that goes to the recycle bin.

> **Free version.** It records **Chaturbate** only. The engine already handles
> other sites, and enabling them is planned for later releases.

> Personal use only. These platforms' terms of service do not allow recording or
> redistributing content. The files stay on your disk.

---

## Download and run

[**Get the latest release**](https://github.com/Emy69/Recam-lite/releases/latest),
unzip it anywhere, and run `Recam.exe`.

Nothing to install: ffmpeg comes bundled and you do not need Python. The app is
portable, so everything it writes stays in its own folder. Move that folder and
your settings and recordings go with it; delete it and nothing is left behind.

Windows 10 or 11, 64-bit.

## Using it

**Panel** is where you add channels. Paste a channel URL, press Add, and the
channel shows up as a tile.

- Turn **Auto** on and Recam records that channel by itself whenever it goes
  live. It is off on a new channel, so adding one only watches it.
- **Record now** starts a capture immediately.
- Tiles are ordered by what matters: recording first, then live, then the rest.
  A recording tile shows a live frame of what is being captured.
- The activity feed, behind the history icon in the header, says what happened
  while you were away.

**Library** is everything you have recorded, grouped into one section per
channel. Click a thumbnail to play it in the app: 10-second skips, playback
speed, and it remembers your volume and where you stopped. Select several at
once to send them to the recycle bin together.

A recording marked **RAW** is a capture that was interrupted, by a power cut for
instance. It is still watchable, and one click converts it to MP4.

**Settings** covers where recordings go, quality, how often channels are checked,
the file name template, interface language (English or Spanish), starting with
Windows, and access from your phone over the local network. If ffmpeg is ever
missing, Settings offers to download it in one click.

**Closing.** Minimizing just minimizes. The **X** asks whether to hide Recam to
the tray, where it keeps recording in the background, or to quit. Quitting
finishes and saves whatever is being captured first, so closing the window never
costs you a recording.

## Where your files are

Both folders sit next to `Recam.exe`:

- `grabaciones/<channel>/` for the recordings, which you can point elsewhere in Settings
- `data/` for the settings, the channel list and `recam.log`

## If something goes wrong

**A network warning, or "too many requests".** Chaturbate limits how often it can
be asked who is live, and does not publish the limit. Recam spaces its requests
out and waits longer whenever it is refused. With a long channel list, raise
**Check every** in Settings.

**A recording with no sound.** The log says so explicitly: look for a `NO AUDIO`
line in `data/recam.log`.

**Private and ticket shows cannot be recorded.** They are not served publicly.

Reporting anything odd genuinely helps decide what gets fixed next. Links are at
the bottom.

---

## Running from source

Only needed to change the code or to use the command line. The downloaded
release includes neither.

Requires **Python 3.11+** and ffmpeg, either on your PATH
(`winget install Gyan.FFmpeg`) or fetched from Settings → Tools.

```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Then `.venv\Scripts\pythonw.exe app.py` for the native window, or `python app.py`,
which also serves the interface at http://127.0.0.1:8211.

### Command line

The engine runs with no interface at all, which is what you want on a server or
behind a bot. **This only works from source**, since the packaged release ships
the app rather than a Python interpreter.

```
python -m recam.cli dashboard           # interactive terminal panel
python -m recam.cli run                 # daemon; Ctrl+C finishes captures and exits
python -m recam.cli now                 # what is recording, from another terminal
python -m recam.cli stop <user>         # stop a capture in flight (or 'all')
python -m recam.cli shutdown            # ask a running daemon to stop cleanly
python -m recam.cli add <url>           # add a channel
python -m recam.cli auto <user> off     # toggle auto-record
python -m recam.cli remove <user>       # remove a channel
python -m recam.cli list                # list the channels
python -m recam.cli status              # who is live right now
python -m recam.cli offset <ms>         # fine audio nudge (normally unnecessary)
```

`now`, `stop` and `shutdown` talk to whichever process is recording through
`data/`, so they work from a second terminal while the app or the daemon keeps
running.

`dashboard` is the Panel in terminal form, live:

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
| v | toggle automatic monitoring |
| q | quit (finishes whatever is recording) |

Do not leave `dashboard` and `run` open at the same time: both would record the
same channels.

### Tests

```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest
```

No network, no ffmpeg and no browser involved. See [tests/README.md](tests/README.md).

---

## How it works

An asyncio service asks Chaturbate's public API who is live every few seconds.
Requests to one site are spaced out so a long channel list does not arrive as a
burst, and if the site answers 429 anyway the app waits longer before asking
again, since retrying during a refusal only extends it. The spacing it learns
that way is remembered between runs.

A capture is written straight to a `.ts` file, which survives a power cut or a
hard close. ffmpeg downloads the HLS stream and copies audio and video as they
are, with no re-encoding, so recording costs almost no CPU. When the broadcast
ends the file is remuxed to MP4, also a plain copy, given a thumbnail, and added
to the library. While recording, a recent frame is pulled out of the growing file
every 15 seconds or so: that is the live preview on the tile.

Recorder processes are tied to a Windows job object, so if Recam dies hard the
system kills them with it instead of leaving ffmpeg recording forever in the
background.

**Audio sync.** Cam sites publish audio and video as two separate HLS playlists.
Given both as separate inputs, ffmpeg starts each one at zero on its own: it
opens the video first, spends a couple of seconds probing it, and by then the
audio's live edge has moved on, so the audio ends up ahead of the picture by a
different amount every time. Recam instead builds one small local playlist
holding the chosen video and its matching audio and hands ffmpeg that single
input, so one common shift applies to both and the original timing survives.
Measured by cross-correlation: 1.6 s of audio lead before, frame-exact after. The
manual nudge in Settings stays as a fine-tune and applies when the file is
converted, shifting timestamps without touching the audio itself.

## Author

Made by **Emy69**.

- Patreon: https://www.patreon.com/c/emy69
- Discord: https://discord.com/invite/ku8gSPsesh
- GitHub: https://github.com/Emy69
- X: https://x.com/dev_emy
- Buy Me a Coffee: https://buymeacoffee.com/emy_69
