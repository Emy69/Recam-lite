# RecordBate — Tutorial

*Versión en español: [TUTORIAL.es.md](TUTORIAL.es.md)*

A step-by-step guide from zero to your first automatic recording.

## 1. Install the prerequisites

You need **Python 3.11+** and **ffmpeg** on Windows.

```
winget install Python.Python.3.12
winget install Gyan.FFmpeg
```

Close and reopen the terminal afterwards so the PATH updates.

## 2. Set up the app

From the RecordBate folder:

```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

That creates a private Python environment and installs everything the app needs (NiceGUI, streamlink, yt-dlp, etc.).

## 3. First run

Double-click **`run.bat`**. A native window opens with three tabs: **Panel**, **Library** and **Settings**.

- `run.bat` starts the app silently (no console).
- `run-debug.bat` starts it with a console attached — use it if something fails and you want to see why.

The interface is in English by default. To switch to Spanish: **Settings → Language → Español → Save settings**, then reload.

## 4. Add your first channel

1. Copy a channel URL from your browser — for example `https://www.twitch.tv/somechannel` or `https://chaturbate.com/somemodel`.
2. Paste it into the box at the top of the **Panel** and press **Add** (or Enter).

The channel appears as a tile with its platform badge and status: **LIVE**, **OFFLINE**, **UNKNOWN** or **RECORDING**.

## 5. Automatic recording

Every channel tile has an **auto** switch (bottom right). With it on, the app checks the channel on every cycle (60 s by default) and starts recording the moment it goes live — no clicks needed. The moving parts:

- **Automatic monitoring** (top switch): the global master switch. Off = no checks, no new recordings.
- **Check now**: polls every channel immediately and tells you how many are live.
- **Record now** (⏺ on a tile): tries to record right now, even if the status says UNKNOWN. If nothing is live, the attempt cancels itself.
- **Stop** (⏹ on a recording tile): stops and saves. That channel's auto-record pauses for 10 minutes so the app doesn't fight you.

While a channel records, its tile shows a **live preview frame** that refreshes every ~15 seconds plus a running counter of time and size. The **activity feed** at the bottom keeps a short history: what started, what got saved, what failed.

## 6. The Library

Recordings are grouped into **one collapsible section per profile**. Inside each section they show as thumbnail tiles with a duration badge.

- **Click a tile** to play it in the built-in player: ±10 s skips, playback speed (1× → 2×), and it remembers your volume and where you left off watching.
- The **⋮ menu** on each tile: open with your system player, show in folder, rename, send to the recycle bin.
- **Sort** (newest, largest, longest…), **search**, and a **streamer filter** at the top.
- **Bulk delete**: press the ☑ button, tick several recordings (clicking a tile toggles it), then *To recycle bin*. One confirmation, and everything goes to the Windows recycle bin — nothing is ever hard-deleted.
- A tile marked **RAW** is an unfinished `.ts` capture (a crash or power cut). Click it to convert it to a clean MP4.

## 7. Settings worth knowing

- **Recordings folder** — where the videos go. The default template `{streamer}/{date} {time} [{platform}]` sorts them into one folder per profile.
- **Quality** — best / 1080p / 720p / 480p.
- **Concurrent recordings** — the cap on simultaneous captures (default 4).
- **Start with Windows** — the app launches at sign-in and starts watching your auto channels.
- **LAN access** — with it on (and a restart), open `http://<your-pc-ip>:8211` from your phone to watch the panel remotely.
- **Update yt-dlp and streamlink** — these sites change often; if a platform stops recording, update here and restart.

## 8. Closing, hiding and the tray

- **Minimize** minimizes to the taskbar, nothing else.
- The window **X** asks: **Hide** (the app keeps recording in the background; bring it back from the tray icon) or **Quit for real** (captures in flight get finalized and saved first).
- The tray icon menu also has *Show RecordBate* and *Quit*.

## 9. Command line (optional)

Everything works without the GUI. From the app folder, `recordbate-cli.bat`:

```
recordbate-cli dashboard      # full-screen terminal panel with keyboard controls
recordbate-cli run            # headless daemon (for a server or background use)
recordbate-cli now            # see what is recording from another terminal
recordbate-cli stop all       # stop every capture (each one is finalized)
```

The CLI shares configuration and channels with the GUI. Don't run the GUI and `run`/`dashboard` at the same time — they would record the same channels twice.

## 10. Troubleshooting

| Symptom | What it means / what to do |
|---|---|
| A channel shows *rate limited (429)* | The site is throttling you. The app backs off automatically and retries; nothing to do. |
| A Stripchat channel says *encrypted stream* | That stream uses Stripchat's Mouflon encryption and cannot be recorded without a key the site rotates constantly. Not fixable from here. |
| *NOT saved — only X KB* | The attempt never got real data: the stream wasn't public, or it ended immediately. The scraps get cleaned up on their own. |
| A platform suddenly stops recording | The site changed something. **Settings → Update yt-dlp and streamlink**, restart, try again. |
| You want to know why a capture ended | Tile menu → **View log**: the exact command, exit code and the recorder's last lines. The full history is in `data/recordbate.log`. |
| Audio out of sync | It shouldn't happen anymore (see the README). If a specific file sounds shifted, play it in VLC and tune with `j`/`k`, or set a nudge in Settings for future conversions. |
