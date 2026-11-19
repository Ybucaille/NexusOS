# Packages

Packages NexusOS builds itself because they are not in the Arch repositories.
Each recipe starts from its AUR PKGBUILD, credited in its header, and is pinned
to the version validated on the owner's laptop.

## Pinned set

The desktop set below was validated together on 2026-10-20 with Qt 6.12:

| Package | Version | Change from AUR |
|---|---|---|
| `quickshell-git` | `2d3b3e9` | upstream backport `5d5d498` (Qt 6.12 build fix); commit pinned |
| `caelestia-shell` | 2.5.0 | two patches: Nmcli process leak, notification null guards |
| `caelestia-cli` | 1.1.3 | none |
| `qt6-m3shapes-git` | `32ad9ce` | commit pinned |
| `python-materialyoucolor` | 3.0.2 | pinned below AUR (3.0.4); `x86_64` instead of `any` |
| `libcava` | 1.0.0 | none |
| `ttf-rubik-vf` | 2.3.0 | none |

The dots and the packages their components need, validated at dots revision
`4a635c6`:

| Package | Version | Change from AUR |
|---|---|---|
| `caelestia-dots` | `4a635c6` | new: the dots as a pinned bare git repository; upstream `d4c24f8` picked (Hyprland 0.57) |
| `caelestia-firefox-theme` | `4a635c6` | built from the same pinned tree |
| `qtengine` | 0.2.1 | Qt6 only (no qt5-base, no KF5); pinned below AUR (0.2.4) |
| `darkly` | 0.5.39 | built from source instead of `darkly-bin`, provides it |
| `papirus-folders` | 1.14.0 | release tarball instead of the signed git tag |
| `pwvucontrol` | 0.5.3 | none |
| `nexusos-base` | 0.3.0 | NexusOS: identity (os-release, logo), `nexusos-update` |
| `nexusos-desktop` | 0.1.0 | NexusOS: session, dots deployment, desktop dependencies |
| `nexusos-keyring` | key date | NexusOS: repository signing key (see Signing) |

Newer upstream versions enter through the `testing` channel only.

## Dots deployment

`caelestia install` and `caelestia update` install packages through an AUR
helper and upgrade the whole system. NexusOS keeps their engine and drops
those steps: `/usr/lib/nexusos/dots` (shipped by `nexusos-desktop`) calls
caelestia-cli's own deployer, changeset and state code against the pinned
repository in `/usr/share/caelestia-dots`.

- First login: `nexusos-session` (sh) deploys the dots before Hyprland starts,
  skipping hooks that need a compositor.
- New pinned revision: `nexusos-dots.path` sees `/usr/share/caelestia-dots/REV`
  change and starts `nexusos-dots.service` inside the session, like
  `caelestia update`.
- Every other login costs one `grep` in sh; Python never starts.

Login goes through greetd with tuigreet on the TTY, instead of a second
compositor for the greeter.

`ci/test-dots.sh` checks first install, fast path and update in a container.

## Identity

`filesystem` owns `/usr/lib/os-release`. `nexusos-base` ships NexusOS's in
`/usr/share/nexusos/os-release` and a pacman hook copies it over Arch's after
every `filesystem` install or upgrade, as Arch derivatives do rather than
forking `filesystem` (`ID=nexusos`, `ID_LIKE=arch`). The shell reads the logo
through `LOGO=nexusos-logo`, `/usr/share/pixmaps/nexusos-logo.svg`, a
monochrome placeholder it tints with the theme. Removing `nexusos-base` keeps
the NexusOS file until the next `filesystem` upgrade.

## Updates

An installed system follows one release: the nexusos repository in
`/var/lib/nexusos/repo` and, in `/etc/pacman.d/mirrorlist`, the Arch Linux
Archive snapshot it was built and tested against. `nexusos-update` (shipped by
`nexusos-base`) moves both together:

1. downloads `nexusos-release` from the source in `/etc/nexusos/update.conf`
   and checks its signature against the NexusOS key alone, not every key
   pacman trusts;
2. takes no snapshot older than the installed one (nothing to update), and
   stops if nothing changed;
