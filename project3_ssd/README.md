# Project 3 — SSD Performance Profiling

FIO-based measurement of an SSD/NVMe device: QD=1 latency, IOPS and throughput
versus block size, read/write mix, queue depth/parallelism, tail latency, LBA
range, and two deliberately misleading setups.

> **Data-loss warning.** Benchmark only a dedicated scratch file on a
> disposable partition or a raw device reserved for tests. The runner refuses
> system paths and mounted raw devices, but you are responsible for the target.
> Never point `TARGET` at a filesystem containing valuable data.

## Quick start

```bash
# 1. Install fio if needed. 2. Choose a scratch target on a disposable range.
export TARGET=/mnt/scratch/ecse4320-fio.dat      # or /dev/nvmeXnY ONLY if disposable
export CONFIRM_DESTRUCTIVE=I_UNDERSTAND
export TEST_SIZE=8G
./scripts/run_experiments.sh quick               # 136 fio jobs, short runtimes
../.venv/bin/python scripts/plot_results.py

# dry run (prints commands, writes nothing):
DRY_RUN=1 TARGET=/tmp/scratch CONFIRM_DESTRUCTIVE=I_UNDERSTAND \
  ./scripts/run_experiments.sh quick
```

Full sweep: `./scripts/run_experiments.sh full` (more block sizes and queue
depths). Useful overrides: `RUNTIME`, `RAMP`, `DIRECT`, `PRECONDITION=1`,
`SETTLE`, `MIX_SETTLE`, `PERF_CORE`. `FIO_RAW_DIR`, `FIO_DATA_OUT`, `FIO_TAIL_OUT`, `FIO_DATA`, and
`FIO_GRAPH_DIR` override parser/plot paths for testing. The six reference job files in `fio/` document the fixed knobs;
the runner overrides `--filename`, `--size`, `--runtime`, `--bs`, and
`--iodepth` so every run is tagged and reproducible.

For a raw device, additionally set `ALLOW_RAW_DEVICE=yes` **only after**
confirming the device is unmounted and contains no data:
`findmnt -S /dev/nvmeXnY`, `lsblk`, and a separate backup are mandatory.

**Host used for the committed run.** fio 3.43 on T2/Linux, Intel Core
Ultra 9 185H; the device is a Crucial CT2000P3PSSD8 (2 TB NVMe) behind LUKS +
btrfs (`compress=zstd:3`). The target was a 4 GiB file in `tmp/fio_scratch_dir/`
created with `chattr +C` (no CoW), `direct=1`, 2 s runtime, 1 s ramp, 30 s
idle between phases, 3 s idle between mix jobs, and a full 4 GiB
preconditioning write. These numbers therefore describe the complete
block stack (dm-crypt + btrfs + SSD), not raw NAND; use a dedicated
unencrypted partition for raw-device measurements. The scratch target is
deleted after validation; `results/env/environment.txt` records the device,
target path, and queue parameters.

## Pre-experiment hypotheses

1. **QD=1 latency:** 4 KiB random reads should be tens of microseconds, 4 KiB
   random writes higher (write cache/GC); 128 KiB sequential reads/writes
   should have lower per-I/O latency and much higher bandwidth.
2. **Block size:** random IOPS should fall as block size grows while bandwidth
   rises; sequential bandwidth should plateau near the interface/device
   ceiling for large blocks. Random and sequential curves should cross because
   the device is optimized for different request shapes.
3. **Read/write mix:** mixed traffic should land between pure-read and
   pure-write throughput; 70/30 may be non-monotonic because writes trigger
   GC and RFO-like internal traffic.
4. **Queue depth/parallelism:** IOPS and bandwidth should rise with QD until a
   knee, then flatten while latency rises. Little's Law (`IOPS ≈ QD/latency`)
   should fit below the knee and diverge as queues/GC grow.
5. **Tail latency:** p99 and p99.9 should grow faster than the mean near the
   knee; mid-QD should show a modest tail, near-knee a long tail.
6. **LBA range and device state:** a small active range may benefit from
   SLC/controller caching; after preconditioning, steady-state writes should
   be slower and less variable than a fresh burst.
7. **Expected failure/pitfall 1:** buffered I/O (`direct=0`) can hit the page
   cache and report inflated IOPS that are not device performance. Compare it
   with `direct=1`.
8. **Expected failure/pitfall 2:** `iodepth=8,numjobs=1` and
   `iodepth=1,numjobs=8` have the same nominal effective queue depth (8) but
   different latency/throughput because per-job queues and submission paths
   differ. Reporting only one as “QD=8” is misleading.

## Experiment → plot map

