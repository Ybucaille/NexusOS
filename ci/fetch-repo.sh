#!/usr/bin/env bash
# Download the signed repo-<date> release into out/repo/<date>/, in place of a
# local build, so the ISO is made from what machines will install.
#
# Every signature is checked against the key committed in pkgs/nexusos-keyring,
# not against the keyring package of the release itself. Only the packages the
# database lists are fetched: the release may still hold older builds.
#
# Usage: ci/fetch-repo.sh [YYYY/MM/DD]   (defaults to yesterday, UTC)
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
snapshot=${1:-$(date -u -d yesterday +%Y/%m/%d)}
date=${snapshot//\//-}
tag="repo-$date"
out="$root/out/repo/$date"
key="$root/pkgs/nexusos-keyring/nexusos.gpg"
[[ -f $key ]] || { echo "no signing key in $key" >&2; exit 1; }

tmp=$(mktemp -d "$root/out/repo/.fetch-XXXXXX")
trap 'rm -rf "$tmp"' EXIT

gpg --dearmor < "$key" > "$tmp/key.gpg"
verify() { gpgv --quiet --keyring "$tmp/key.gpg" "$1.sig" "$1" 2>/dev/null || { echo "bad signature: $(basename "$1")" >&2; exit 1; }; }

gh release download "$tag" -D "$tmp/repo" -p 'nexusos.db*' -p 'nexusos.files*'
verify "$tmp/repo/nexusos.db"
verify "$tmp/repo/nexusos.files"

mapfile -t files < <(tar -xOf "$tmp/repo/nexusos.db" --wildcards '*/desc' | awk '/^%FILENAME%$/ {getline; print}')
(( ${#files[@]} )) || { echo "empty database in $tag" >&2; exit 1; }
patterns=()
for f in "${files[@]}"; do patterns+=(-p "$f" -p "$f.sig"); done
gh release download "$tag" -D "$tmp/repo" "${patterns[@]}"
for f in "${files[@]}"; do verify "$tmp/repo/$f"; done

# repo-add's names, which pacman and build-repo.sh expect next to the links.
for db in nexusos.db nexusos.files; do
  for ext in '' .sig; do mv "$tmp/repo/$db$ext" "$tmp/repo/$db.tar.zst$ext"; ln -s "$db.tar.zst$ext" "$tmp/repo/$db$ext"; done
done

rm -rf "$out"
mv "$tmp/repo" "$out"
echo "${#files[@]} packages from $tag, signatures checked, in $out"
