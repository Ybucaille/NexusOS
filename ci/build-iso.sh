#!/usr/bin/env bash
# Build the NexusOS live ISO in a throwaway Arch container, from archiso's
# releng profile with iso/ laid over it and the nexusos repository of a
# snapshot (ci/build-repo.sh, or the repo-<date> release).
#
# pacstrap mounts /proc, /sys and /dev into the image root, so the container
# gets CAP_SYS_ADMIN; it is not fully privileged.
#
# Usage: ci/build-iso.sh [YYYY/MM/DD]   (defaults to yesterday, UTC)
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
snapshot=${1:-$(date -u -d yesterday +%Y/%m/%d)}
repo="$root/out/repo/${snapshot//\//-}"
[[ -f $repo/nexusos.db ]] || { echo "no repository at $repo, run ci/build-repo.sh first" >&2; exit 1; }

out="$root/out/iso"
# pacstrap reuses the container's package cache: keep it between builds.
cache="$root/out/cache/pacman"
mkdir -p "$out" "$cache"
# xorriso would append to an existing image of the same name.
rm -f "$out/nexusos-${snapshot//\//.}-x86_64.iso"

docker run --rm --cap-add SYS_ADMIN --security-opt seccomp=unconfined \
  -e SNAPSHOT="$snapshot" -e OWNER="$(id -u):$(id -g)" \
  -v "$root/iso:/iso:ro" -v "$root/ci/iso-in-container.sh:/build.sh:ro" -v "$repo:/repo:ro" -v "$out:/out" \
  -v "$cache:/var/cache/pacman/pkg" \
  archlinux:latest bash /build.sh

ls -lh "$out"/*.iso
