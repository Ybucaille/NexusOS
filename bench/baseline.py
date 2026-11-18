#!/usr/bin/env python3
"""Measure the current machine as a NexusOS comparison point.

Writes one JSON file in bench/results/ and prints a short summary.
Standard library only; run as the desktop user, not root.

Usage:
    bench/baseline.py [--label NAME] [--llm MODEL]
"""

import argparse
import json
import os
import platform
import re
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"

# Processes that make up the desktop itself (compositor, shell, session plumbing).
DESKTOP_CORE = {
    "Hyprland", "qs", "quickshell", "caelestia", "hyprpaper", "hypridle", "hyprlock",
    "xdg-desktop-por", "xdg-document-po", "xdg-permission-",
    "pipewire", "pipewire-pulse", "wireplumber",
    "dbus-broker", "dbus-broker-lau", "dbus-daemon", "systemd", "(sd-pam)",
    "at-spi-bus-laun", "at-spi2-registr", "gvfsd", "gvfsd-metadata", "gvfs-udisks2-vo",
    "polkit-gnome-au", "uwsm", "greetd", "wayland-session", "Xwayland",
}

# Package versions recorded with every run.
PACKAGES = [
    "linux", "linux-cachyos", "hyprland", "quickshell-git", "caelestia-shell",
    "caelestia-cli", "mesa", "nvidia-open", "nvidia-utils", "ollama", "ollama-cuda",
]


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def boot():
    out = run(["systemd-analyze", "time"])
    stages = {k: float(v) for v, k in re.findall(r"([\d.]+)s \((\w+)\)", out)}
    total = re.search(r"= ([\d.]+)s", out)
    target = re.search(r"graphical\.target reached after ([\d.]+)s", out)
    blame = []
    for line in run(["systemd-analyze", "blame", "--no-pager"]).splitlines()[:10]:
        parts = line.split(None, 1)
        if len(parts) == 2:
            blame.append({"time": parts[0], "unit": parts[1]})
    return {
        "stages_s": stages,
        "total_s": float(total.group(1)) if total else None,
        "graphical_target_s": float(target.group(1)) if target else None,
        "blame_top10": blame,
    }


def meminfo():
    info = {}
    for line in (read("/proc/meminfo") or "").splitlines():
        key, value = line.split(":", 1)
        info[key] = int(value.split()[0])  # kB
    zram = []
    for line in run(["zramctl", "--bytes", "--noheadings", "--raw",
                     "--output", "NAME,DISKSIZE,DATA,COMPR,TOTAL,ALGORITHM"]).splitlines():
        name, size, data, compr, total, algo = line.split()
        zram.append({"name": name, "size": int(size), "data": int(data),
                     "compressed": int(compr), "total": int(total), "algorithm": algo})
    return {
        "total_mib": info.get("MemTotal", 0) // 1024,
        "available_mib": info.get("MemAvailable", 0) // 1024,
        "swap_used_mib": (info.get("SwapTotal", 0) - info.get("SwapFree", 0)) // 1024,
        "zram": zram,
        "swappiness": read("/proc/sys/vm/swappiness"),
        "thp": read("/sys/kernel/mm/transparent_hugepage/enabled"),
    }


def pressure():
    result = {}
    for kind in ("cpu", "memory", "io"):
        text = read(f"/proc/pressure/{kind}") or ""
        result[kind] = {line.split()[0]: dict(f.split("=") for f in line.split()[1:4])
                        for line in text.splitlines()}
    return result


def ancestors(pid):
    """PIDs of this process and its parents, excluded from the measurement."""
    chain = set()
    while pid > 1:
        chain.add(pid)
        stat = read(f"/proc/{pid}/stat")
        if not stat:
            break
        pid = int(stat.rsplit(")", 1)[1].split()[1])
    return chain


