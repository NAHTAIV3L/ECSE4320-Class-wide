#!/bin/bash
# Project 2 driver.  Usage: ./run_experiments.sh [quick|full]
#
# Raw output: results/data/raw_measurements.csv (one row per trial)
# Summaries:  results/data/summary.csv, latency_table.csv
# Env:        results/env/*
#
# Environment overrides: PERF_CORE, REPS
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-quick}"
BIN="$DIR/membench"
RAW="$DIR/results/data/raw_measurements.csv"
mkdir -p "$DIR/results/data" "$DIR/results/graphs" "$DIR/results/logs" "$DIR/results/env"

if [ ! -x "$BIN" ]; then (cd "$DIR" && make); fi
"$DIR/scripts/capture_env.sh" >/dev/null || true

PERF_CORE=${PERF_CORE:-2}
taskset -c "$PERF_CORE" true 2>/dev/null || PERF_CORE=0

# Powers of two plus 1.5x intermediates for denser cache-transition curves.
gen_sizes() {
  local maxp=$1 p out=""
  for p in $(seq 15 "$maxp"); do
    out="$out $((1 << p))"
    if [ "$p" -lt "$maxp" ]; then out="$out $((3 << (p - 1)))"; fi
  done
  echo "$out"
}

if [ "$MODE" = "full" ]; then
  SIZES="${SIZES:-$(gen_sizes 30)}"
  REPS=${REPS:-3}
  STRIDE_SIZES="${STRIDE_SIZES:-4194304 16777216 67108864 268435456 536870912}"
  STRIDE_LIST="${STRIDE_LIST:-8 16 24 32 40 48 64 96 128 192 256 384 512 768 1024 1536 2048 3072 4096}"
  MLP_SIZES="${MLP_SIZES:-67108864 268435456 536870912}"
else
  SIZES="${SIZES:-$(gen_sizes 28)}"
  REPS=${REPS:-2}
  STRIDE_SIZES="${STRIDE_SIZES:-4194304 16777216 67108864 268435456}"
  STRIDE_LIST="${STRIDE_LIST:-8 16 24 32 40 48 64 96 128 192 256 384 512 768 1024 1536 2048 4096}"
  MLP_SIZES="${MLP_SIZES:-67108864 268435456}"
fi

SEQ_SIZES="${SEQ_SIZES:-8388608 16777216 33554432 67108864 134217728 268435456 536870912}"
MIX_SIZES="${MIX_SIZES:-2097152 8388608 33554432 134217728 536870912}"
HUGE_SIZES="${HUGE_SIZES:-67108864 134217728 268435456 536870912}"
CHAIN_LIST="${CHAIN_LIST:-1 2 3 4 6 8 16 24 32 40 48 64}"

echo "test,size_bytes,stride_bytes,mode,rw,chains,huge,trial,time_s,steps,accesses,bytes_moved,bytes_traffic_est,ns_per_step,ns_per_access,GiB_s,GiB_s_traffic,exp_type" > "$RAW"

run() { # exp_type args...
  local exp=$1; shift
  taskset -c "$PERF_CORE" "$BIN" "$@" 2>/dev/null | sed "s/$/,${exp}/" >> "$RAW"
}

echo "mode=$MODE core=$PERF_CORE reps=$REPS governor=$(cat /sys/devices/system/cpu/cpu${PERF_CORE}/cpufreq/scaling_governor 2>/dev/null || echo unknown)"

echo "[1/7] zero-queue latency vs working set (random pointer chase)"
for rep in $(seq 1 "$REPS"); do
  for sz in $SIZES; do
    run latency_sweep latency --size "$sz" --stride 64 --mode random --chains 1 --trials 7
  done
done

echo "[2/7] sequential pointer chase (prefetch pitfall comparison)"
for rep in $(seq 1 "$REPS"); do
  for sz in $SEQ_SIZES; do
    run latency_seq latency --size "$sz" --stride 64 --mode seq --chains 1 --trials 5
  done
done

echo "[3/7] bandwidth vs working set (read/write mixes, unit-stride payload)"
for rep in $(seq 1 "$REPS"); do
  for rw in read write copy mix70 mix50; do
    for sz in $SIZES; do
      run bw_sweep bandwidth --size "$sz" --stride 8 --mode seq --rw "$rw" --trials 5
    done
  done
done

echo "[4/7] read/write mix at fixed footprints"
for rep in $(seq 1 "$REPS"); do
  for sz in $MIX_SIZES; do
    for rw in read write copy mix70 mix50; do
      run rw_mix bandwidth --size "$sz" --stride 8 --mode seq --rw "$rw" --trials 7
    done
  done
done

echo "[5/7] stride / granularity sweep"
for rep in $(seq 1 "$REPS"); do
  for sz in $STRIDE_SIZES; do
    for st in $STRIDE_LIST; do
      run stride_sweep bandwidth --size "$sz" --stride "$st" --mode seq --rw read --trials 5
    done
    for st in 32 64 128 256 512 1024 2048; do
      run stride_random bandwidth --size "$sz" --stride "$st" --mode random --rw read --trials 5
    done
  done
done

echo "[6/7] memory-level-parallelism / intensity sweep"
for rep in $(seq 1 "$REPS"); do
  for sz in $MLP_SIZES; do
    for k in $CHAIN_LIST; do
      run mlp latency --size "$sz" --stride 64 --mode random --chains "$k" --trials 5
    done
  done
done

echo "[7/7] TLB / huge-page and benchmark-pitfall cases"
for rep in $(seq 1 "$REPS"); do
  for sz in $HUGE_SIZES; do
    for huge in 0 1; do
      for st in 64 4096; do
        run huge_pages latency --size "$sz" --stride "$st" --mode random --chains 1 --trials 5 --huge "$huge"
      done
    done
  done
  # Cache reuse versus DRAM: both look like "bandwidth" but measure different levels.
  run pitfall_cache_reuse bandwidth --size 8388608 --stride 8 --mode seq --rw read --trials 7
  run pitfall_cache_reuse bandwidth --size 1073741824 --stride 8 --mode seq --rw read --trials 7
  # Sequential pointer chase looks much faster because of hardware prefetch.
  run pitfall_prefetch latency --size 536870912 --stride 64 --mode seq --chains 1 --trials 5
  run pitfall_prefetch latency --size 536870912 --stride 64 --mode random --chains 1 --trials 5
done

ROOT="$(cd "$DIR/.." && pwd -P)"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3
"$PY" "$DIR/scripts/summarize.py"
echo "done -> $RAW"
