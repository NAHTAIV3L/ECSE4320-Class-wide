# Project 2 — Cache & Memory Performance Profiling

Portable single-thread memory microbenchmarks plus optional `perf` collection.
The harness measures zero-queue load latency, bandwidth under read/write mixes,
stride/granularity effects, memory-level parallelism, and TLB/huge-page
behaviour, then emits summary tables and plots.

## Quick start

```bash
make
scripts/run_experiments.sh quick        # ~2 min on the scaffold host
../.venv/bin/python scripts/plot_results.py
# optional, requires perf and permission to read counters:
../.venv/bin/python scripts/collect_perf.py
../.venv/bin/python scripts/plot_results.py
```

`collect_perf.py` uses `PERF_CORE` (default 2) and `PERF_REPS` (default 3).
On hybrid CPUs it keeps only the PMU that owns `PERF_CORE`, so the
`cpu_core`/`cpu_atom` split cannot mix counters from different cores.

`run_experiments.sh full` extends the footprint sweep to 1 GiB and adds more
repetitions. Both modes sample 1.5x intermediate footprints as well as powers of
two, and the stride/MLP/mix sweeps use denser intermediate settings. `PERF_CORE` (default 2) and `REPS` are environment overrides.
Always pin to one physical core; CPU 1/2 on this host are SMT siblings.

## Benchmark design

`membench latency` builds a single random cycle of pointers over
`size / stride` cache-line-spaced nodes and chases it. One chain gives a
zero-queue dependent-load latency. `--chains K` starts K pointers at different
positions on the same cycle: the footprint and random distribution stay fixed,
while K independent loads raise memory-level parallelism. Report `ns_per_step`
(per-chain step latency) and `GiB_s` (64 B lines per second); Little's Law
predicts `throughput = K / latency`.

`membench bandwidth` performs unrolled 64-bit loads/stores over a configurable
footprint and stride. Sequential mode touches every 8-byte word exactly once,
using `stride / 8` residue-class passes, so changing the stride changes memory
access locality rather than reducing the amount of payload touched. `--rw`
selects read, write, copy, 70/30, or 50/50; `--mode random` samples a uniform
random stream of `size / stride` stride-spaced nodes. `GiB_s` is payload
bandwidth; `GiB_s_traffic` is a first-order estimate that counts each cache
line touched by strided/random accesses. The latter is not a hardware counter
and is labelled accordingly in the plots.

Memory is allocated with `mmap`, every page is pre-faulted, and
`MADV_HUGEPAGE` / `MADV_NOHUGEPAGE` is applied for the huge-page study. The
host THP policy is recorded in `results/env/environment.txt`; `huge=1` only
has an effect when THP is enabled (`madvise` or `always`).

## Pre-experiment hypotheses

1. Dependent-load latency should step up at the L1 → L2 → L3 → DRAM
   boundaries (roughly 1–4 ns, 8–15 ns, 30–50 ns, 80–120 ns on a modern
   client CPU). Large random footprints should also show a TLB component
   because 4 KiB pages give only ~2–4 MiB per TLB coverage.
2. Bandwidth should be high and flat while the footprint fits in a cache,
   then collapse to the DRAM plateau. Copy/triad-like kernels should reach a
   higher payload bandwidth than read-only because reads and writes use
   different queue resources.
3. Read/write mixes should fall between the pure-read and pure-write
   throughputs; a 70/30 mix should not be the arithmetic mean if write
   allocate/RFO traffic is significant.
4. Increasing K independent chains should raise throughput roughly linearly
   until the memory controller saturates; after the knee, per-chain latency
   should rise while throughput flattens.
5. Strides at or above 64 B should cut payload bandwidth by roughly the
   stride/8 ratio until DRAM latency dominates; random access should be worse
   than sequential at the same stride because the prefetcher cannot help.
6. Huge pages should help the large-footprint random case by reducing TLB
   misses, with little effect when the footprint fits in cache.
7. **Expected failure/pitfall:** a “bandwidth” run on an 8 MiB footprint will
   report much higher GiB/s than a 1 GiB run even though the code is identical;
   it is measuring L3 reuse, not DRAM. A sequential pointer chase will also
   look far faster than a random chase because the hardware prefetcher hides
   DRAM latency. Both are benchmark pitfalls, not hardware speedups.

## Experiment → output map

