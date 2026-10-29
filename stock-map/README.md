# Stock screen map

A map of every stock MPC Sample 1.3.0.12 screen, in English, Russian and Chinese: which button or
SHIFT combination opens what, what SCREEN 1-3 and K1-K3 do there, how to leave it, and what
the function is for (a summary of the [Akai user guide](https://www.akaipro.com/guides/mpc-sample/)).
56 sections, 383 screenshots taken in this lab from the stock app, no mod running.

## Open it

`index.html` is one self-contained file (6.4 MB): pictures, styles and script are inside it.

- Double-click `stock-map/index.html`, or drag it into a browser. No server, no install, no
  internet needed (without internet the page uses system fonts).
- To pass it on, send `index.html` alone; it opens the same from a chat attachment, a USB
  stick or a zip on Windows.
- Or serve the folder: `python3 -m http.server` in `stock-map/`, then http://localhost:8000.

RU / EN / 中文 at the top switches the language (the choice is remembered, the first visit follows
the browser's language). The search box filters by button or word; a picture opens enlarged
on click, Esc closes it.

## What is in a section

- The screen the button opens (the shot after one press from the Sample screen).
- **What it does**: from the Akai guide, with a link to its page there.
- The SCREEN 1-3 and K1-K3 labels of that screen, DATA, how to leave it.
- **In the shot**: what the screenshot itself shows.
- The screens reached inside: SCREEN 1-3, each pressed twice, SHIFT + SCREEN 1-3, DATA push
  and one DATA step. Presses that changed nothing are listed on one line.

At the top, buttons are grouped by kind (a screen of their own, a popup, an icon by the
sample name, no new screen) and listed in panel order.

Not covered: pad hits and pairs such as ERASE + pad, menus deeper than one level, external
MIDI. 1000 shots were taken; most repeat (each path starts from the same screen) or show no
change, which leaves 286 different screens.

## Rebuild it

Two steps, with the lab running (`python3 lab.py run`) and Pillow installed
(`pip install pillow`):

```bash
python3 tools/stock_walk.py
```

Photographs every stock screen into `out/stockmap/` (about 45 minutes). Run it on the stock
app only: no SD card in the panel, no `lab.py preload`. It presses real functions (Normalize,
Extract, Save Sample, Half Seq, ...), so it first copies the project to `/data/.stockmap-backup`
and puts it back whenever it cannot return to the Sample screen and at the end. Samples it
saves stay in `/data/UserContent/Samples/User`. Don't touch the panel while it runs: a pad hit
lands in the shots. Rerunning resumes after the last path done.

```bash
python3 stock-map/build/build.py
```

Writes `stock-map/index.html` from the shots and from:

| File | What |
|---|---|
| `build/notes_ru.json`, `build/notes_en.json` | Per button: the screen name, what it does, SCREEN/K labels, DATA, exit, what the shot shows, what each inner step did, the guide page |
| `build/order.json` | Panel order of the index |
| `build/site/` | Page template: `index.html`, `styles.css`, `app.js` |

A new firmware version: run the walk, look through the new shots (`out/stockmap/*.png`, paths in
`walk.json`) and update the notes.