3. downloads the database, checks it against the release file, then every
   package it lists that the current repository lacks (checksum and
   signature), staging the new repository next to the current one;
4. takes a snapper pre snapshot when snapper manages `/`;
5. switches the repository and the mirrorlist and runs `pacman -Syu`; if
   pacman fails, both go back as they were;
6. takes the post snapshot and requires signatures for `[nexusos]` from then
   on.

An update interrupted after the switch is resumed by the next run
(`/var/lib/nexusos/update-pending`). `nexusos-update --check` only says whether
a newer release exists.

The CI writes `nexusos-release` next to the database (`snapshot=` and the
database's `sha256`) and signs it; `ci/fetch-repo.sh` checks it too.

## Channels

The source in `/etc/nexusos/update.conf` is a channel, a GitHub release that
always holds one complete repository:

- `stable` (the default, the release the repository page shows): a dated
  release promoted after testing, by `ci/promote.sh [YYYY/MM/DD]` (defaults to
  what testing carries). Test the candidate first:
  `UPDATE=<date> ci/test-install.sh` moves an installed system to it.
- `testing`: every build that passes CI, as soon as it is published.

`ci/publish-release.sh` fills both, and the dated releases. Packages go up
first, then the databases, then `nexusos-release`: a machine that updates
during the upload meets a mismatch (release file, database, package) and
stops before changing anything. Assets the new database no longer lists leave
last, assets GitHub already holds with the same `sha256` are not sent again,
and a channel never moves back to an older snapshot. Moving a machine from
testing back to stable leaves it where it is until stable catches up.

Promotion is manual for now: the weekly promotion VISION.md describes waits
for a boot test in CI.

## Qt coupling

Quickshell uses private Qt APIs, and `qt6-m3shapes-git` and the
`caelestia-shell` plugin link against Qt. A `qt6-base` update can break the
shell even when these packages do not change: they must be rebuilt and tested
against every new Qt before that Qt reaches `stable`.

## Building

```bash
ci/build-repo.sh              # all packages, yesterday's Arch snapshot
ci/build-repo.sh 2026/11/17   # a given snapshot
ci/build-repo.sh 2026/11/17 libcava
```

The build runs in a throwaway Docker container against a dated Arch Linux
Archive snapshot, so the host is never modified. Results land in
`out/repo/<snapshot>/` (not tracked) as a pacman repository named `nexusos`.
`NEXUSOS_BUILD_MEMORY` and `NEXUSOS_BUILD_CPUS` cap the container (default
6 GiB, 6 CPUs).

GitHub Actions (`.github/workflows/build-repo.yml`) runs the same two scripts
on every push touching `pkgs/` or the repository build scripts, weekly, and
on demand for a given date, then publishes the repository as the release
`repo-<YYYY-MM-DD>` and in the `testing` channel: one asset per package plus
`nexusos.db`, `nexusos.files` and `nexusos-release`, each with its signature.
A run for an existing date replaces its assets. Without a given date, a run
builds yesterday's snapshot, or the one testing is on when that is newer, so
a push always reaches testing.

## Signing

Packages and the database are signed with an ed25519 subkey of the
`NexusOS Package Signing` key. Machines trust the primary key through
`nexusos-keyring`, so a subkey can be replaced without touching them.

- The primary key only certifies and stays with the owner, in
  `~/.config/nexusos/gnupg`, with an offline backup and its revocation
  certificate.
- The signing subkey expires after a year. CI holds a copy without passphrase
  in the `NEXUSOS_SIGNING_KEY` secret; the build imports it as root, so
  upstream build code, which runs as `builder`, cannot read it.

```bash
ci/signing-key.sh init                                         # once
ci/signing-key.sh ci-secret                                    # after init and renew
ci/signing-key.sh renew                                        # yearly, then commit the keyring
NEXUSOS_SIGNING_KEY=$(ci/signing-key.sh ci-key) ci/build-repo.sh   # signed local build
```

Without the key the build is unsigned. `ci/test-dots.sh` requires signatures,
checked through the keyring package, whenever the database is signed.
