# NexusOS — vision

NexusOS is an installable Arch Linux derivative built around
[Nexus](https://github.com/Ybucaille/Nexus_private): a fast, fluid desktop that
ships Caelestia natively, runs local models well, and lets Nexus supervise the
machine once Nexus is ready for it.

This document records the decisions taken so far and the reasons behind them.
It is the reference until code replaces it.

## Two distinct projects

- **Nexus** is an autonomous agent that runs on any distribution. It owns the
  agent logic: orchestration, policy, permissions (auto mode), memory,
  executor, autonomous projects.
- **NexusOS** is the operating system built around Nexus, the way SteamOS is
  built around Steam. It contains no agent logic.

The dependency goes one way. Nexus never depends on NexusOS; NexusOS consumes
Nexus releases as a package. The only shared surface is a versioned
**capability interface** that Nexus detects at startup and ignores when absent.

NexusOS does not compensate for missing Nexus features. When a piece is
missing on the Nexus side, NexusOS waits for it.

## Nexus as supervisor

Nexus is meant to become the supervisor of NexusOS: updates, crash analysis,
RAM, disk and performance tuning. It must never sit on the critical path of
system health. Three tiers:

| Tier | Who | Latency | Role |
|---|---|---|---|
| Reflexes | kernel, systemd, systemd-oomd, watchdogs, sched_ext | ms | kill a memory hog, restart a crashed unit, keep the desktop responsive |
| Rules | deterministic NexusOS services | seconds | staged update in a snapshot with automatic rollback, Btrfs scrub, cache cleanup, SMART watch |
| Judgment | Nexus | minutes | read core dumps, choose update windows, tune knobs, anticipate problems |

Nexus supervises by adjusting the lower tiers, not by replacing them. NexusOS
exposes to it:

- **Observation:** PSI pressure, journal, core dumps, disk/Btrfs health,
  temperatures, benchmark results.
- **Actions:** a finite catalog, each with a risk level, an allowed range and a
  rollback (stage/apply/rollback an update, restart a unit, snapshot, resize
  zram within bounds, switch scheduler, adjust a cgroup slice weight). Nexus's
  own permission system decides what runs unattended.

Guarantees:

- Every tuning change is measured before and after by the benchmark suite and
  reverted if it is worse. Nexus optimizes against numbers.
- Nexus runs under a systemd watchdog; its own updates go through the same
  snapshot/rollback path; it cannot disable its own safeguards.
- Supervision is event-driven and cheap: deterministic triage or a small model
  first, the large model only when there is something to analyze.

Until Nexus has an executor, NexusOS builds the reflex and rule tiers (which
make the OS solid on their own) and specifies the interface.

## Principles

1. **Measured, not assumed.** The user's current Arch + Caelestia install is
   the baseline (`bench/`). Every tuning must beat it or is dropped.
2. **Fluid means never broken.** The biggest threat to fluidity on Arch is an
   update that breaks the desktop. NexusOS stays rolling, but one tested step
   behind.
3. **Distributable by design, private until decided.** No machine-specific
   hardcoding, no secret in the image. Publication happens only when the owner
   decides.

## Base

- Arch Linux packages, plus CachyOS repositories for `linux-cachyos` and
  x86-64-v3/v4 builds. Trade-off: a third-party dependency, accepted instead of
  rebuilding the whole distribution (a full rebuild is feasible once, but the
  continuous rebuild load is not, for a measured gain of roughly 0–10 %).
- x86-64-v3 baseline; v4 packages selected automatically when the CPU supports
  them.
- Hardware profiles chosen at install time (CPU, GPUs, hybrid graphics, RAM).
  The first profile is the owner's laptop: i7-11800H (AVX-512), 16 GB RAM,
  Intel UHD + RTX 3060 Mobile 6 GB, 1920×1080 @ 144 Hz.

## Packages built by NexusOS

Only where it pays off:

- `quickshell` pinned to a commit validated with `caelestia-shell`
  (the shell requires the git version), `caelestia-shell`, `caelestia-cli`;
- llama.cpp / Ollama for CUDA sm_86 and AVX-512;
- meta-packages `nexusos-base`, `nexusos-desktop`, `nexusos-tuning`;
- later, `nexus`, built from Nexus release tags.

## Desktop: Caelestia

Caelestia (Hyprland + Quickshell, GPL-3.0) ships natively. Updates come
through the NexusOS repository, not `caelestia update`, which runs a full
system upgrade through the AUR and would bypass tested packages. The Caelestia
CLI still applies the dotfiles; user changes stay in
`~/.config/caelestia/hypr-vars.lua` and `hypr-user.lua`.

Login: greetd + tuigreet (Caelestia's recommendation) or autologin.

## Updates

- Repositories point at a dated Arch Linux Archive snapshot, not live mirrors.
- CI boots the candidate, starts Hyprland and Caelestia, compares a
  screenshot, then promotes the snapshot to `stable` weekly; `testing` follows
  faster.
- Btrfs + snapper snapshot before every transaction, Limine boot entries on
  snapshots for rollback from the bootloader.
- Nexus database migrations run after the snapshot, which matches Nexus's
  explicit-backup requirement.

## System tuning (hypotheses to measure)

Btrfs with zstd, zram, unified kernel image with a minimal initramfs,
`scx_lavd`, desktop slices prioritized over background agents, systemd-oomd,
thermald, power-profiles-daemon.

Open question with large impact: **desktop on the Intel iGPU** (RTX left fully
free for models, better battery, but weak GPU for Caelestia's blur and
animations at 144 Hz) **versus everything on the RTX**. Decided by measurement.

## Local models

Local first; adding a remote provider is an opt-in upgrade handled by Nexus's
`ModelRegistry`. NVIDIA drivers and a CUDA inference runtime are preinstalled;
first boot suggests a model that fits the detected VRAM. Provider keys are
stored with `systemd-creds`, never in clear text.

## Installer and distribution

- Now: a scripted archinstall profile.
- At publication: a graphical installer (Calamares), a public package
  repository, GPL compliance for Caelestia changes and PKGBUILDs, a name check
  (the Arch Linux trademark forbids calling a derivative "Arch"; "NexusOS" must
  not collide with a known project).

## Repository layout (planned)

| Path | Content |
|---|---|
| `bench/` | baseline and comparison measurements |
| `pkgs/` | PKGBUILDs |
| `iso/` | archiso profile |
| `installer/` | archinstall profile |
| `ci/` | package build, ISO build, boot test |
| `docs/` | capability interface specification |

## Phases

1. Baseline measurement of the current machine.
2. Package repository with pinned Caelestia and Quickshell.
3. Installable ISO, tested on USB or a second disk (Hyprland in a VM is not
   reliable enough to judge fluidity).
4. Update channel: dated snapshots, weekly promotion, snapshot and rollback.
5. Tuning, each change justified by measurements.
6. Nexus packaged as a service, in its current version.

The capability interface follows when Nexus is ready.

## Open questions

- Frame-time measurement method under Caelestia animations.
- Whether the Caelestia CLI can apply dotfiles without its system upgrade.
- iGPU versus dGPU for the desktop.
- Package repository hosting and access while private.
