# Virtual MPC Sample panel

A browser page shaped like the MPC Sample: the rear panel with its jacks on top, the top
panel with the live VM screen below, and a side rack of virtual gear the jacks can be
patched to. The page server plays the panel MCU on the control-surface UART, so the stock
`MPC Sample` receives exactly the frames the hardware sends.

```bash
python3 lab.py run      # starts the panel server too, then open http://127.0.0.1:8765/
python3 lab.py panel    # the panel server alone (e.g. after restarting it)
```

The device scales with the window width and the page scrolls; hide the side rack with the
⟩ button at the top right to give the device the whole width (the screen is then shown at
1:1 or larger with sharp pixels).

## Controls

| Input | Mouse | Keys |
|---|---|---|
| Pads 1–16 | click (higher on the pad = harder hit) | `Z X C V` 1–4, `A S D F` 5–8, `Q W E R` 9–12, `1 2 3 4` 13–16 |
| SHIFT | click | Shift |
| PLAY / STOP | click | Space / Esc |
| Buttons above the screen | click | F1 F2 F3 |
| Data encoder | drag or scroll; double-click = push | ← → turn, Enter push |
| K1 / K2 / K3 | drag or scroll | `7`/`M`, `8`/`/`, `9`/`'` up/down |
| Fader, MAIN VOLUME | drag or scroll | PgUp/PgDn, `]`/`[` |
| Other buttons | click | see the key legend at the bottom of the side rack |

The page shows what the app drives: button LEDs, pad colours, the pad-bank letter.
Clicks on the screen do nothing: the device has no touchscreen and the app ignores
pointer input (tested with the virtio tablet).

## Rear panel

Left to right as on the device: PHONES, SYNC OUT, MIDI OUT, MIDI IN, AUDIO OUT R/L, AUDIO IN
R/L, REC GAIN, USB, POWER, SD. Click a jack to patch it; cables are drawn to the rack.

| Jack | Can be patched to | What happens |
|---|---|---|
| MIDI OUT | MIDI monitor, loop cable | the External MIDI UART: everything the app sends is decoded in the monitor (notes, CC, clock with BPM, start/stop, SysEx); the loop cable echoes it into MIDI IN |
| MIDI IN | MIDI keyboard, loop cable | keyboard (2 octaves, channel, velocity), CC slider, Start/Stop, 120 BPM clock, all-notes-off. The app listens on USB by default: SHIFT + pad 8 (MIDI CONFIG), MIDI Port = External, Pad MIDI In = On (stock settings, saved by the app) |
| AUDIO OUT, PHONES | computer speakers, loop cable (AUDIO OUT) | channels 1–2 (AUDIO OUT, speaker; they follow MAIN VOLUME) or 3–4 (PHONES; fixed level, measured) play on the computer; with nothing plugged the built-in speaker plays, i.e. the computer |
| AUDIO IN | test tone, computer input, loop cable | fed into the app's capture; REC GAIN scales it; "computer input" is the microphone, recorded by the page (the browser asks for access); unplugged = silence |
| SD | click to insert / eject | the card is a copy of the host folder `sdcard/` (see "SD card") |
| POWER | click | the device's power key (QEMU `system_powerdown` = gpio-keys KEY_POWER): the app asks "Do you want to shut down?", saves on "Yes" and powers off; QEMU stays, the next click powers on |
| SYNC OUT, USB | — | not emulated (see below) |

Verified 2026-10-27: pad hits come out of MIDI OUT as `ch1 Note on/off`; notes sent to MIDI IN
reach the app; the MIDI and audio loop cables work; the test tone reaches the app's input at
the REC GAIN level.

Not emulated: **SYNC OUT** (the app has CV/Sync Out settings, but the hardware path is
unknown; channels 3–4 turned out to carry a second audio mix, not sync), **USB audio**
(the gadget's UAC2 function needs isochronous endpoints, which the lab's dummy_hcd lacks;
USB network and USB MIDI work, see README "USB-C"), **jack detection** (the app does not
see plugs; the lab only routes the audio). That PHONES is channels 3–4 is an
assumption from the measured levels.

## Protocol

Source of the input map: a community-documented control-surface protocol, verified on hardware
on 2026-10-22.
The map lives in `panel/controls.json`.

