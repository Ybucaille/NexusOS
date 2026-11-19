#!/usr/bin/env bash
# Promote a dated release to the stable channel, unchanged: its signatures are
# checked against the key in git (fetch-repo.sh), then publish-release.sh moves
# stable to it. Test it first: UPDATE=<date> ci/test-install.sh moves an
# installed system to it.
#
# Usage: ci/promote.sh [YYYY/MM/DD]   (defaults to the snapshot testing carries)
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
field() { sed -n "s/^$1=//p" "$2" | head -n1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

if [[ -n ${1:-} ]]; then
  snapshot=$1
else
  gh release download testing -p nexusos-release -D "$tmp/testing"
  snapshot=$(field snapshot "$tmp/testing/nexusos-release")
fi
[[ $snapshot =~ ^[0-9]{4}/[0-9]{2}/[0-9]{2}$ ]] || { echo "bad snapshot: $snapshot" >&2; exit 1; }
if gh release download stable -p nexusos-release -D "$tmp/stable" 2>/dev/null; then
  current=$(field snapshot "$tmp/stable/nexusos-release")
  [[ ! $snapshot < $current ]] || { echo "stable is on $current, newer than $snapshot" >&2; exit 1; }
fi

"$root/ci/fetch-repo.sh" "$snapshot"
date=${snapshot//\//-}
"$root/ci/publish-release.sh" stable "$root/out/repo/$date" "stable channel" \
  "NexusOS stable channel, the default of nexusos-update: the release repo-$date (Arch Linux Archive snapshot of $date), promoted after testing."
