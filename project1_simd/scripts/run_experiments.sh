#!/bin/bash
# Project 1 driver.  Usage: ./run_experiments.sh [quick|full]
#
# Produces:
#   results/data/measurements.csv  summary statistics, one row per process run
#   results/data/raw_trials.csv    every timed trial (for re-analysis)
#   results/env/*                  environment metadata
#
# Environment overrides: PERF_CORE, WARMUP, RUNS, REPS, STRIDE_SIZES,
#                       STRIDE_LIST, GATHER_SIZES
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-quick}"
mkdir -p "$DIR/results/data" "$DIR/results/graphs" "$DIR/results/logs" "$DIR/results/env"

if [ "$MODE" = "full" ]; then
  MAXP=28; WARMUP=${WARMUP:-8}; RUNS=${RUNS:-30}; REPS=${REPS:-2}
  STRIDE_SIZES="${STRIDE_SIZES:-262144 16777216}"
  STRIDE_LIST="${STRIDE_LIST:-1 2 3 4 6 8 12 16 24 32 48 64 96 128 192 256}"
  GATHER_SIZES="${GATHER_SIZES:-$STRIDE_SIZES}"
  ALIGN_SIZES="${ALIGN_SIZES:-262144 16777216}"
  ALIGN_SHIFTS="${ALIGN_SHIFTS:-0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 23 24 31 32 47 48 63 64 95 96 127}"
else
  MAXP=24; WARMUP=${WARMUP:-4}; RUNS=${RUNS:-20}; REPS=${REPS:-1}
  STRIDE_SIZES="${STRIDE_SIZES:-262144}"
  STRIDE_LIST="${STRIDE_LIST:-1 2 3 4 6 8 12 16 24 32 48 64 96 128}"
  GATHER_SIZES="${GATHER_SIZES:-$STRIDE_SIZES}"
  ALIGN_SIZES="${ALIGN_SIZES:-262144}"
  ALIGN_SHIFTS="${ALIGN_SHIFTS:-0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 23 24 31 32 47 48 63 64}"
fi

PERF_CORE=${PERF_CORE:-2}
taskset -c "$PERF_CORE" true 2>/dev/null || PERF_CORE=0

# Intermediate 1.5x points between powers of two, plus the powers themselves.
if [ -z "${WORKING_SIZES:-}" ]; then
  WORKING_SIZES=""
  for p in $(seq 10 "$MAXP"); do
    WORKING_SIZES="$WORKING_SIZES $((1 << p))"
    if [ "$p" -lt "$MAXP" ]; then
      WORKING_SIZES="$WORKING_SIZES $((3 << (p - 1)))"
    fi
  done
fi

OUT="$DIR/results/data/measurements.csv"
RAW="$DIR/results/data/raw_trials.csv"
: > "$RAW"
echo "kernel,optimization,type_size,n,stride,align_shift_elems,align_offset_bytes,touched,valid,runs,mean_s,median_s,min_s,max_s,sd_s,GiB_s,GFLOP_s,arithmetic_intensity,exp_type,process_rep" > "$OUT"

KERNELS="saxpy dot multiply stencil"
OPTS="scalar default widest"
TYPES="float double"

run_one() { # kernel opt type n stride shift exp rep
  local k=$1 o=$2 ty=$3 n=$4 st=$5 sh=$6 exp=$7 rep=$8
  local bin="$DIR/build/${k}-${o}-${ty}"
  [ -x "$bin" ] || { echo "missing $bin" >&2; return 0; }
  local row
  row=$(taskset -c "$PERF_CORE" "$bin" "$n" "$WARMUP" "$RUNS" "$st" "$sh" "$RAW" 2>/dev/null | tail -1) || true
  [ -z "$row" ] && return 0
  echo "${row},${exp},${rep}" >> "$OUT"
}

"$DIR/scripts/capture_env.sh" >/dev/null || true
GOV=$(cat /sys/devices/system/cpu/cpu${PERF_CORE}/cpufreq/scaling_governor 2>/dev/null || echo unknown)
echo "mode=$MODE core=$PERF_CORE governor=$GOV warmup=$WARMUP runs=$RUNS reps=$REPS"

echo "[1/6] working-set / locality sweep (2^10..2^${MAXP}, powers + 1.5x intermediates)"
for rep in $(seq 1 "$REPS"); do
  for k in $KERNELS; do
    for o in $OPTS; do
      for ty in $TYPES; do
        for n in $WORKING_SIZES; do
          run_one "$k" "$o" "$ty" "$n" 1 0 working_set "$rep"
        done
      done
    done
  done
done

echo "[2/6] stride sweep"
for rep in $(seq 1 "$REPS"); do
  for k in $KERNELS; do
    for o in $OPTS; do
      for ty in $TYPES; do
        for n in $STRIDE_SIZES; do
          for st in $STRIDE_LIST; do
            run_one "$k" "$o" "$ty" "$n" "$st" 0 stride "$rep"
          done
        done
      done
    done
  done
done

echo "[3/6] gather / random-permutation sweep"
for rep in $(seq 1 "$REPS"); do
  for k in $KERNELS; do
    for o in $OPTS; do
      for ty in $TYPES; do
        for n in $GATHER_SIZES; do
          run_one "$k" "$o" "$ty" "$n" 0 0 gather "$rep"
        done
      done
    done
  done
done

echo "[4/6] alignment / unaligned-start sweep"
for rep in $(seq 1 "$REPS"); do
  for k in $KERNELS; do
    for o in $OPTS; do
      for ty in $TYPES; do
        for n in $ALIGN_SIZES; do
          for sh in $ALIGN_SHIFTS; do
            run_one "$k" "$o" "$ty" "$n" 1 "$sh" alignment "$rep"
          done
        done
      done
    done
  done
done

echo "[5/6] tail-handling sweep (sizes around 2^20)"
for rep in $(seq 1 "$REPS"); do
  for k in $KERNELS; do
    for o in $OPTS; do
      for ty in $TYPES; do
        for delta in -15 -11 -7 -5 -3 -2 -1 0 1 2 3 4 5 6 7 8 11 13 15 16 23 31 32 47 63 64 95 127; do
          n=$((1048576 + delta))
          run_one "$k" "$o" "$ty" "$n" 1 0 tail "$rep"
        done
      done
    done
  done
done

echo "[6/6] negative examples: large-N memory bound"
for rep in $(seq 1 "$REPS"); do
  for ty in $TYPES; do
    for o in $OPTS; do
      run_one multiply  "$o" "$ty" 268435456 1 0 negative "$rep"
      run_one stencil   "$o" "$ty" 268435456 1 0 negative "$rep"
    done
  done
done

ROWS=$(( $(wc -l < "$OUT") - 1 ))
echo "done: $ROWS rows -> $OUT"
echo "raw trials -> $RAW"