| From the panel | Frame |
|---|---|
| Pad hit / release | `99 note vel` / `89 note 00`; notes for pads 1–16: 37 36 42 82 40 38 46 44 48 47 45 43 49 55 51 53 |
| Button press / release | `90 note 7F` / `80 note 00` (channel 1) |
| K1 K2 K3, MAIN VOLUME, fader | `B0 cc value`, CC 16 17 18, 19, 20 (absolute, 7-bit) |
| Data encoder | `B0 64 01` = +1, `B0 64 7F` = −1; push = note 111 |
| Device Enquiry reply | `F0 7E 00 06 02 47 5C 00 00 01 00 00 33 F7` (MCU 1.0.0.051) |

| From the app | Meaning |
|---|---|
| `B0 note state` | button LED: single 0/1/≥2, bicolour 3·A+B |
| `B0 57..64 state` | pad bank A–H indicator |
| `F0 47 7F 5C 65 00 04 pad R G B F7` | pad colour, 7-bit components |
| `F0 47 7F 5C 42 00 00 F7` | sent at start, purpose unknown |

## Verified in the lab VM (2026-10-27)

| Check | Result |
|---|---|
| Pads, K1–K3, fader | screen reacts |
| SAMPLE, SEQ | mode changes, app updates the mode LEDs |
| PLAY, STOP | transport runs and stops, LEDs follow |
| PAD BANK | cycles A→B→C…, bank LEDs 57–64 follow |
| SAMPLE SELECT, encoder push | browser opens, folder opens |
| Encoder turn | moves the selected parameter; `7F` decreased End, so `01` = +1 |
| Buttons above the screen | switch Trim / Tune / Filter |
| Stock `az0x-firmware-tool` | reads MCU 1.0.0.051 from the emulator: "Firmware up-to-date" |
| Input latency | 40–100 ms from frame to screen |

Not verified: what you hear on the Mac (the level reaches −15 dBFS at the bridge on pad hits), pad pressure (`A9`), every button on every page, LED colours of the
bicolour dies (shown as lit/dim only), and the level meter LEDs (not mapped).

## SD card

Put samples (and projects, kits) in the folder `sdcard/` next to `lab.py`. Clicking the SD
slot on the rear panel inserts a card made from that folder; clicking again ejects it.
`lab.py run` inserts it at start when the folder is not empty.

```
sdcard/  --qemu-img (vvfat)-->  out/run/sdcard.img (FAT16, label "MPC SAMPLE", 512 MiB)
         --QEMU sdhci-pci + sd-card-->  /dev/mmcblk1p1  --stock edisksd-->  /media/MPC SAMPLE
```

- The guest side is the device's own path: the kernel sees an SD card on `mmc1`, stock
  `edisksd` mounts it at `/media/MPC SAMPLE` (nosuid, nodev, noexec, as on the device), and
  the app lists it as the card. Verified 2026-10-27.
- The card is a **copy**: inserting copies the folder again, and what the app saved on the
  previous card is lost. The image survives a VM restart, so a reboot keeps it.
