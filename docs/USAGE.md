# Using the lab

The full guide: commands, the factory library, USB-C, SD autorun packages, host
requirements, and why the VM behaves as it does. Start with the [README](../README.md).

## How the VM stays close to the device

- The eMMC is a GPT disk with the device's partition names and PARTUUIDs, so the
  stock mount units, `az0x-data-mkfs` and `az0x-factory-mkfs` work unchanged.
- The rootfs partition is byte-identical to the firmware image and is opened through
  dm-verity with the **stock** verity table and root hash taken from the signed FIT.
- Lab-only files (ssh, console autologin, network, audio bridge) live in a separate
  read-only ext4 layer, stacked with overlayfs by the lab initramfs. The merged root
  is read-only, as on the device.
- The device tree is QEMU's `virt` DTB plus the AC50 identity properties and two
  `serial-midi` nodes, as on the device: "Control Surface" (panel MCU) and "External MIDI"
  (rear MIDI jacks), each on its own PL011.
- The kernel is mainline 6.12.60 with PREEMPT_RT and HZ=1000 (the device runs 6.12.60-imb-rt), plus virtio.

## Lab-only kernel parameter: `nohz=off`

`lab.py run` boots the guest with a periodic tick. Without it the guest idles
tickless, and under HVF an idle vCPU wakes for a disk-completion interrupt 15–30 ms
late; the project load does ~2000 reads one after another and each paid that delay.

| Measured 2026-10-27 | tickless (default kernel) | `nohz=off` |
|---|---|---|
| 4 KiB O_DIRECT read in the VM, p50 / p90 | 0.63 / 31.7 ms | 0.02 / 0.09 ms |
| Cold start: project ready after a reboot | 42–46 s | 23 s |
| Warm start: app restart, files in page cache | 5.5 s | 5.0 s |
| Host CPU of QEMU, app idle | 10.5 % of one core | 25.9 % of one core |

The remaining cold-start time is the app itself: it loads all samples of the project
into RAM (127 files, 81.5 MB) and polls the background loader in 50 ms sleeps, so
with a cold page cache it waits about 18 s in those sleeps. To compare a cold start
with the device, time the device from power-on to a ready screen.
`python3 lab.py run --extra nohz=on` brings tickless idle back for a test (the later option on the command line wins).

## Use

Everything is one Python script, `lab.py` (Windows: `py lab.py ...`). The Makefile only wraps it.

```bash
python3 lab.py build        # Docker: kernel (~15 min once), audio bridge, disk images, factory library
python3 lab.py run          # the VM with a QEMU window; starts the panel too
python3 lab.py ssh          # root shell in the VM (the lab's own sshd, port 2022)
python3 lab.py preload LIB.so   # restart MPC Sample with LD_PRELOAD=LIB.so; --off to undo
python3 lab.py stop         # power key + "Yes": MPC Sample saves, shuts down, then QEMU exits
python3 lab.py reset        # next start from a clean eMMC (fresh /data and /factory)
```

**Switching off.** As on the device: press POWER on the panel's rear, answer "Yes" (the
button above the screen under it); MPC Sample saves the project and powers the system off.
QEMU keeps running (`-no-shutdown`) and the next POWER press switches the sampler on again.
`lab.py stop` and Ctrl-C in the `run` window do the same (and answer "Yes" themselves), then
close QEMU. Closing the QEMU window kills the VM without saving, like pulling the plug.

**Factory library.** The device's `/content` partition (factory kits, samples and demo
projects) is written at the factory and is not part of the firmware update. Copy it from
your own MPC Sample (below) and `lab.py build content` puts it on the lab's `/content`.

Open **http://127.0.0.1:8765/** for the virtual MPC Sample (PANEL.md); sound plays in
that page after the first click. Samples: put them in `sdcard/` and click the SD slot on the
rear panel. Nothing here writes to the physical device.

