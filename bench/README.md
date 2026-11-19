# Benchmarks

Every NexusOS tuning must beat a reference, measured with the same script on
the same hardware.

## Which reference

The owner's daily install keeps changing (packages, services, autostarts), so
it cannot stay the reference. Comparisons run between two installs built from
the **same dated package snapshot**:

- **vanilla:** plain Arch + Caelestia from our installer, every tuning off;
- **nexusos:** the same, with the tunings under test.

Only the tuning differs, so the gap is its effect. Each new snapshot gets a new
vanilla run; results are never compared across snapshots.

The runs of the daily install (`results/*-baseline.json`) record the
starting point: what NexusOS was built to improve on. They are history, not
the comparison target. Results describe the machine they ran on (hardware,
disks, running programs), so they stay on it: `results/` is not tracked.

## Clean-run protocol

Open applications distort memory, pressure and model timings, so a reference
run follows this protocol:

1. Reboot, plugged in, power profile unchanged.
2. Log in and open nothing except one terminal.
3. Wait two minutes for autostart and indexing to settle.
4. Run:

   ```bash
   bench/baseline.py --label baseline --llm qwen2.5:7b-instruct
   ```

The script reports `clean run: yes` only after two minutes of uptime and when
user applications use less than 100 MiB. A run that is not clean stays useful
for the desktop footprint, but is not a reference.

## What is measured

- **Boot:** firmware, loader, kernel and userspace stages, time to
  `graphical.target`, the critical chain that delayed it, the ten slowest
  units.
- **Memory:** total, available, swap and zram usage, swappiness, THP.
- **Pressure:** PSI for CPU, memory and I/O.
- **Processes**, in four groups: `system` (anonymous memory of
  `system.slice`), `desktop_core` (Hyprland, Caelestia shell, portals,
  PipeWire, session bus…), `autostart` (personal background services) and
  `apps`. The **desktop footprint** is `system + desktop_core`; the terminal
  that runs the script is excluded. User processes are counted by PSS, which
  splits shared pages fairly.
- **Graphics:** GPUs, which DRM nodes Hyprland and the shell open, monitors.
- **CPU and power:** governor, EPP, power profile, sched_ext state, load.
- **Storage:** root filesystem and options, I/O scheduler, fstrim.
- **Versions** of the kernel, Hyprland, Quickshell, Caelestia, Mesa, NVIDIA
  and Ollama.
- **Local model** (`--llm`): cold and warm time to first token and tokens/s
  through the Ollama API, VRAM used afterwards.

## Not measured yet

- Frame times during Caelestia animations at 144 Hz.
- Input latency.

Both need a method before the tuning phase.

Results go to `results/`, one JSON file per run.
