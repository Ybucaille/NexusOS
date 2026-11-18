# Benchmarks

Every NexusOS tuning must beat the baseline of the owner's current Arch +
Caelestia install, measured with the same script on the same hardware.

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
