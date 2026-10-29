# PoC results — MPC Sample in QEMU (2026-10-27)

Firmware 1.3.0.12, host Apple Silicon (10 cores, 16 GB), QEMU 11.1.2 with HVF, Docker 29.9.
Lab kernel: mainline 6.12.60 + `kernel/patches`, `-mpclab`, all built in.

## Outcome

The stock `/usr/bin/MPC Sample` runs in the VM, started by the stock `ac50.service`
through the stock `launch-ac50`, and draws its real UI on a 320×240 virtio-gpu screen.
Control-surface bytes injected from the host change that UI. Our existing
`libmpc_lab_screen.so` (V9.7.25) loads through `LD_PRELOAD` and installs its JUCE hook.

(Screenshots of this run live in `out/shots/` and are not committed.)

| What | Result |
|---|---|
| Stock rootfs integrity | byte-identical partition, opened by dm-verity with the **stock** table and root hash from the signed FIT; boots |
| Boot to shell | ≈4 s to autologin on hvc0; stock systemd 255, stock units |
| `/data`, `/factory` | created by stock `az0x-data-mkfs` / `az0x-factory-mkfs` on the blank virtual eMMC (`mkfs.ext4 -O encrypt`) |
| `ac50.service` | active, no restart loop |
| Display | KMS dumb buffers: `CREATE_DUMB`, `ADDFB`, `SETCRTC`, continuous `PAGE_FLIP` on `/dev/dri/card0` |
| Control surface | app opens `/dev/snd/midiC2D0` (R and W) and reads with timestamped rawmidi; injected bytes arrive and change the screen |
| Audio | app finds `/dev/snd/by-path/platform-sound`, opens it as 44100 Hz, 4 in / 4 out, 128-sample buffer |
| LD_PRELOAD | `V9725_LIVE_JUCE_HOOK_INSTALLED READ_ONLY 320x240 NO_MODEL_WRITES`; Build-ID gate rejects the library in child processes, as designed |
| ssh / 9p share | `make ssh`; host `share/` is `/lab` in the VM |

## Hypotheses from the architecture document

| # | Status | Evidence |
|---|---|---|
| H1 KMS without rockchipdrm | **PROVEN** | strace DRM ioctls above; UI visible |
| H2 320×240 mode | **PROVEN** for 320×240 (`virtio-gpu xres/yres`); other modes not tried |
| H3 rawmidi named "Control Surface" is the panel | **PROVEN** after kernel patch 0001: mainline names the port "Serial MIDI 0-0"; the inMusic module uses the DT `label` (strings `got label %s`, `%s at serial%d`) |
| H4 UI without MCU answers | **PROVEN**: full UI with a silent MCU |
| H5 firmware-tool does not block | **PROVEN**, with a caveat: with no Device Enquiry reply it assumes MCU `0.0.0.000`, starts an update, `midifirmup` pings the bootloader (`F0 00 00 17 70 F7` ×10), gets no answer and gives up (“Device not found”). An MCU emulator must answer the enquiry with a version ≥ `1.0.0.051`, or every boot runs the update attempt |
| H6 missing devices not fatal | **PROVEN** for RGA (`Raster graphics accelerator is not available`), `/dev/tee0`, `/dev/rga2`, splash socket, UAC2 gadget card |
| H7 empty `/content` | **PROVEN** not fatal; the app opens its demo/temp project from `/data` |
| H8 audio card selection | **PROVEN**: exactly `/dev/snd/by-path/platform-sound`, polled once per second; solved by a lab udev rule on the virtio-snd card |
| H9 mouse as touch | **DISPROVEN**: the app ignores virtio-tablet clicks (the device has no touchscreen) |
| H10 LD_PRELOAD via `launch-ac50` | **PROVEN** |

## Control-surface traffic captured at app start (app → MCU)

```
F0 00 00 17 70 F7                      x10, midifirmup bootloader ping
F0 47 7F 5C 42 00 00 F7                init frame (matches static analysis)
F0 47 7F 5C 65 00 04 pp RR GG BB F7    pad colour, pp = 00..0F pads, 10 = extra LED
B0 id vv                               button LED, channel 1, vv = 0..3
```

Input (MCU → app), first probe with `scripts/probe_cs.py B0 0 127` (value 7F then 00):
CC 25 and 29 set a parameter value overlay (100 % / 0 %), CC 26 and 30 change the
selected field. CC on channel 16 ids 0–40 had no visible effect. Superseded: the full, hardware-verified input map
is in docs/PANEL.md (pads `99`, buttons `90`/`80` on channel 1, knobs CC 16–20, encoder CC 100). The early probe results
above were skewed by the audio problem: input was processed seconds late.

## Differences from the device that remain

| Item | Effect | Next step |
|---|---|---|
| USB gadget fails: `USBG_ERROR_NO_ACCESS` | no `UAC2Gadget` card, no `f_midi` port, no ncm | stock scheme sets uac2 `shared_clock`, absent in mainline 6.12: a second inMusic kernel patch to reproduce |
| ~~virtio-snd EIO~~ | **fixed**: `platform-sound` now points at `snd-aloop`; with virtio-snd the stream restarted every second and panel input lagged 1–3 s (docs/PANEL.md) | done: `mpclab-audio-bridge` copies aloop to virtio-snd → CoreAudio without changing the app's timing |
| No initramfs of the device | stock init (startup-mode, splash, `/run/initramfs`) not exercised | try the stock initramfs from the FIT later |
| Root is overlayfs over dm-0, not dm-0 | `/proc/self/mountinfo` differs | acceptable; `mpclab.layer` can be omitted to boot pure dm-0 |
| Guest boots with `nohz=off` (not on the device) | fixes 15–30 ms late wake-ups of idle vCPUs under HVF; cold project load 42–46 s → 23 s; +15 % of one host core while idle (README) | look for a QEMU/HVF-side fix that keeps tickless idle |
| Kernel has many unused drivers built in | size, build time | trim config (now PREEMPT_RT + HZ=1000, as on the device) |
| ~~Firmware-tool update attempt each boot~~ | **fixed** while `make panel` runs: the panel answers Device Enquiry, the stock tool reports "Firmware up-to-date at 1.0.0.051" | — |

## Reproduce

```bash
python3 lab.py build && python3 lab.py run
python3 lab.py ssh
python3 lab.py preload ~/Downloads/mpc-lab-v9-7-25-physical-live-screen/libmpc_lab_screen.so
python3 lab.py preload --off
python3 scripts/cs.py --listen 5 B0 1E 7F B0 1E 00
python3 scripts/qmp.py screendump out/shots/now.png
python3 lab.py stop && python3 lab.py reset   # clean /data and /factory on next boot
```
