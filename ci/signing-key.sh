#!/usr/bin/env bash
# Manage the repository signing key: an offline primary key that only
# certifies, and a signing subkey that CI uses. Machines trust the primary
# key, so a leaked subkey is replaced without touching their keyring.
#
# The key lives in its own GnuPG home, never in ~/.gnupg. Passphrases are read
# on the terminal; no pinentry window is ever opened.
#
# Usage:
#   ci/signing-key.sh init     create the key, write pkgs/nexusos-keyring files
#   ci/signing-key.sh ci-key   print the signing subkey, without passphrase,
#                              for: ci/signing-key.sh ci-key | gh secret set NEXUSOS_SIGNING_KEY
#   ci/signing-key.sh renew    extend the signing subkey by a year, refresh the keyring
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
keyring="$root/pkgs/nexusos-keyring"
uid='NexusOS Package Signing'
export GNUPGHOME=${NEXUSOS_GNUPGHOME:-$HOME/.config/nexusos/gnupg}

# A GnuPG home that asks for passphrases on the terminal only.
new_home() {
  mkdir -p -m 700 "$1"
  echo 'pinentry-mode loopback' > "$1/gpg.conf"
  printf 'pinentry-program /usr/bin/false\nallow-loopback-pinentry\n' > "$1/gpg-agent.conf"
}

read_passphrase() {
  local again
  read -rsp "$1: " pass </dev/tty; echo >/dev/tty
  [[ -z ${2:-} ]] && return
  read -rsp 'Again: ' again </dev/tty; echo >/dev/tty
  [[ $pass == "$again" ]] || { echo 'passphrases differ' >&2; exit 1; }
  [[ -n $pass ]] || { echo 'empty passphrase' >&2; exit 1; }
}

with_pass() { gpg --batch --passphrase-fd 3 "$@" 3<<<"$pass"; }

primary() { gpg --list-keys --with-colons "$uid" | awk -F: '/^fpr/{print $10; exit}'; }

# The newest valid signing subkey.
signing_subkey() {
  gpg --list-keys --with-colons "$uid" |
    awk -F: '/^sub/{s = ($12 ~ /s/ && $2 !~ /[rei]/)} /^fpr/ && s {k = $10; s = 0} END {print k}'
}

# Public key, trust and revocation files for pacman-key --populate nexusos.
write_keyring() {
  local fpr
  fpr=$(primary)
  gpg --armor --export "$fpr" > "$keyring/nexusos.gpg"
  echo "$fpr:4:" > "$keyring/nexusos-trusted"
  touch "$keyring/nexusos-revoked"
  sed -i "s/^pkgver=.*/pkgver=$(date -u +%Y%m%d)/" "$keyring/PKGBUILD"
  echo "keyring written to $keyring (commit it)"
}

case ${1:-} in
  init)
    [[ -z $(gpg --list-keys --with-colons "$uid" 2>/dev/null) ]] || { echo "a key already exists in $GNUPGHOME" >&2; exit 1; }
    new_home "$GNUPGHOME"
    read_passphrase 'Passphrase for the primary key' confirm
    with_pass --quick-gen-key "$uid" ed25519 cert never 2>/dev/null
    fpr=$(primary)
    with_pass --quick-add-key "$fpr" ed25519 sign 1y
    backup="$(dirname "$GNUPGHOME")/nexusos-signing-backup.asc"
    (umask 077; with_pass --armor --export-secret-keys "$fpr" > "$backup")
    write_keyring
    cat <<EOF
primary key $fpr created.
Copy these two files to offline storage (USB key), then delete the first:
  $backup
  $GNUPGHOME/openpgp-revocs.d/$fpr.rev
Then give CI the signing subkey:
  ci/signing-key.sh ci-key | gh secret set NEXUSOS_SIGNING_KEY
EOF
    ;;
  ci-key)
    sub=$(signing_subkey)
    [[ -n $sub ]] || { echo 'no valid signing subkey' >&2; exit 1; }
    read_passphrase 'Passphrase for the primary key'
    # gpg exports secret keys under their own passphrase: clear it in a
    # throwaway copy of the keyring, export from there, then wipe the copy.
    tmp=$(mktemp -d)
    trap 'GNUPGHOME=$tmp gpgconf --kill all 2>/dev/null; rm -rf "$tmp"' EXIT
    cp -a "$GNUPGHOME/." "$tmp"
    rm -f "$tmp"/S.*
    printf '%s' "$pass" > "$tmp/old"
    : > "$tmp/new"
    for grip in $(GNUPGHOME=$tmp gpg -K --with-colons --with-keygrip "$uid" | awk -F: '/^grp/{print $10}'); do
      reply=$(GNUPGHOME=$tmp gpg-connect-agent 'OPTION pinentry-mode=loopback' "/definqfile PASSPHRASE $tmp/old" \
        "/definqfile NEW_PASSPHRASE $tmp/new" "PASSWD $grip" /bye)
      [[ $reply != *ERR* ]] || { echo 'wrong passphrase' >&2; exit 1; }
    done
    GNUPGHOME=$tmp gpg --batch --passphrase '' --armor --export-secret-subkeys "$sub!"
    ;;
  renew)
    fpr=$(primary) sub=$(signing_subkey)
    [[ -n $sub ]] || { echo 'no valid signing subkey' >&2; exit 1; }
    read_passphrase 'Passphrase for the primary key'
    with_pass --quick-set-expire "$fpr" 1y "$sub"
    write_keyring
    echo 'CI still holds the old expiry: ci/signing-key.sh ci-key | gh secret set NEXUSOS_SIGNING_KEY'
    ;;
  *)
    sed -n '2,/^set -euo/{/^#/s/^# \{0,1\}//p}' "$0"
    exit 1
    ;;
esac