def classify(comm, cgroup):
    if "/user.slice/" not in cgroup:
        return "system"
    if comm in DESKTOP_CORE:
        return "desktop_core"
    if "@autostart.service" in cgroup or "/session.slice/" in cgroup or "/background.slice/" in cgroup:
        return "autostart"
    if re.search(r"/app\.slice/[^/]+\.service$", cgroup):
        return "autostart"
    return "apps"


def processes():
    """PSS per category; PSS splits shared pages fairly between processes."""
    skip = ancestors(os.getpid())
    totals = {"system": 0, "desktop_core": 0, "autostart": 0, "apps": 0}
    per_name = {key: {} for key in totals}
    unreadable = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) in skip:
            continue
        comm = read(entry / "comm")
        cgroup = (read(entry / "cgroup") or "").split("::", 1)[-1]
        rollup = read(entry / "smaps_rollup")
        if comm is None or "/user.slice/" not in cgroup:
            continue  # system services are measured through their cgroup below
        if rollup is None:
            unreadable += 1  # other users' processes (mostly root services)
            continue
        match = re.search(r"^Pss:\s+(\d+)", rollup, re.M)
        if not match:
            continue  # kernel threads
        pss = int(match.group(1))
        cat = classify(comm, cgroup)
        totals[cat] += pss
        per_name[cat][comm] = per_name[cat].get(comm, 0) + pss

    def top(cat, n=12):
        items = sorted(per_name[cat].items(), key=lambda kv: -kv[1])[:n]
        return [{"name": k, "pss_mib": round(v / 1024, 1)} for k, v in items]

    # Root services are unreadable per process; their cgroup's anonymous memory is not.
    stat = read("/sys/fs/cgroup/system.slice/memory.stat") or ""
    anon = re.search(r"^anon (\d+)", stat, re.M)
    totals["system"] = int(anon.group(1)) // 1024 if anon else 0

    return {
        "pss_mib": {k: round(v / 1024, 1) for k, v in totals.items()},
        "desktop_footprint_mib": round((totals["system"] + totals["desktop_core"]) / 1024, 1),
        "desktop_core_top": top("desktop_core"),
        "autostart_top": top("autostart"),
        "apps_top": top("apps"),
        "unreadable_processes": unreadable,
    }


def drm_nodes():
    """Map /dev/dri nodes to PCI devices, then see which ones the desktop opens."""
    by_path = Path("/dev/dri/by-path")
    nodes = {}
    if by_path.exists():
        for link in by_path.iterdir():
            nodes[os.path.basename(os.readlink(link))] = link.name.split("-")[1]
    gpus = {}
    for line in run(["lspci", "-D"]).splitlines():
        if re.search(r"VGA|3D|Display", line):
            gpus[line.split()[0]] = line.split(": ", 1)[1]
    used = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        comm = read(entry / "comm")
        if comm not in ("Hyprland", "qs", "quickshell"):
            continue
        opened = set()
        try:
            for fd in (entry / "fd").iterdir():
                target = os.readlink(fd)
                if target.startswith("/dev/dri/"):
                    opened.add(os.path.basename(target))
        except OSError:
            continue
        used[comm] = sorted(f"{n} -> {gpus.get(nodes.get(n), nodes.get(n))}" for n in opened)
    return {"gpus": gpus, "desktop_opens": used}


def monitors():
    try:
        return [{"name": m["name"], "mode": f'{m["width"]}x{m["height"]}@{m["refreshRate"]:.0f}'}
                for m in json.loads(run(["hyprctl", "-j", "monitors"]) or "[]")]
    except (ValueError, KeyError):
        return []


def cpu_power():
    return {
        "model": next((l.split(":", 1)[1].strip() for l in (read("/proc/cpuinfo") or "").splitlines()
                       if l.startswith("model name")), None),
        "threads": os.cpu_count(),
        "x86_64_v4": "avx512f" in (read("/proc/cpuinfo") or ""),
        "governor": read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "epp": read("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"),
        "power_profile": run(["powerprofilesctl", "get"]).strip() or None,
        "sched_ext": read("/sys/kernel/sched_ext/state"),
        "loadavg": read("/proc/loadavg"),
    }


