# Installer

NexusOS installs with archinstall, from the Arch snapshot of the live medium,
with a NexusOS profile and defaults on top. On the live desktop, open a
terminal and run:

```bash
sudo nexusos-install
```

| File | Role |
|---|---|
| `nexusos-install` | runs archinstall with `config.json` (in `/usr/local/bin` on the ISO) |
| `config.json` | defaults: Limine, `linux`, NetworkManager, zram, hostname `nexusos`, profile Desktop › NexusOS |
| `nexusos.py` | the NexusOS profile: `nexusos-desktop` and its keyring, greetd, the repository copy |

The menus still ask for the disk, users and passwords, locale, keyboard,
timezone and graphics driver. The recommended disk layout is archinstall's
default with Btrfs, its subvolumes and snapper. On the owner's laptop, pick
the NVIDIA open kernel module.

Limine goes to the removable path (`EFI/BOOT/BOOTX64.EFI`), so the firmware
boot menu shows the disk, not an entry named after Arch; the profile renames
the Limine entries to NexusOS.

## Package sources

The live system is built pointing at its own sources, and archinstall copies
them to the target:

- `/etc/pacman.d/mirrorlist`: the Arch Linux Archive snapshot the image was
  built from, not live mirrors;
- `/etc/pacman.conf`: `[nexusos]` above `[core]`, served from
  `/var/lib/nexusos/repo`, the signed repository the ISO carries and the
  profile copies onto the disk.

An installed system therefore stays on the tested snapshot: `pacman -Syu`
finds nothing newer. Moving to a newer snapshot moves the date and the
repository together, with `nexusos-update` (nexusos-base).

Arch packages come from the archive during the installation, which needs a
network connection; the NexusOS packages come from the ISO.

## Testing

```bash
ci/test-install.sh 2026/11/18          # UEFI then BIOS
ci/test-install.sh 2026/11/18 uefi     # one firmware
```

The test boots the newest ISO next to a blank 40 GB disk, runs
`nexusos-install --silent` with the shipped defaults plus a disk layout and
throwaway credentials, then boots the disk alone. It checks the boot menu, the
Btrfs root subvolume, the identity, the package sources (synced, nothing to
upgrade), greetd with tuigreet, then has greetd open the test user's session
and checks Hyprland and the Caelestia shell. Logs and screenshots land in
`out/iso/test/`.

The guest fetches packages through `out/cache/pacman/` served as a pacman
`CacheServer`, removed from the installed mirrorlist before the first boot.
A download from the archive that fails stops an unattended archinstall
(its menus offer to try again): the test then installs again on a blank
disk, three times at most.

## Known issues

- archinstall's own screens still say Arch Linux, and Limine sits in
  `arch-limine` when not on the removable path.
- No Limine entries for snapper snapshots yet (VISION: rollback from the
  bootloader).
- archinstall 4.5 drops the snapper setting of a configuration file: it reads
  subvolume names as strings and compares them with `Path("@")`. The menus
  are not affected; an unattended install (and the test) gets no snapper.
