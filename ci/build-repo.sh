#!/usr/bin/env bash
# Build the NexusOS package repository in a throwaway Arch container.
#
# The host system is never modified: packages are built inside Docker against
# a dated Arch Linux Archive snapshot and land in out/repo/<snapshot>/.
#
# Usage: ci/build-repo.sh [YYYY/MM/DD] [package...]
#   snapshot defaults to yesterday (UTC); packages default to all, in build order.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
snapshot=${1:-$(date -u -d yesterday +%Y/%m/%d)}
shift || true

# Dependencies first: later packages install earlier ones from the local repo.
order=(ttf-rubik-vf libcava python-materialyoucolor qt6-m3shapes-git quickshell-git caelestia-cli caelestia-shell)
packages=("${@:-${order[@]}}")

out="$root/out/repo/${snapshot//\//-}"
mkdir -p "$out"

# Memory and CPU caps keep the desktop usable while Quickshell compiles.
docker run --rm \
  --memory "${NEXUSOS_BUILD_MEMORY:-6g}" --cpus "${NEXUSOS_BUILD_CPUS:-6}" \
  -e SNAPSHOT="$snapshot" -e PACKAGES="${packages[*]}" \
  -v "$root/pkgs:/pkgs:ro" -v "$root/ci/build-in-container.sh:/build.sh:ro" -v "$out:/repo" \
  archlinux:latest bash /build.sh

echo "repository: $out"
