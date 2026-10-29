# Convenience wrapper for macOS/Linux. Everything lives in lab.py, which also runs on
# Windows (py lab.py ...). Firmware stays in $(FW_DIR) and is only mounted read-only.
FW_DIR ?= $(CURDIR)/firmware
LAB := FW_DIR=$(FW_DIR) python3 lab.py

.PHONY: build kernel bridge tools images run run-headless panel ssh stop reset status test

build:        ; $(LAB) build
kernel:       ; $(LAB) build kernel
bridge:       ; $(LAB) build bridge
tools:        ; $(LAB) build tools
images:       ; $(LAB) build bridge images
run:          ; $(LAB) run
run-headless: ; $(LAB) run --headless
panel:        ; $(LAB) panel
ssh:          ; $(LAB) ssh
stop:         ; $(LAB) stop
reset:        ; $(LAB) reset
status:       ; $(LAB) status
test:         ; python3 lab.py selftest && python3 panel/server.py --selftest
