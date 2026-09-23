#!/usr/bin/env bash
# Build the standalone Linux executable inside a Debian Bookworm container.
#
# PyInstaller binaries are only forward compatible with glibc: an executable
# linked against a newer glibc will not start on an older one. The GitHub
# runners ship a newer glibc than Raspberry Pi OS / Debian Bookworm (2.36),
# so building directly on the runner produces binaries that fail on a Pi with
# "GLIBC_2.3x not found". Building inside Bookworm pins the oldest glibc we
# support, and the result still runs on newer distributions.
#
# Usage: scripts/build-linux-binary.sh <executable-name>
# Runs on the host architecture, so the arm64 runner produces an arm64 binary.
set -euo pipefail

EXECUTABLE_NAME="${1:?usage: build-linux-binary.sh <executable-name>}"
BUILD_IMAGE="${BUILD_IMAGE:-debian:bookworm-slim}"

docker run --rm \
  -v "${PWD}:/src" \
  -w /src \
  -e EXECUTABLE_NAME="${EXECUTABLE_NAME}" \
  "${BUILD_IMAGE}" \
  bash -euo pipefail -c '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends \
      binutils ca-certificates python3 python3-venv \
      gcc libc6-dev python3-dev zlib1g-dev
    rm -rf /var/lib/apt/lists/*

    python3 -m venv /tmp/venv
    /tmp/venv/bin/pip install --upgrade pip
    /tmp/venv/bin/pip install . pyinstaller
    /tmp/venv/bin/pyinstaller --onefile --name "${EXECUTABLE_NAME}" \
      --distpath dist --workpath /tmp/pyinstaller-build --specpath /tmp \
      memory_mapper/__main__.py
  '

# The container writes as root (dist/, *.egg-info); hand the tree back to the
# runner user so later steps can package and clean up the workspace.
sudo chown -R "$(id -u):$(id -g)" .

"./dist/${EXECUTABLE_NAME}" --help > /dev/null
echo "Built dist/${EXECUTABLE_NAME}"
ldd --version | head -n 1 || true
objdump -T "./dist/${EXECUTABLE_NAME}" 2>/dev/null \
  | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -n 5 || true