def storage():
    root = run(["findmnt", "-no", "SOURCE,FSTYPE,OPTIONS", "/"]).split()
    schedulers = {}
    for dev in Path("/sys/block").iterdir():
        sched = read(dev / "queue/scheduler")
        if sched and not dev.name.startswith(("zram", "loop")):
            schedulers[dev.name] = sched
    return {
        "root": dict(zip(("source", "fstype", "options"), root)),
        "io_scheduler": schedulers,
        "fstrim_timer": run(["systemctl", "is-enabled", "fstrim.timer"]).strip(),
    }


def versions():
    found = {}
    for line in run(["pacman", "-Q", *PACKAGES]).splitlines():
        name, ver = line.split()
        found[name] = ver
    return found


def llm(model):
    """Cold and warm time-to-first-token through the local Ollama API."""
    url = "http://localhost:11434/api/generate"

    def generate(keep_alive):
        body = json.dumps({"model": model, "prompt": "Explain what a process scheduler does.",
                           "stream": True, "keep_alive": keep_alive,
                           "options": {"temperature": 0, "num_predict": 128, "seed": 1}}).encode()
        req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
        start = time.monotonic()
        first = None
        final = {}
        with urllib.request.urlopen(req, timeout=300) as resp:
            for line in resp:
                chunk = json.loads(line)
                if first is None and chunk.get("response"):
                    first = time.monotonic() - start
                if chunk.get("done"):
                    final = chunk
        tokens = final.get("eval_count", 0)
        duration = final.get("eval_duration", 0) / 1e9
        return {"ttft_s": round(first or 0, 3),
                "tokens_per_s": round(tokens / duration, 1) if duration else None}

    # Unload first so the cold run includes model loading.
    urllib.request.urlopen(urllib.request.Request(
        url, json.dumps({"model": model, "keep_alive": 0}).encode(),
        {"Content-Type": "application/json"}), timeout=60).read()
    cold = generate("5m")
    warm = generate(0)
    vram = run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"])
    return {"model": model, "cold": cold, "warm": warm, "vram_after_mib": vram.strip() or None}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--llm", metavar="MODEL", help="also measure an Ollama model")
    args = parser.parse_args()

    procs = processes()
    # A clean run has no user application open besides the launching terminal.
    clean = procs["pss_mib"]["apps"] < 100
    result = {
        "label": args.label,
        "date": datetime.now().astimezone().isoformat(timespec="seconds"),
        "clean": clean,
        "uptime_s": float((read("/proc/uptime") or "0").split()[0]),
        "kernel": platform.release(),
        "cmdline": read("/proc/cmdline"),
        "cpu_power": cpu_power(),
        "boot": boot(),
        "memory": meminfo(),
        "pressure": pressure(),
        "processes": procs,
        "graphics": {**drm_nodes(), "monitors": monitors()},
        "storage": storage(),
        "versions": versions(),
    }
    if args.llm:
        result["llm"] = llm(args.llm)

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f'{datetime.now():%Y%m%d-%H%M}-{args.label}.json'
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    b, p = result["boot"], procs["pss_mib"]
    print(f"{out}")
    print(f"clean run: {'yes' if clean else 'NO, apps use ' + str(p['apps']) + ' MiB'}")
    print(f"boot: {b['total_s']} s total, graphical.target after {b['graphical_target_s']} s")
    print(f"desktop footprint: {procs['desktop_footprint_mib']} MiB "
          f"(system {p['system']}, desktop core {p['desktop_core']}, "
          f"autostart {p['autostart']}, apps {p['apps']})")
    if "llm" in result:
        l = result["llm"]
        print(f"llm {l['model']}: cold TTFT {l['cold']['ttft_s']} s, "
              f"warm TTFT {l['warm']['ttft_s']} s, {l['warm']['tokens_per_s']} tok/s")


if __name__ == "__main__":
    main()
