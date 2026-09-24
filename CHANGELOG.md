# Changelog

## v0.2.0

Still Chaturbate only. The engine supports the other sites, but this build does
not enable them.

### Recording

- **A poll the request throttle turned away no longer triggers a capture
  attempt.** With more channels than one pass could poll, every channel the
  throttle had just protected went on to resolve the same endpoint anyway,
  through the priority lane. The request rate climbed with the list instead of
  staying flat: 100 a minute with 50 channels, against a limit the site starts
  enforcing around 60. That is what earned the 429s.
- A pass now polls whoever has waited longest first, so a list too long for one
  window is covered evenly instead of the same names at the bottom never being
  looked at.
- A poll that was never sent no longer repaints a channel known to be live as
  unknown.
- Wider adaptive spacing between requests, and idle channels are polled less
  often, to keep a pass short.
- The poll interval is the period of a pass rather than a pause between passes.
- The settings file is validated on load: a value of the wrong type or out of
  range no longer reaches the engine and kills the watch loop.
- Several ways a recording could be lost are closed.

### Interface

- The Panel, Library, Settings and dialogs are restyled.
- The Panel groups channels into live and not broadcasting, with how long each
  has been on and a still of the room.
- Only the tiles that actually changed are redrawn, so the page no longer
  flickers on every update.
- The Library lays profiles out in a grid.
- Empty, error and missing-tool states everywhere they were missing.
- Right-clicking the URL box offers Paste and Paste-and-add.
- Stopping a recording asks first.
- **A newly added channel starts with auto-record off**, so adding one does not
  start recording it by itself.

### Tools

- Settings detects a missing ffmpeg or ffprobe and offers a one-click download.
- A live room's still is fetched once per poll instead of every 30 seconds.

### Command line

- `shutdown` stops a running daemon cleanly.

### Packaging and docs

- The Spanish README and both tutorials are gone, leaving one README to keep up
  to date instead of four documents.
- The `.bat` launchers are gone. They only ran `python -m recam.cli`, which
  works the same on every machine.

### Tests

- A test suite for the engine, and one for the Panel, Library and Settings
  built against a simulated client, with no browser involved.
- A release gate that fails when a site is half-enabled, when the version is
  bumped in one place and not the others, when a package the code imports is
  one the frozen build would not ship, or when a translated string loses one of
  its two languages.

## v0.0.1

First test build. Chaturbate only.
