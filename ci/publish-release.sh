#!/usr/bin/env bash
# Publish a signed repository directory as the assets of a GitHub release: a
# dated build (repo-YYYY-MM-DD) or an update channel (testing, stable).
#
# Machines update from a channel while it changes, so the upload order keeps
# every moment safe: packages first, then the databases, then the signed
# nexusos-release that names them. A client that meets a new release file and
# an old database, or a database whose package is not there yet, stops on the
# mismatch and tries again later. Assets the new database no longer lists
# leave last. A release never moves to an older snapshot, and assets GitHub
# already holds with the same checksum are not sent again. The nexusos-buildkeys
# manifest of a build, when there is one, goes with the databases: it is no
# part of what machines install, and the next build reads it to reuse packages.
#
# Usage: ci/publish-release.sh TAG DIR TITLE NOTES
#   DIR as build-repo.sh or fetch-repo.sh leave it; GH_TOKEN or gh's login.
set -euo pipefail

(( $# == 4 )) || { echo "usage: ci/publish-release.sh TAG DIR TITLE NOTES" >&2; exit 64; }
tag=$1 dir=$2 title=$3 notes=$4
field() { sed -n "s/^$1=//p" "$2" | head -n1; }
for f in nexusos-release nexusos.db nexusos.files; do
  [[ -f $dir/$f && -f $dir/$f.sig ]] || { echo "$dir: no signed $f" >&2; exit 1; }
done
snapshot=$(field snapshot "$dir/nexusos-release")
# Named once: gh finds the repository from the current directory otherwise,
# and the uploads run from a staging directory outside it.
export GH_REPO=${GH_REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

if gh release view "$tag" >/dev/null 2>&1; then
  if gh release download "$tag" -p nexusos-release -D "$tmp/current" 2>/dev/null; then
    current=$(field snapshot "$tmp/current/nexusos-release")
    if [[ $snapshot < $current ]]; then
      echo "$tag stays on snapshot $current, newer than $snapshot"
      exit 0
    fi
  fi
  gh release edit "$tag" --title "$title" --notes "$notes"
else
  # The repository page shows stable; testing is a prerelease. The tag goes
  # on the branch head, not on the built commit: when the workflow file
  # changed since, GitHub refuses the CI token a ref that would carry the
  # older workflow.
  case $tag in
    stable) flags=(--latest) ;;
    testing) flags=(--prerelease --latest=false) ;;
    *) flags=(--latest=false) ;;
  esac
  gh release create "$tag" "${flags[@]}" --title "$title" --notes "$notes"
fi

# Only what the database lists, under real file names: pacman fetches
# nexusos.db, and release assets cannot be symlinks.
mapfile -t packages < <(tar -xOf "$dir/nexusos.db" --wildcards '*/desc' | awk '/^%FILENAME%$/ {getline; print}')
(( ${#packages[@]} )) || { echo "empty database in $dir" >&2; exit 1; }
# GitHub renames assets with other characters (an epoch's ':'), and pacman
# would then ask for a file that is not there.
bad=$(printf '%s\n' "${packages[@]}" | grep -v '^[A-Za-z0-9._-]*$' || true)
[[ -z $bad ]] || { echo "asset names GitHub would rewrite: $bad" >&2; exit 1; }
mkdir "$tmp/stage"
for f in "${packages[@]}"; do cp -L "$dir/$f" "$dir/$f.sig" "$tmp/stage/"; done
cp -L "$dir"/nexusos.{db,files}{,.sig} "$dir"/nexusos-release{,.sig} "$tmp/stage/"
[[ ! -f $dir/nexusos-buildkeys ]] || cp -L "$dir/nexusos-buildkeys" "$tmp/stage/"
cd "$tmp/stage"

gh api "repos/$GH_REPO/releases/tags/$tag" --jq '.assets[] | "\(.digest // "") \(.name)"' > "$tmp/assets"
send() {
  local f todo=()
  for f; do
    grep -qxF "sha256:$(sha256sum < "$f" | cut -d' ' -f1) $f" "$tmp/assets" || todo+=("$f")
  done
  (( !${#todo[@]} )) || gh release upload "$tag" --clobber "${todo[@]}"
}
send *.pkg.tar.zst *.pkg.tar.zst.sig
databases=(nexusos.db nexusos.db.sig nexusos.files nexusos.files.sig)
[[ ! -f nexusos-buildkeys ]] || databases+=(nexusos-buildkeys)
send "${databases[@]}"
send nexusos-release.sig nexusos-release

awk '{print $2}' "$tmp/assets" | while read -r asset; do
  [[ -e $asset ]] || gh release delete-asset "$tag" "$asset" --yes
done
echo "$tag: snapshot $snapshot, ${#packages[@]} packages"