**macOS: start the lab from Terminal.app (or iTerm), not from inside another app's terminal
pane.** Measured 2026-10-27: QEMU started from the Claude desktop app's terminal tab had all
its threads dropped to priority 4 (background) for seconds at a time, and the guest then woke
1-30 ms late on 75 % of timer ticks; started from Terminal.app, 99.8 % were on time.

### Factory library (stock content)

The factory library is not in this repository and not in the firmware update. Take it from
your own MPC Sample:

1. Get root SSH on the device (a community root-SSH autorun package: OpenSSH over USB,
   192.168.155.1:2222, see "SD autorun packages" below).
2. Connect the MPC Sample to the computer over USB-C.
3. Copy `/content` (about 2.1 GB on the device: all factory kits, samples and projects).
   On macOS/Linux:

   ```bash
   mkdir -p ~/Downloads/MPC-Sample-Factory
   scp -O -P 2222 -i ~/.ssh/YOUR_KEY -r root@192.168.155.1:/content ~/Downloads/MPC-Sample-Factory/
   ```

   The copy only reads from the device; it does not modify the firmware or the factory files.
   Tested on firmware 1.3.0.12.
4. Put it on the lab's eMMC (the VM must be stopped):

   ```bash
   CONTENT_DIR=~/Downloads/MPC-Sample-Factory/content python3 lab.py build content
   ```

   Without `CONTENT_DIR` the step uses `$FW_DIR/content`. It rewrites partition 11 of
   `out/emmc.base.qcow2` as ext4 (`/content`, mounted read-only as on the device) and drops
   macOS litter (`.DS_Store`, `._*`). Your `/data` (projects, settings) is kept: it lives in
   `out/run/emmc.qcow2`. In the app the library shows up as on the device.

### USB-C: network, root SSH and SD autoruns

