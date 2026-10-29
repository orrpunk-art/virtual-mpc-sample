# Build container for the MPC Sample virtual lab. Runs natively on arm64 (Apple Silicon) and
# amd64 (Windows / Linux PCs): everything for the VM is built with aarch64-linux-gnu-gcc,
# which is the native compiler on arm64 and Debian's cross compiler on amd64.
FROM debian:bookworm

RUN dpkg --add-architecture arm64 && apt-get update && \
    CROSS=$([ "$(dpkg --print-architecture)" = arm64 ] || echo gcc-aarch64-linux-gnu) && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential bc bison flex libssl-dev libelf-dev xz-utils zstd curl ca-certificates \
        gdisk e2fsprogs cpio file python3 rsync openssh-client kmod \
        device-tree-compiler u-boot-tools qemu-utils \
        libasound2-dev:arm64 libc6-dev:arm64 $CROSS && \
    cd /tmp && apt-get download busybox-static:arm64 && dpkg -x busybox-static_*_arm64.deb /opt/arm64 && rm -f *.deb && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /lab
