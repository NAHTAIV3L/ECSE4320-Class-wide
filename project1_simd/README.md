# Project 1 — SIMD Advantage Profiling

Single-threaded measurement of scalar vs. auto-vectorized vs. `-march=native`
builds for SAXPY, dot product, elementwise multiply, and a 1-D three-point
stencil. It also compares unit stride, non-unit stride, and a random-permutation
gather access pattern.

## Quick start

```bash
./configure                 # writes build.ninja (24 binaries, needs ninja)
ninja -j$(nproc)
scripts/run_experiments.sh quick    # ~ minutes; writes results/data/*.csv
scripts/verify_vectorization.sh     # compiler reports + disassembly evidence
../.venv/bin/python scripts/plot_results.py
```

`run_experiments.sh full` runs the larger sweep (N up to 2^28, 2 process
repetitions). Both modes sample powers of two plus 1.5x intermediate working-set
sizes, so the locality curves have points between the power-of-two steps. The
stride sweep uses positive element strides; gather is selected by passing
`STRIDE=0` to a kernel binary. `PERF_CORE`, `WARMUP`, `RUNS`, `REPS`,
`STRIDE_SIZES`, and `GATHER_SIZES` are environment overrides. Pin to a physical P-core (`PERF_CORE=2` by default on this host);
CPU 1 and 2 are SMT siblings, so do not compare runs pinned to both.

## Build variants


| label     | flags                                                                     | purpose                                                                      |
|-----------|---------------------------------------------------------------------------|------------------------------------------------------------------------------|
| `scalar`  | `-march=x86-64 -O2 -fno-tree-vectorize -fno-tree-slp-vectorize`           | scalar baseline with optimisation enabled                                    |
| `default` | `-march=x86-64 -O3 -ftree-vectorize`                                      | baseline x86-64 (SSE2, 128-bit) auto-vectorisation                           |
| `widest`  | `-O3 -march=native -mprefer-vector-width=256 -ffast-math -fno-math-errno` | widest ISA (AVX2, 256-bit) plus FP reassociation so reductions can vectorise |

The T2 GCC defaults to `-march=x86-64-v3`; the baseline build is pinned to
`-march=x86-64` deliberately so that `default` (SSE2) and `widest` (AVX2)
differ in vector width. `-ffast-math` is required for GCC to reassociate the dot-product reduction;
it is part of the `widest` treatment and is stated explicitly rather than
hidden. Elementwise kernels are validated against a `long double` reference
(with a tolerance appropriate to the stored type), so the flag change is
visible in the correctness column rather than assumed away.

The host used for the scaffold is an Intel Core Ultra 9 185H (Meteor Lake):
AVX2/AVX-VNNI, **no AVX-512**, 10 cores / 16 threads, 48 KiB L1d, 2 MiB L2
per core, 24 MiB shared L3. Re-run `scripts/capture_env.sh` on the measurement
host and report its output with the results.

## Experiment → plot map

| Required study                                          | Script section              | Figure                                                                |
|---------------------------------------------------------|-----------------------------|-----------------------------------------------------------------------|
| Scalar vs auto-vectorized vs widest across working sets | working-set sweep           | `01_throughput_vs_working_set_*`, `02_speedup_vs_working_set_*`       |
| Locality sweep with cache annotations                   | working-set sweep           | `03_locality_sweep_*.png`                                             |
| Alignment and tail handling                             | alignment + tail sweeps     | `04_alignment_*`, `05_tail_handling_*`                                |
| Stride/gather                                           | stride + gather sweeps      | `06_stride_*`, `07_stride_efficiency_*`, `11_gather_*`, `12_gather_*` |
| Data-type comparison                                    | full sweep                  | `08_dtype_comparison_*`                                               |
| Vectorization verification                              | `verify_vectorization.sh`   | `results/logs/vectorization_summary.txt`, `results/logs/vec_*`        |
| Roofline                                                | working-set data            | `09_roofline_{4,8}B.png`                                              |
| Negative example                                        | large-N elementwise kernels | `10_negative_examples.png`                                            |
| Summary table                                           | working-set data            | `results/data/summary_speedup.csv`                                    |

## Measurement discipline

- `CLOCK_MONOTONIC`, one process per configuration, warm-up iterations
  discarded, 20–30 timed repetitions per process (plus optional process
  repetitions). `measurements.csv` stores mean/median/min/max/std; raw trials
  are in `raw_trials.csv`.
- `taskset` pins to one logical CPU. `capture_env.sh` records governor, SMT
  topology, compiler version, flags, cache geometry, and THP state. The
  default host governor is `powersave`; results are only comparable at the
  same governor/frequency policy, and a `performance` governor is preferable.
- Data are generated deterministically with SplitMix64, so all builds and both
  types see the same value stream. Arrays are 64 B aligned by default; the
  alignment sweep shifts every array by an integral number of elements and
  reports the resulting byte offset.
- SAXPY is in-place, so `y` is restored from `y0` before every timed run
  (outside the timer). This avoids overflow across repetitions; the restore
  copy can also warm the array, which is noted as a limitation for small N.
- Every stride pass touches all `n` elements exactly once (`touched = n`); the
  kernel iterates residue classes so the inner loop still has the requested
  element stride. Effective bandwidth counts those useful elements
  (`touched x bytes/element`), so it under-counts the extra cache-line traffic
  that non-unit strides cause. This is intentional for the SIMD-efficiency
  comparison but is not a DRAM-counter measurement.
- The gather mode uses a deterministic Fisher–Yates permutation of `0..n-1`;
  every source and destination index is visited exactly once. The index-array
  traffic is not included in the effective-bandwidth numerator, so gather
  bandwidth is still a useful-data rate, not a DRAM-counter rate.

## Roofline

For each data type the roofline uses:

- **Measured bandwidth:** maximum effective GiB/s among `widest`, large-N
  (>= L3) working-set points.
- **Estimated single-core peak:** `max_freq × lanes × 2 FMA × 2 FLOP/FMA`.
  For the 185H at 5.1 GHz: ~163 GFLOP/s (`float`, 8 lanes) and ~82 GFLOP/s
  (`double`, 4 lanes). The plotting script defaults to 160/80 and accepts
  `PEAK_GFLOPS_F32` / `PEAK_GFLOPS_F64` overrides.
- Arithmetic intensity per element is fixed by the kernel:
  SAXPY 0.083 (f64) / 0.167 (f32), dot 0.125/0.25, multiply 0.042/0.083,
  stencil 0.3125/0.625 FLOP/byte.

## Negative / misleading cases

1. **Large-N elementwise kernels** do vectorise (see disassembly) but show
   almost no speedup because DRAM bandwidth is the limit. Reporting only the
   small-N result would overstate SIMD’s benefit.

## AI-use note (edit before submission)

This scaffold, benchmark driver, and plotting scripts were drafted with an AI
coding assistant. The measurements must be produced and checked independently:
verify the compiler reports, rerun with the stated governor/pinning, inspect
the negative case, and replace this note with the exact tools used, what they
generated, and what you changed, rejected, or validated. Do not submit the
hypotheses or plots without checking that the raw CSV supports them.

## Layout

```
main.c                  benchmark (one kernel per compile)
configure               ninja generator
build.ninja             generated
scripts/capture_env.sh  hardware/OS/compiler metadata + cache geometry
scripts/run_experiments.sh
scripts/verify_vectorization.sh
scripts/plot_results.py
results/data/           processed CSV, raw trials, summary table
results/graphs/         all figures
results/logs/           compiler reports, disassembly, concise summary
results/env/            environment and cache-size files
```
