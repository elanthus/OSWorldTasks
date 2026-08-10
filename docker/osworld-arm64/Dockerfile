# syntax=docker/dockerfile:1

# qemux/qemu 6.18, linux/arm64 image manifest. The upstream image is MIT
# licensed and publishes its exact source revision in OCI labels.
FROM qemux/qemu@sha256:b51ff8a5d69c10e57d3515c7a40dbbd47c410152b1491f849d12ede7607b80be AS upstream

# Copying the pinned filesystem through scratch deliberately drops the base
# image's /storage VOLUME declaration. OSWorld containers are disposable;
# keeping storage in the writable container layer lets container removal clean
# it up instead of leaving an anonymous Docker volume after every reset.
FROM scratch
COPY --from=upstream / /

# OSWorld's pinned DockerProvider mounts its immutable guest base at
# /System.qcow2. QEMU-Docker normally discovers /boot.qcow2, so teach the
# sourced installer about OSWorld's fixed path. Snapshot mode keeps that base
# read-only and stores all guest writes in an ephemeral QEMU overlay.
RUN mkdir -p /storage \
    && sed -i '/^findFile "iso"/i detectType "/System.qcow2" \&\& return 0' /run/install.sh \
    && grep -Fx 'detectType "/System.qcow2" && return 0' /run/install.sh

ENV ARGUMENTS="-snapshot"
ENV CPU_CORES="1" RAM_SIZE="1G" DISK_SIZE="16G" BOOT="http://example.com/image.iso"

EXPOSE 22 5900 8006
ENTRYPOINT ["/usr/bin/tini", "-s", "/run/entry.sh"]

LABEL org.opencontainers.image.title="PixelGym OSWorld ARM64 QEMU host" \
      org.opencontainers.image.description="ARM64 QEMU-Docker compatibility host for the pinned OSWorld-V2 Docker provider" \
      org.opencontainers.image.source="https://github.com/qemus/qemu" \
      org.opencontainers.image.revision="c698406b5a447234379b3fdede03d7f6e4e3fa6c" \
      org.opencontainers.image.licenses="MIT" \
      io.pixelgym.base.digest="sha256:b51ff8a5d69c10e57d3515c7a40dbbd47c410152b1491f849d12ede7607b80be" \
      io.pixelgym.osworld.release="v2026.06.24"
