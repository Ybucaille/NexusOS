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

Newer upstream versions enter through the `testing` channel only.

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