The stock USB gadget runs as on the device (`az0x-usb-gadget@ac50`): the MPC side `usb0`
gets 192.168.155.1/28 from the firmware's own network config and serves DHCP, and a
"computer" on the other end of the cable (the lab's emulated USB host, inside the VM) gets
192.168.155.10. USB MIDI is there too. Two lab changes make it work: kernel patch 0004
accepts inMusic's `shared_clock` attribute, and the lab layer carries a copy of the stock
gadget scheme without the UAC2 (USB audio) function, which cannot bind on the lab's USB
controller (`scripts/gadget_scheme.py`, generated from the firmware at build time).

Port 2222 on the Mac is forwarded to 192.168.155.1:2222, the address community root-SSH
autoruns listen on, so the device's instructions work with `127.0.0.1` in place of
`192.168.155.1`. The lab's own sshd moved to port 2022 (`python3 lab.py ssh`).

### SD autorun packages (zip)

Test an SD autorun package (root SSH, mods, your own script) the way a user runs it on
the device:

```bash
python3 lab.py autorun PACKAGE.zip --yes
python3 lab.py autorun --results
```

or the **SD autorun packages** card on the panel page (pick the zip, Run, Results).
The zip may hold `autorun/` at its root, one folder down (release archives) or only the
folder's contents. The lab then:

1. unpacks it (no absolute or `..` paths, at most 1 GiB);
2. makes an exFAT card labelled `MPCSAMPLE` with the firmware's own `fdisk` and
   `mkfs.exfat`, inside the VM, in RAM on a loop device (the app sees only the finished
   card) - `out/run/autorun-card.img`;
3. powers the MPC off (power key + Yes; if that does not finish in 60 s, e.g. a running mod
   blocks poweroff, a hard power-off like holding POWER), inserts the card and powers it on,
   as on the device: the stock `az01-script-runner` starts the package at power-on;
4. `--yes` presses the left button above the screen (the script's Yes) after 6 s, for
   packages that ask. `--no-reboot` inserts the card into the running MPC instead.
   `--device-label` labels the card `MPC SAMPLE` as the device does, so it mounts at
   `/media/MPC SAMPLE` (with the space) instead of `/media/MPCSAMPLE`.

Packages whose `autorun/autorun.json` has `"askQuestion": false` (root SSH does) start
silently. The card stays in across POWER off/on on the rear panel, so such a package starts
again at every power-on until the card is ejected, like a card left in the device.

`--results` shows the script runner's lines of this boot, the card's files and the tail of
the logs scripts leave (`autorun/*.txt`, `/run/*/events.txt`). The label has no space:
some loaders refuse a card mounted at `/media/MPC SAMPLE` (the device's own card label).

Tested 2026-10-27 (firmware 1.3.0.12) with community packages:

| Package type | Result in the lab |
|---|---|
| Root SSH over USB (OpenSSH on 192.168.155.1:2222) | Works: SSH starts by itself; `ssh -p 2222 -i KEY root@127.0.0.1` gives `uid=0(root)` on 192.168.155.1, `scp -O -P 2222 ...` copies files. |
| RAM-copy loader (unmodified app copy in RAM, automatic return) | Works: interface, sound, return to the stock app after 3 min. |
| Mod patching the RAM copy (K-value preview) | Works: in Sample, pressing the encoder shows the next K value in yellow for 2 s. |
| Mod session with a menu exit | Works: **Mod Off** appears in Project (SHIFT + pad 16) and returns to the stock app. |
| Pad play while sampling (LD_PRELOAD into the stock app) | Works: in Sample Record with source Resample, pads after the first one play and are recorded. |

Mods that patch a RAM copy of the app, built here, matched the published hashes of their
patches and patched app copies; their helper programs differ with the compiler, so the
`autorun.sh`/`autorun.check` that carry those helpers' hashes differ too.

### Your own code and firmware

[docs/CUSTOM_FIRMWARE.md](CUSTOM_FIRMWARE.md) is a step-by-step guide to SD autorun
packages, `LD_PRELOAD` libraries, lab layer files and another stock firmware version.

### Boot splash

At power-on the screen shows the stock MPC Sample splash (`/usr/share/akai/splash/mpc-sample.png`),
taken at build time from the initramfs in the stock kernel FIT and drawn by the lab initramfs
(`scripts/splash_raw.py`), until the app takes the display. The kernel logo is off.

### Host requirements

| | macOS (Apple Silicon) | Windows 10/11 | Linux |
|---|---|---|---|
| QEMU 9+ | `brew install qemu` | qemu.org installer, add to PATH | distro package |
| Docker (only for `build`) | Docker Desktop | Docker Desktop (WSL 2) | Docker |
| Python 3.9+, OpenSSH client | built in | python.org, OpenSSH is built in | distro packages |
| CPU acceleration | HVF | none for an ARM guest: TCG software emulation | KVM on arm64 hosts, else TCG |
| Sound | in the panel page (browser) | same | same |

`lab.py run` picks the accelerator itself (`--accel tcg` forces emulation). Under TCG the
guest CPU is a modelled Cortex-A55, the device's core. Measured on this Mac under TCG:
boot 28 s, project ready at 88 s, panel latency ~100 ms (same as HVF), but the app's
128-frame audio stream breaks and recovers about once a second (clicks), and QEMU uses
~1.7 host cores. On Windows/x86 expect the same order; it has not been run on Windows yet.

All host-side links are TCP on 127.0.0.1: panel UART 5551, MIDI UART 5552, console 5553,
QMP 5554/5555, audio bridge 5556, sound 5557, VNC 5910 (the page's screen), lab ssh 2022, the device's USB-network ssh 192.168.155.1:2222 -> 2222, panel web page 8765. The 9p share (`share/` =
`/lab` in the VM) exists on macOS/Linux only; nothing depends on it.

Host tools in `scripts/`: `cs.py` (raw bytes on the panel UART), `probe_cs.py` (scan control
ids), `qmp.py screendump x.png`, `con.py 'cmd'` (run a command on the console).
