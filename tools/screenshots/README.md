# Screenshot tooling

The annotated images in `docs/images/ui/` are generated, not hand-made. Both usage guides share
them, so a number means the same thing in every language.

```sh
pip install -e ".[docs]"                     # Pillow (Chrome must be installed too)
python tools/screenshots/capture.py all      # throwaway server, worker and jobs → raw captures
python tools/screenshots/build.py            # red boxes and numbers → docs/images/ui/
```

- `capture.py` starts its own server on a free port with its own data directory, runs a handful of
  demo jobs, drives headless Chrome, and writes raw PNGs, element coordinates and terminal output
  to `$RCM_SHOT_WORK` (default `<tmp>/rcm-screenshots`). Stages run one by one — `setup`,
  `history`, `round1`, `round2`, `terminal`, `store`, `down`, `schedule`, `teardown` — or all at
  once with `all`.
- The rcm processes it starts get a `HOME` inside the work directory and an explicit
  `RCM_SERVER`, so they never read or write your own `client.toml`, submission file or pinned
  client version, and never find another server. Chrome keeps your `HOME`: headless Chrome on
  macOS cannot open an http page from a different one.
- `store` draws the Store tab with the real page but sample data: a throwaway server has no
  release scripts, secrets or stores, so the `/api/repos/…` answers come from the browser tests'
  stubs (`tests/test_web_browser.py`) with a small overlay in `capture.py`. `schedule` restarts the
  server with a bare repository, a `golive` preset that exits 75 after printing an action, and a
  notify hook that fails, then captures the Scheduled section and `rcm top`.
- The time field on the version page is drawn in the operating system's locale, whatever the page
  language; captures on a machine set to another language show the date in that language.
- It never shows your machine: the host name is fixed to `macmini`, and paths, ports, LAN
  addresses and tokens are rewritten in the captured text. Look at the images before committing
  anyway.
- `RCM_SHOT_ADVERTISE=1` binds the throwaway server to `0.0.0.0` and advertises it, which is what
  makes a real `rcm discover` capture possible. Use it only where no other rcm server advertises,
  and only on a network you trust. Without it `rcm discover` is not captured at all (it would only find
  other servers), and `build.py` keeps the existing `cli-discover.png`.
- `build.py` fails if an image goes over 400 KB, and the numbers it draws are the ①②③ of
  `docs/usage.md` and `docs/usage.ko.md`.

The demo presets under `scripts/` are the ones the captures run; they only print step markers and
sleep.