| Required study                                            | `exp_type` in `fio_summary.csv`            | Figure                                                            |
|-----------------------------------------------------------|--------------------------------------------|-------------------------------------------------------------------|
| QD=1 baselines (4K random R/W, 128K seq R/W) with p95/p99 | `qd1_baseline`                             | `01_qd1_baselines.png`, `results/data/tail_latency.csv`           |
| Block-size sweep, random + sequential                     | `bs_sweep`                                 | `02_block_size_rand.png`, `02_block_size_seq.png`                 |
| Read/write mix (100/0 down to 0/100 in 10 % steps)        | `rw_mix`                                   | `03_read_write_mix.png`                                           |
| QD / parallelism sweep, knee, Little's Law                | `qd_sweep`, `parallel_sweep`               | `04_qd_sweep_*.png`, `05_little_law_qd.png`, `10_parallelism.png` |
| Tail latency at mid and near-knee QD                      | `qd_sweep`                                 | `06_tail_latency.png`                                             |
| LBA range / working set                                   | `lba_range`                                | `07_lba_range.png`                                                |
| Device state (burst vs steady)                            | `device_state`                             | `08_device_state.png`                                             |
| Buffered-I/O and effective-QD pitfalls                    | `pitfall_buffered`, `pitfall_effective_qd` | `09_pitfalls.png`                                                 |

## Methodology

- FIO 3.x, `ioengine=libaio`, `direct=1`, `time_based=1`,
  `ramp_time` discarded, `group_reporting=1`, `randrepeat=1`,
  `norandommap=1`, `invalidate=1`, percentile list
  `50:95:99:99.9`. Raw JSON is kept in `results/raw/`; the parser records
  IOPS, MiB/s, mean completion latency, and p50/p95/p99/p99.9 for read and
  write components.
- `scripts/capture_env.sh` records the SSD model, interface, filesystem/raw
  device, mount, scheduler, queue parameters, `lsblk`, `nvme list`, and
  `smartctl` output when available. Fill in the SSD model/interface and
  capacity/range used from that file.
- `PRECONDITION=1` performs a full sequential write before measuring so the
  device is closer to steady state. Without it, a fresh device may show
  SLC-cache burst behaviour; the two `device_state` runs are a quick check,
  not a substitute for a proper preconditioning protocol.
- Scheduler/queue parameters are recorded, but the benchmark does not change
  them. Do not compare results collected with different governors, pinning, or
  mount options.
- For raw devices, `TEST_SIZE` must be smaller than the device and the range
  must be disposable. For files, `fallocate` reserves the range; `direct=1`
  still bypasses the page cache.
- Latency percentiles are taken from `clat_ns.percentile` in the JSON
  (`clat` = completion latency, not submission latency). The parser converts
  ns to microseconds.

## Analysis notes

- **Knee:** the plotting script marks the first QD reaching 90% of peak IOPS.
  Report both the IOPS knee and the latency at that QD; the knee is device- and
  preconditioning-dependent.
- **Little's Law:** `IOPS ≈ QD / mean_latency` for a single job with per-job
  queue depth. The comparison line uses the measured mean latency at each QD;
  divergence above the knee is queueing/GC, not a units error.
- **Tail latency:** p99/p99.9 are more informative than the mean for latency
  SLOs. Report them at QD=1 (unloaded), QD=32 (mid), and the near-knee QD.
- **Effective queue depth:** for multiple jobs the nominal concurrency is not
  simply `iodepth`; it is approximately `iodepth × numjobs` in flight per
  device, but submission/completion batching and per-job files change the
  actual pressure. State exactly what was varied.
- **Device state:** report temperature/throttling if `smartctl` provides it,
  whether the test hit an SLC cache, and whether the device had been written
  recently. A single burst run is not a steady-state result.

## Results from the committed run

136 measured jobs plus one preconditioning write; 12 figures in
`results/graphs/`, raw JSON in `results/raw/`, processed tables in
`results/data/`. The committed matrix used `RUNTIME=2`, `RAMP=1`,
`SETTLE=30`, and `MIX_SETTLE=3`; eight late-phase points that initially showed
a transient GC dip (the 512 KiB block-size jobs and QD=32 40/50 mix jobs) were
re-measured after a 45 s idle.

| QD=1 baseline | IOPS | MiB/s | mean clat | p99 clat |
|---|---:|---:|---:|---:|
| 4 KiB random read | 14,014 | 54.7 | 67.8 us | 77.3 us |
| 4 KiB random write | 45,098 | 176.2 | 16.1 us | 24.7 us |
| 128 KiB sequential read | 4,367 | 545.9 | 215.1 us | 395.3 us |
| 128 KiB sequential write | 13,159 | 1,644.9 | 35.7 us | 85.5 us |

Key observations:

