# Contributing

Issues and pull requests are welcome, in English, Russian or Chinese. The
[roadmap](README.md#roadmap) lists where help counts most.

- **Never commit firmware**, factory content, files extracted from either, or device
  identifiers (serial numbers, keys). `firmware/`, `out/`, `sdcard/` and `share/` are
  git-ignored for that reason.
- **Open an issue first** for a large change, so we can agree on the approach.
- **Run the self-tests** before a pull request: `make test` (or
  `python3 lab.py selftest && python3 panel/server.py --selftest`).
- **Say what you measured and where**: host, accelerator (HVF / KVM / TCG), firmware version.
  Docs here state measured facts with a date; keep it that way.
- **Keep the stock system stock.** The rootfs is checked by dm-verity; lab changes go into the
  lab layer (`overlay/lab-layer/`), see [docs/CUSTOM_FIRMWARE.md](docs/CUSTOM_FIRMWARE.md).
- Kernel patches in `kernel/patches/` are GPL-2.0; everything else is MIT.

AI-assisted contributions are fine (this project was built with Claude). You are responsible
for what you submit: read it, run it, and say in the pull request what you tested.