- Limit: about 500 MB per card (QEMU's vvfat builds FAT16). The device card is exFAT.
- Same on all hosts: only `qemu-img` is needed, no 9p share.
- The app offers to run `autorun` scripts found on a card (device behaviour).

## Why the VM audio is snd-aloop

MPC Sample runs a 128-frame audio period and processes panel input in step with
the audio stream. On virtio-snd the stream failed with EIO about once a second, and
panel input then lagged by 1–3 s or seemed ignored. With `platform-sound` on
`snd-aloop` input takes 40–100 ms. The lab kernel runs `HZ=1000` like the device, but
full preemption instead of the device's `PREEMPT_RT`, so that aloop's period timer can run in
hard IRQ context (see "Clicks").

## Sound

```
MPC Sample --128-frame stream--> snd-aloop (platform-sound) <-- input: tone / loop / microphone
                                     |  both loopback pairs, same clock as the app
                          mpclab-audio-bridge (VM service, realtime threads)
                                     |  channels 1-2 or 3-4, S16 stereo, 5.8 ms chunks
                          virtio-serial "mpclab.pcm" = QEMU chardev, WebSocket server on 5557
                                     |
                          page's audio worker --MessagePort--> AudioWorklet --> speakers
                          page: microphone --> audio worker --> back the same way
panel server <--- routing / levels over virtio-serial "mpclab.audio" (TCP 5556) ---> bridge
```

- The app keeps its stream on `snd-aloop`, so its timing and the panel latency do not
  depend on the host. Measured with the bridge running: 70–110 ms from frame to screen
  (without it 115–260 ms in the same session, i.e. no penalty).
- No QEMU audio device: virtio-snd returned EIO under load and its blocking writes held
  up the bridge. The bridge never blocks on the host side: a full port drops a chunk.
- The page plays the sound after the first click or key press (browser autoplay rule; a
  badge shows until then). Its worklet keeps 320 frames (7 ms) queued and resamples by up to
  0.3 % to follow the guest's clock; after an underrun it raises the queue by 128 frames and
  lowers it again after 10 s without one. The status line shows the queue and the underruns.
- Sound bypasses the Python panel server and the page's main thread: measured, chunks left
  QEMU every 5.8 ms (p99 7 ms) but came out of a Python relay with gaps up to 16 ms. The
  context asks for the smallest output latency (`latencyHint: 0`; Chrome on macOS 2.9 + 8 ms
  instead of 5.8 + 16 with "interactive").
- Pad to sound, measured (Apple Silicon, HVF, 23 ms app buffer): the sound reaches the page
  ~18 ms after the pad frame, plus the worklet queue and the browser's output latency.
- The bridge opens the loopback only after the app's stream runs and takes its
  parameters (4 ch, S32_LE, 44.1 kHz in 1.3.0.12); opening first could make the app's own
  open fail.
- Levels go to the panel ~10 times a second (meters on the rear jacks and in the rack),
  with the bridge's counters: `drop` (host chunks dropped because nobody read the port)
  and `under` (the app's input ran dry).
- Sound plays on the browser's output device; with the page closed there is no sound.
  The microphone is opened only while AUDIO IN is patched to "computer input".

## Clicks (app stream restarts)

A click is an xrun: the app's 128-frame stream (playback buffer 384 frames = 8.7 ms, capture
256) is not serviced in time and the app restarts both streams. Measured causes and fixes
(2026-10-27, Apple Silicon, HVF), app restarts per minute:

| Cause | Evidence | Fix |
|---|---|---|
| QEMU started from the Claude desktop app's terminal pane | its threads dropped to priority 4 for seconds; 75 % of guest timer ticks 1-30 ms late | start the lab from Terminal.app / iTerm |
| stock snd-aloop timer (jiffies timer wheel) | fired 2-5 ms late ~100 times a second (diagnostic print) | patch 0002: hrtimer, in hard IRQ context on a non-RT kernel |
| virtio-snd host output | EIO under load, blocking writes | removed; sound goes to the page over virtio-serial |
| panel screendumps after each input | ~2x restarts while 30 fps PNG dumps ran (A/B twice) | PPM dumps (half the cost in QEMU's main loop) |
| vCPU stalls by the host | kernel trace: one timer interrupt's handlers logged 10 ms apart | not fixable in the guest; see below |

Left over: ~6-11 per minute with the page idle; more while the Mac is busy. Tried without a
measurable gain (and removed): raising ksoftirqd, `idle=poll`, vCPU threads at
user-interactive QoS, locking guest RAM against macOS memory compression, bigger host chunks.

`lab.py run` gives the app a 23 ms buffer (`snd_aloop.min_buffer=1024`, patch 0003): about
half the clicks in an A/B (11 -> 6 per minute), for ~15 ms more pad-to-sound latency, and in
use the clicks were nearly gone. `python3 lab.py run --extra snd_aloop.min_buffer=0` gives
back the device's 8.7 ms buffer.

Diagnostics: `CONFIG_FTRACE` is on (sched, timer, irq and `snd_pcm` xrun/hwptr events in
/sys/kernel/tracing); `tools/wakelat.c` measures how late a realtime thread wakes from 1 ms
sleeps (`/lab/bin/wakelat 60`).

MAIN VOLUME is software volume on this device (CC 19). The panel answers the app's
`0x42` request (and reports again whenever the panel reconnects) with MAIN VOLUME at
100/127, so sound is on after every start. The device's MCU reports all five pots, but the
lab sends only MAIN VOLUME: the app takes a K1-K3 or fader value as a move on the open page
(measured: CC 16 = 64 set the Trim start of the current pad to 50 %), and the lab's report
comes seconds after start, when a project may be loaded already. `lab.py run` starts the panel server before QEMU for this
reason: like the MCU it must be there when the app starts. This follows the MCU code (a function that reports CC
16–20 when the app asks over SysEx); that `0x42` is that request is an inference. The
report is sent 1.5 s and 5 s after `0x42`: sent immediately it was lost, because the app
writes `0x42` before it starts reading and the serial-midi driver drops input until then.