- **Queue depth.** Random-read IOPS rose from 13.9 k at QD=1 to 261 k at QD=32,
  peaked at 266 k at QD=48, and then stayed near 252-255 k through QD=256; mean
  latency rose from 68 us to 1,011 us. The read knee is therefore near QD=32.
  Random-write IOPS rose from 44.3 k at QD=1 to 148.7 k at QD=4 and then
  flattened at roughly 139-150 k through QD=256 while latency grew from 16 us
  to 1,755 us; the write knee is near QD=4. Little's Law is plotted in
  `05_little_law_qd.png`.
- **Block size.** At QD=32, random-read IOPS fell from 251 k at 4 KiB to 5.4 k
  at 512 KiB while bandwidth rose from 981 MiB/s to a 2,945 MiB/s peak at
  32 KiB and 2,711 MiB/s at 512 KiB (the 64-128 KiB mid-range is
  non-monotonic). Random-write bandwidth rose from 585 MiB/s (4 KiB) to
  2,501 MiB/s (512 KiB) as IOPS fell from 150 k to 5.0 k. Sequential QD=1
  bandwidth rose from 229 to 1,370 MiB/s for reads and 178 to 2,265 MiB/s for
  writes between 4 and 512 KiB; sequential was faster than random at every
  tested size.
- **Read/write mix.** At QD=32, total throughput mostly fell as the write share
  rose: 230 k (100 % read), 223 k (90/10), 209 k (80/20), 198 k (70/30), 180 k
  (60/40), 173 k (50/50), 168 k (40/60), 142 k (30/70), 153 k (20/80), 149 k
  (10/90), and 119 k (100 % write); the 20/80 and 10/90 points are within run
  noise of each other. At QD=1 the order reversed: write-only was fastest
  (40.3 k vs 13.3 k for read-only). This is a queue-depth-dependent result.
- **Parallelism.** At fixed per-job `iodepth=8`, random-read IOPS scaled from
  61.6 k (`numjobs=1`) to 147 k (`2`), 203 k (`4`), and 233 k (`8`).
  Random-write IOPS stayed near 138-150 k, but p99 latency jumped from 71 us to
  14.6 ms, showing saturation in the tail before the mean throughput moved.
- **LBA range.** A 1 GiB active range gave 267 k IOPS at QD=32 versus 257 k for
  4 GiB (about 4 %), while QD=1 was essentially unchanged (14.1 k vs 13.9 k).
  The effect is weak on this LUKS + btrfs stack and should not be
  over-interpreted as a raw-device cache effect.
- **Device state.** The two identical back-to-back 128 KiB random-write runs
  were indistinguishable (17.87 k vs 17.93 k IOPS, 2.24 GiB/s in both), so no
  state change was detectable in this short pair. This is not a substitute for
  a proper precondition/steady-state protocol.
- **Buffered-I/O pitfall.** `direct=0` and `direct=1` produced similar IOPS
  (13.94 k vs 13.50 k), but buffered mean completion latency was 0.34 us versus
  68.5 us for O_DIRECT. The buffered number is page-cache completion, not
  device latency, and would be misleading if reported as SSD latency.
- **Effective-QD pitfall.** `iodepth=8,numjobs=1` and `iodepth=1,numjobs=8`
  differed in this run (84.7 k vs 102.6 k IOPS; mean 91.4 vs 74.0 us; p99 214
  vs 134 us). The nominal product is not enough to predict behavior; report
  both knobs and the resulting latency.
- **Preconditioning and GC.** The first exploratory run (before
  preconditioning) reported 1.17 M IOPS and 0.17 us for the first 4 KiB
  random-read job, because the freshly `fallocate`d file still had unwritten
  extents/cache. After a full preconditioning write the same job reports about
  14 k IOPS and 68 us. The QLC device can also show short-lived GC collapses
  after heavy write phases; the runner idles between phases/mix jobs, and the
  affected late points above were re-measured after idle. Treat these short
  runs as burst/cache-influenced measurements, not a full steady-state
  endurance result.

## AI-use note (edit before submission)

The FIO harness, parser, and plotting scripts were drafted with an AI coding
assistant. The student must independently verify the target safety checks,
inspect the raw JSON, confirm the fixed knobs, and rerun on the actual device.
Replace this note with the tools used, what they generated, and what you
changed, rejected, or validated. In particular, validate the safety logic
before using a raw device.

## Layout

```
fio/                      reference job files (QD=1, mixes)
scripts/capture_env.sh    device/OS/fio/SMART metadata
scripts/run_experiments.sh safe driver (confirmation + target checks)
scripts/parse_fio.py      raw JSON -> fio_summary.csv / tail_latency.csv
scripts/plot_results.py   all required figures
results/raw/              raw fio JSON
results/data/             processed CSV tables
results/graphs/           figures
results/env/              environment.txt
```