| Required study | `exp_type` in raw/summary | Figure |
|---|---|---|
| Zero-queue per-level latency table | `latency_sweep` | `01_latency_vs_working_set.png`, `02_latency_by_level.png`, `results/data/latency_table.csv` |
| Pattern / granularity / stride | `stride_sweep`, `stride_random` | `04_stride_granularity.png`, `05_stride_random.png` |
| Read/write mix | `rw_mix` | `06_read_write_mix.png` |
| Intensity / throughput-latency knee | `mlp` | `07_intensity_little_law_*B.png`, `results/data/mlp_table.csv` |
| Working-set / cache transitions | `latency_sweep`, `bw_sweep` | `01_*`, `03_bandwidth_vs_working_set_*` |
| Cache/TLB miss rates vs runtime | `perf_latency`, `perf_tlb` | `08_perf_counters_*.png` (if perf available) |
| TLB / huge pages | `huge_pages` | `09_tlb_huge_pages.png` |
| Benchmark pitfall | `pitfall_cache_reuse`, `pitfall_prefetch` | `10_pitfalls.png` |

## Measurement discipline

- `CLOCK_MONOTONIC`, page pre-faulting, a warm-up pass for latency, 5–7 timed
  trials per configuration, 2–3 process repetitions. `summary.csv` reports
  mean, standard deviation, and minimum; the raw per-trial rows remain in
  `raw_measurements.csv` for re-analysis.
- `taskset` pins every run. The host governor is recorded (the scaffold host
  defaulted to `powersave`; use `performance` for the final numbers and state
  it in the report). Frequency scaling and migration are the main reasons a
  run would not reproduce.
- `capture_env.sh` records CPU model, cache geometry, NUMA, SMT, THP policy,
  compiler, kernel, `perf_event_paranoid`, and tool availability.
- The `perf` figures show miss rates (cache misses per cache reference, LLC
  load misses per LLC load, dTLB load misses per dTLB load), not raw counts.
  Raw counts scale with the number of accesses and would make larger
  footprints look worse even when the miss rate is unchanged.
- The summary uses the same `size_bytes` to label cache levels; the level is a
  prediction from the sysfs cache sizes, not a counter. The actual transition
  points in the plots should be used for the report.

## Analysis notes

- **AMAT-style reasoning:** for a capacity miss at level i, the extra cost is
  `miss_rate × (latency(level i+1) − latency(level i))`. The latency staircase
  in `01_latency_vs_working_set.png` is the input to that calculation.
- **Little's Law:** at the MLP knee, `throughput ≈ K / latency`. The shaded
  comparison in `07_intensity_little_law_*B.png` uses the measured K=1
  latency; divergence after the knee is the queueing/saturation signature.
- The 16 MiB point in the scaffold run sits far above the 8 MiB point. That is
  consistent with TLB pressure (16 MiB of 4 KiB pages needs 4096 entries) and
  possible L3 partitioning, not with an abrupt L3 capacity cliff alone; the
  huge-page experiment and `perf` dTLB counters are the checks.
- `mix70`/`mix50` execute exactly one operation per element (read or write),
  so their payload byte count is one 8-byte access per element; compare them
  to read/write at the same footprint rather than to copy, which moves two
  arrays.

## Negative / misleading cases

1. **Cache reuse mistaken for DRAM bandwidth:** `pitfall_cache_reuse` runs the
   same read loop at 8 MiB and 1 GiB. The small run is L3-resident and reports
   much higher GiB/s; reporting it as “memory bandwidth” would be wrong.
2. **Prefetcher mistaken for low latency:** `pitfall_prefetch` compares a
   sequential and random pointer chase at 512 MiB. The sequential chase can be
   several times faster because the prefetcher turns dependent loads into an
   overlapped stream, so it is not a zero-queue DRAM latency measurement.
3. **Unpinned / frequency-scaled runs:** not a separate `exp_type` because it
   is a process-level error, but it is the easiest way to make these numbers
   irreproducible. `capture_env.sh` records the governor; always pin with
   `taskset` and state whether turbo/frequency scaling was active.

## AI-use note (edit before submission)

The benchmark harness, scripts, and plotting code were drafted with an AI
coding assistant. The student must independently validate the pointer-chase
cycle, confirm that the reported cache levels match the host, rerun with a
fixed governor and pinning, and check the optional `perf` columns before
submitting. Replace this note with the exact tools used, what they generated,
and what you changed, rejected, or verified.

## Layout

```
src/membench.c                  latency + bandwidth benchmark
Makefile
scripts/capture_env.sh          environment + cache geometry
scripts/run_experiments.sh      full measurement matrix
scripts/summarize.py            raw trials -> summary/latency/MLP tables
scripts/collect_perf.py         optional perf counters (PMU-normalized)
scripts/plot_results.py         all required figures
results/data/                   raw, summary, latency table, MLP table, perf
results/graphs/                 figures
results/logs/                   perf status and any notes
results/env/                    environment.txt, cache_sizes.csv
```
