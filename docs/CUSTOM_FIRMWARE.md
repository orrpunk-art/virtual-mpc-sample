# Running your own code and firmware in the lab

The lab boots the stock MPC Sample firmware unchanged: the stock rootfs is checked block by
block with dm-verity against the root hash in the stock kernel FIT, exactly as on the device.
Your code goes on top of it in one of four ways, from the least to the most invasive. None of
them writes to the firmware images in `FW_DIR`, and none of them talks to a physical MPC.

| Way | What it changes | Survives a power-off | Same as on the device |
|---|---|---|---|
| 1. SD autorun package | whatever the script does, in RAM | no (the card runs it again at power-on) | yes |
| 2. `lab.py preload` | `LD_PRELOAD` library in the stock app | no | needs root (SSH) on the device |
| 3. Lab layer file | any file of the root filesystem | yes, until you rebuild | no (lab only) |
| 4. Another firmware version | the whole stock system | yes | yes |

## 1. SD autorun package

This is how mods reach a real MPC Sample: a card with an `autorun/` folder, run by the
firmware's own `az01-script-runner`.

1. Make the folder:

   ```
   autorun/
     autorun.json    which script to run, and whether to ask first
     autorun.sh      your script (runs as root)
     autorun.check   sha256(autorun.json + autorun.sh + runner salt), see below
     ...             your files (libraries, binaries)
   ```

   `autorun.json`:

   ```json
   {
     "value0": {
       "scripts": [
         {
           "path": "autorun.sh",
           "askQuestion": false,
           "message": "Start my mod?",
           "yesButtonText": "Start",
           "noButtonText": "Cancel"
         }
       ],
       "files": []
     }
   }
   ```

   - `"askQuestion": false` runs the script silently when the card is in, including at
     power-on. `true` shows `message` with the two buttons first.
   - Keep the three dialog fields even when `askQuestion` is `false`; published silent
     packages keep them as well.
   - `autorun.check` is the lowercase hex SHA-256 of the exact bytes of `autorun.json`,
     then `autorun.sh`, then the ASCII salt `f7bed1c9-7e90-4731-9194-c996666b4331`. Any
     edit to either file needs a new check.

2. Zip it. `autorun/` may be at the zip's root or one folder down.

3. Run it in the lab:

   ```bash
   python3 lab.py autorun my-mod.zip           # power off, card in, power on
   python3 lab.py autorun my-mod.zip --yes     # ...and press Yes, for packages that ask
   python3 lab.py autorun my-mod.zip --no-reboot   # insert into the running MPC instead
   python3 lab.py autorun --results            # runner log, card files, /run/*/*.log
   ```

   On the panel page, the **SD autorun packages** card does the same.

4. The lab builds an exFAT card labelled `MPCSAMPLE` with the firmware's own `fdisk` and
   `mkfs.exfat`. The card stays in across POWER off/on, so a silent package starts again
   at every power-on, until you eject the card (SD slot on the rear panel) or start the lab
   again.

5. Keep the script RAM-only, as the published packages do: copy into `/run`, add systemd
   drop-ins under `/run/systemd/system`, and check the SHA-256 of the app binary and of your
   own files before changing anything. A power-off then always returns to stock.

The lab's own `pad-resample` package (play the pads while sampling with the Resample
source) is a complete example: an `LD_PRELOAD` library, a script that adds the `/run`
drop-in and restarts the app, and a build script that writes `autorun.check`.

## 2. `LD_PRELOAD` library in the stock app

This is the fastest loop while developing a library.

1. Cross-compile for AArch64 against the device's glibc and alsa-lib. The lab's Docker
   image `mpclab-tools` (made by `lab.py build`) has `aarch64-linux-gnu-gcc` and the arm64
   alsa-lib headers:

   ```bash
   docker run --rm -v "$PWD":/w mpclab-tools \
       aarch64-linux-gnu-gcc -O2 -fPIC -shared /w/mylib.c -o /w/mylib.so -ldl -lasound
   ```

2. Load it:

   ```bash
   python3 lab.py preload mylib.so
   ```

   It copies the library to `/run/mpclab-preload/`, adds a `/run` drop-in to
   `ac50.service`, restarts MPC Sample and checks that the library is mapped.

3. Undo it with `python3 lab.py preload --off`, or power off.

## 3. A file in the lab layer

`overlay/lab-layer/` becomes a read-only ext4 image (`out/lab-layer.ext4`). The lab
initramfs stacks it above the verified stock rootfs (`lowerdir=/layer:/lower`), so a file
there replaces the stock file at the same path. The lab uses this for its sshd, network,
audio bridge and its copy of the USB gadget scheme.

1. Put the file under `overlay/lab-layer/` at its path in the root filesystem, for example
   `overlay/lab-layer/usr/bin/my-tool`.
2. Rebuild the layer and restart:

   ```bash
   python3 lab.py build images
   python3 lab.py stop
   python3 lab.py run
   ```

Do not commit firmware files into `overlay/`: they are proprietary and stay out of the
repository. Files that come
from the firmware go into the layer at build time, as `scripts/build-images.sh` does for
the gadget scheme.

There is no such layer on the device: something that works only through the lab layer
still needs a way onto the device, such as an autorun package (1).

## 4. Another stock firmware version

1. Get the inMusic update image for that version (`MPCSAMPLE-<version>-update.img`).
2. Extract its partitions into `FW_DIR` (default `./firmware`, git-ignored):

   ```
   $FW_DIR/extracted/part1   boot partition (ext2 with kernel.fit)
   $FW_DIR/extracted/part2   rootfs (ext4 with the dm-verity hash tree)
   $FW_DIR/extracted/parr    recovery partition (ext2)
   ```

   The update image is an `AZ0x` container. This repository has no extractor for it.
3. Write the new SHA-256 values into `inputs.sha256`, so that `build` checks them:

   ```bash
   cd "$FW_DIR" && shasum -a 256 extracted/part1 extracted/part2 extracted/parr \
       Firmware/MPCSAMPLE-<version>-update.img
   ```

4. Rebuild the disk images from the new partitions, and start from a clean eMMC:

   ```bash
   python3 lab.py stop
   REBUILD_EMMC=1 python3 lab.py build images
   python3 lab.py build content     # if you have the factory library
   python3 lab.py reset
   python3 lab.py run
   ```

   The kernel command line, including the dm-verity table and root hash, is read from the
   new version's FIT, and the boot splash is taken from its initramfs.

Only 1.3.0.12 has been run in the lab. Other versions can differ in what the lab relies
on: the app's control-surface protocol, `ac50.service`, the gadget scheme and the autorun
runner. Packages that check the app's SHA-256 refuse another version on purpose.

A modified rootfs does not boot: dm-verity rejects any block whose hash does not match the
stock root hash. Use the lab layer (3) for lab changes instead.

## Power semantics in the lab

- **POWER** on the panel's rear panel works as on the device: the app asks "Do you want to
  shut down?", saves on Yes, and powers the system off. QEMU keeps running, and the next
  POWER press powers the MPC on again with the same SD card.
- `lab.py stop` does the same and then closes QEMU.
- `/run` and every `LD_PRELOAD` drop-in are gone after a power-off. `/data` (projects,
  settings) stays on `out/run/emmc.qcow2` until `lab.py reset`.
