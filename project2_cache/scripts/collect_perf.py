#!/usr/bin/env python3
"""Collect perf counters for selected memory workloads.

The workload is pinned with taskset. On hybrid CPUs perf expands generic
events to both PMUs (cpu_core/... and cpu_atom/...); only the PMU containing
the selected core is kept. Event names are normalised to their base names so
the plotting code is portable.

If perf is unavailable (or perf_event_paranoid forbids access), writes a note
and exits successfully so the rest of the pipeline is still reproducible.
"""
import csv
import os
import re
import shutil
import statistics
import subprocess

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(DIR, "membench")
OUT = os.path.join(DIR, "results/data/perf_counters.csv")
NOTE = os.path.join(DIR, "results/logs/perf_status.txt")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
os.makedirs(os.path.dirname(NOTE), exist_ok=True)

CORE = os.environ.get("PERF_CORE", "2")
REPS = int(os.environ.get("PERF_REPS", "3"))
EVENTS = ["cycles", "instructions", "cache-references", "cache-misses",
          "LLC-loads", "LLC-load-misses", "dTLB-loads", "dTLB-load-misses"]


def write_note(lines):
    with open(NOTE, "w") as f:
        for line in lines:
            f.write(str(line) + "\n")


def cpu_list_contains(path, cpu):
    try:
        with open(path) as f:
            text = f.read().strip()
    except OSError:
        return False
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            if int(lo) <= cpu <= int(hi):
                return True
        elif int(part) == cpu:
            return True
    return False


def selected_pmu(cpu):
    if cpu_list_contains("/sys/devices/cpu_core/cpus", cpu):
        return "cpu_core"
    if cpu_list_contains("/sys/devices/cpu_atom/cpus", cpu):
        return "cpu_atom"
    return None


def split_event(name):
    """Return (pmu, base event), stripping the /u or /k privilege suffix."""
    name = name.strip()
    pmu = None
    m = re.match(r"^(cpu_core|cpu_atom)/(.+?)(?:/[uk])?$", name)
    if m:
        pmu, name = m.group(1), m.group(2)
    else:
        name = re.sub(r"/[uk]$", "", name)
    return pmu, name


def core_is_usable(cpu):
    return subprocess.run(
        ["taskset", "-c", str(cpu), "true"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


if shutil.which("perf") is None:
    write_note(["perf is not installed; install linux-tools/perf and rerun."])
    print("perf not installed; wrote", NOTE)
    raise SystemExit(0)

try:
    CORE = int(CORE)
except ValueError:
    CORE = 0
if not core_is_usable(CORE):
    CORE = 0

PMU = selected_pmu(CORE)

CONFIGS = []
for size in (1 << 20, 1 << 23, 1 << 25, 2 ** 29):
    CONFIGS.append(("perf_latency", size, 64, "random", 1))
for size in (1 << 20, 1 << 25, 2 ** 29):
    CONFIGS.append(("perf_tlb", size, 4096, "random", 1))

rows = []
failed = []
for exp, size, stride, mode, chains in CONFIGS:
    cmd = ["perf", "stat", "-x,", "-e", ",".join(EVENTS), "-r", str(REPS),
           "taskset", "-c", str(CORE), BIN, "latency", "--size", str(size),
           "--stride", str(stride), "--mode", mode, "--chains", str(chains),
           "--trials", "1"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as e:
        failed.append(f"{exp} size={size}: {e}")
        continue
    if p.returncode != 0:
        failed.append(f"{exp} size={size}: perf exit {p.returncode}: {p.stderr.strip()[:300]}")
        continue

    # membench stdout -> latency, bandwidth, and work performed.
    ns = []
    gib = []
    accesses = []
    seconds = []
    for line in p.stdout.splitlines():
        f = line.split(",")
        if len(f) >= 16:
            try:
                seconds.append(float(f[8]))
                accesses.append(float(f[10]))
                ns.append(float(f[14]))
                gib.append(float(f[15]))
            except ValueError:
                pass

    def mean_or_nan(values):
        return statistics.mean(values) if values else float("nan")

    ns_mean = mean_or_nan(ns)
    gib_mean = mean_or_nan(gib)
    access_mean = mean_or_nan(accesses)
    time_mean = mean_or_nan(seconds)

    # perf stat stderr -> counters. With -r N, perf reports the mean across
    # repeats; a hybrid CPU also emits one line per PMU per event.
    vals = {}
    units = {}
    for line in p.stderr.splitlines():
        f = line.split(",")
        if len(f) < 3:
            continue
        event_pmu, event = split_event(f[2])
        if event not in EVENTS:
            continue
        if PMU and event_pmu and event_pmu != PMU:
            continue
        raw = f[0].strip()
        if raw.startswith("<") or not raw:
            continue
        try:
            value = float(raw.replace(" ", ""))
        except ValueError:
            continue
        vals.setdefault(event, []).append(value)
        units[event] = f[1].strip()

    # The expected case is one row per event. Averaging also handles perf
    # versions that emit one row per repeat instead of a pre-averaged value.
    for event, values in vals.items():
        rows.append({
            "exp_type": exp, "size_bytes": size, "stride_bytes": stride,
            "mode": mode, "chains": chains, "event": event,
            "value": statistics.mean(values), "unit": units.get(event, ""),
            "accesses": access_mean, "time_s": time_mean,
            "ns_per_access": ns_mean, "GiB_s": gib_mean,
        })
    if not vals:
        failed.append(f"{exp} size={size}: no counter rows parsed")

if rows:
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    write_note([f"collected {len(rows)} counter rows on cpu {CORE} "
                f"(pmu={PMU or 'generic'}, perf -r {REPS}); "
                f"failures: {failed}"])
    print(f"wrote {OUT} ({len(rows)} rows)")
else:
    write_note(["No perf rows collected."] + failed)
    print("no perf rows collected; see", NOTE)
