#!/usr/bin/env bash
# Runs inside the archlinux container started by build-iso.sh.
set -euo pipefail

trap 'chown -R "$OWNER" /out /var/cache/pacman/pkg' EXIT

echo "Server = https://archive.archlinux.org/repos/$SNAPSHOT/\$repo/os/\$arch" > /etc/pacman.d/mirrorlist
pacman -Syuu --noconfirm --needed archiso >/dev/null

# A signed repository is checked through its own keyring, as on a machine.
siglevel='Optional TrustAll'
if [[ -f /repo/nexusos.db.sig ]]; then
  cp /repo/nexusos-keyring-*.pkg.tar.zst /tmp/
  pacman -U --noconfirm /tmp/nexusos-keyring-*.pkg.tar.zst >/dev/null
  siglevel=Required
fi
echo "nexusos repository: SigLevel = $siglevel"

profile=/profile
cp -a /usr/share/archiso/configs/releng "$profile"
cp -a /iso/airootfs/. "$profile/airootfs/"
cat /iso/packages.x86_64 >> "$profile/packages.x86_64"
cat /iso/profiledef.sh >> "$profile/profiledef.sh"
printf '\n[nexusos]\nSigLevel = %s\nServer = file:///repo\n' "$siglevel" >> "$profile/pacman.conf"

# greetd owns tty1 instead of releng's root autologin.
rm -r "$profile/airootfs/etc/systemd/system/getty@tty1.service.d"

# A derivative must not present itself as Arch Linux: no Arch name or logo in
# the boot menus.
rm "$profile/syslinux/splash.png"
sed -i -e 's/Arch Linux install medium/NexusOS live medium/g' -e 's/MENU TITLE Arch Linux/MENU TITLE NexusOS/' \
  -e '/MENU BACKGROUND/d' "$profile"/efiboot/loader/entries/*.conf "$profile"/syslinux/*.cfg "$profile"/grub/*.cfg

# Dated like the packages, so the same snapshot gives the same version.
SOURCE_DATE_EPOCH=$(date -u -d "$SNAPSHOT" +%s) mkarchiso -w /work -o /out "$profile"
