#!/bin/bash
# Project 3: safe FIO driver.
#
#   TARGET=/path/to/dedicated/file \
#   CONFIRM_DESTRUCTIVE=I_UNDERSTAND \
#   ./scripts/run_experiments.sh quick
#
# The script refuses to run without an explicit confirmation and a non-system
# target.  Use a dedicated scratch file/partition or a disposable raw device.
# NEVER point TARGET at a filesystem that contains valuable data.
#
# Environment:
#   TARGET              required: scratch file or raw block device
#   CONFIRM_DESTRUCTIVE must be I_UNDERSTAND
#   OVERWRITE=yes       required if TARGET already exists and is non-empty
#   ALLOW_RAW_DEVICE=yes required for a block device
#   TEST_SIZE=8G        file/range size
#   RUNTIME=15 RAMP=3   per-job fio runtime/ramp (seconds)
#   DIRECT=1            1=O_DIRECT, 0=buffered (used in the pitfall run)
#   PRECONDITION=1      write the whole range before measuring (steady state)
#   SETTLE=10           seconds to idle between phases (lets GC/flush drain)
#   MIX_SETTLE=2        seconds to idle between read/write-mix jobs
#   DRY_RUN=1           print commands and exit without touching TARGET
#   PERF_CORE=2         CPU for the fio process (taskset)
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-quick}"
TARGET="${TARGET:-}"
CONFIRM="${CONFIRM_DESTRUCTIVE:-}"
OVERWRITE="${OVERWRITE:-no}"
ALLOW_RAW_DEVICE="${ALLOW_RAW_DEVICE:-no}"
TEST_SIZE="${TEST_SIZE:-8G}"
RUNTIME="${RUNTIME:-$([ "$MODE" = full ] && echo 20 || echo 8)}"
RAMP="${RAMP:-$([ "$MODE" = full ] && echo 5 || echo 2)}"
DIRECT="${DIRECT:-1}"
PRECONDITION="${PRECONDITION:-0}"
SETTLE="${SETTLE:-10}"
MIX_SETTLE="${MIX_SETTLE:-2}"
DRY_RUN="${DRY_RUN:-0}"
PERF_CORE="${PERF_CORE:-2}"
RAW="$DIR/results/raw"
mkdir -p "$RAW" "$DIR/results/data" "$DIR/results/graphs" "$DIR/results/env" "$DIR/results/logs"
if [ "$DRY_RUN" != "1" ]; then
  rm -f "$RAW"/*.json
fi

usage() {
  sed -n '2,30p' "$0"
}
abort() { echo "REFUSING: $*" >&2; exit 1; }

[ -n "$TARGET" ] || { usage; abort "TARGET is required"; }
[ "$CONFIRM" = "I_UNDERSTAND" ] || abort "set CONFIRM_DESTRUCTIVE=I_UNDERSTAND after reading the safety warning"

TARGET_REAL=$(readlink -f "$TARGET" 2>/dev/null || true)
[ -n "$TARGET_REAL" ] || abort "invalid TARGET"
case "$TARGET_REAL" in
  /|/home|/root|/boot|/usr|/etc|/var|/dev|/proc|/sys)
    abort "TARGET resolves to a system path ($TARGET_REAL)";;
esac

if [ -b "$TARGET_REAL" ]; then
  [ "$ALLOW_RAW_DEVICE" = "yes" ] || abort "raw device requires ALLOW_RAW_DEVICE=yes"
  if findmnt -S "$TARGET_REAL" >/dev/null 2>&1; then
    abort "raw device is mounted"
  fi
  ROOT_SRC=$(findmnt -no SOURCE / 2>/dev/null || true)
  if [ -n "$ROOT_SRC" ] && [ "$(readlink -f "$ROOT_SRC" 2>/dev/null)" = "$TARGET_REAL" ]; then
    abort "TARGET is the root device"
  fi
else
  PARENT=$(dirname "$TARGET_REAL")
  [ -d "$PARENT" ] || abort "parent directory does not exist: $PARENT"
  if [ -e "$TARGET_REAL" ] && [ ! -f "$TARGET_REAL" ]; then
    abort "TARGET exists and is not a regular file"
  fi
  if [ -e "$TARGET_REAL" ] && [ "$(stat -c%s "$TARGET_REAL")" -gt 0 ] && [ "$OVERWRITE" != "yes" ]; then
    abort "TARGET exists and is non-empty; set OVERWRITE=yes to reuse it"
  fi
fi

if [ "$DRY_RUN" != "1" ]; then
  command -v fio >/dev/null || abort "fio is not installed (install fio or use DRY_RUN=1)"
  taskset -c "$PERF_CORE" true 2>/dev/null || PERF_CORE=0
  if [ ! -b "$TARGET_REAL" ]; then
    if [ ! -e "$TARGET_REAL" ]; then
      if command -v fallocate >/dev/null; then fallocate -l "$TEST_SIZE" "$TARGET_REAL";
      else truncate -s "$TEST_SIZE" "$TARGET_REAL"; fi
    else
      CUR=$(stat -c%s "$TARGET_REAL")
      # Grow an existing scratch file if needed; never shrink it.
      if command -v numfmt >/dev/null; then NEED=$(numfmt --from=iec "$TEST_SIZE"); else NEED=0; fi
      if [ "$NEED" -gt 0 ] && [ "$CUR" -lt "$NEED" ]; then
        if command -v fallocate >/dev/null; then fallocate -l "$TEST_SIZE" "$TARGET_REAL";
        else truncate -s "$TEST_SIZE" "$TARGET_REAL"; fi
      fi
    fi
    AVAIL=$(df -B1 --output=avail "$PARENT" | tail -1)
    NEED=$(numfmt --from=iec "$TEST_SIZE" 2>/dev/null || echo 0)
    [ "$AVAIL" -gt $((NEED + 1073741824)) ] || abort "not enough free space in $PARENT"
  fi
  TARGET="$TARGET_REAL"
  "$DIR/scripts/capture_env.sh" >/dev/null || true
  if [ "$PRECONDITION" = "1" ]; then
    echo "preconditioning (sequential write of $TEST_SIZE) ..."
    fio --name=precondition --filename="$TARGET" --rw=write --bs=128k \
        --iodepth=32 --numjobs=1 --size="$TEST_SIZE" --ioengine=libaio \
        --direct=1 --group_reporting=1 --output-format=json \
        --output="$RAW/precondition.json" >/dev/null
  fi
else
  echo "DRY RUN: no fio invocation and no writes to $TARGET"
fi

RUN_ID=0
run_fio() { # name rw bs iodepth numjobs exp [runtime] [rwmixread] [direct] [size]
  local name=$1 rw=$2 bs=$3 qd=$4 nj=$5 exp=$6 rt=${7:-$RUNTIME}
  local mix=${8:-} dir=${9:-$DIRECT} size=${10:-$TEST_SIZE}
  RUN_ID=$((RUN_ID + 1))
  local out
  out=$(printf "%s/%03d_%s_%s.json" "$RAW" "$RUN_ID" "$exp" "$name")
  local -a cmd=(fio --name="$name" --filename="$TARGET" --rw="$rw" --bs="$bs"
    --iodepth="$qd" --numjobs="$nj" --size="$size" --ioengine=libaio
    --direct="$dir" --time_based=1 --runtime="$rt" --ramp_time="$RAMP"
    --group_reporting=1 --randrepeat=1 --norandommap=1 --invalidate=1
    --percentile_list=50:95:99:99.9 --description="$exp"
    --output-format=json --output="$out")
  if [ -n "$mix" ]; then cmd+=(--rwmixread="$mix"); fi
  if [ "$DRY_RUN" = "1" ]; then
    printf 'taskset -c %s ' "$PERF_CORE"; printf '%q ' "${cmd[@]}"; echo
    return 0
  fi
  echo "[$RUN_ID] $exp $name"
  taskset -c "$PERF_CORE" "${cmd[@]}"
}

SMALL=1G
if [ "$MODE" = "full" ]; then
  BSS="${BSS:-4k 8k 16k 32k 64k 128k 256k 512k 1m 2m}"
  QDS="${QDS:-1 2 3 4 6 8 12 16 24 32 48 64 96 128 192 256 384 512}"
  NJS="${NJS:-1 2 3 4 6 8 12}"
  DEFAULT_LBA="1G 2G 4G 8G $TEST_SIZE"
else
  BSS="${BSS:-4k 8k 16k 32k 64k 128k 256k 512k}"
  QDS="${QDS:-1 2 4 8 16 32 48 64 96 128 192 256}"
  NJS="${NJS:-1 2 3 4 6 8}"
  DEFAULT_LBA="1G 2G 4G $TEST_SIZE"
fi
MIXES="${MIXES:-100 90 80 70 60 50 40 30 20 10 0}"
LBA_QDS="${LBA_QDS:-1 8 32}"
LBA_SIZES="${LBA_SIZES:-$DEFAULT_LBA}"
LBA_SIZES=$(printf '%s\n' $LBA_SIZES | awk '!seen[$0]++' | tr '\n' ' ')

settle() {
  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY RUN: would settle ${SETTLE}s"
  else
    echo "settling ${SETTLE}s (GC/flush) ..."
    sync
    sleep "$SETTLE"
  fi
}

echo "== [1/8] QD=1 baselines =="
run_fio r4k randread 4k 1 1 qd1_baseline
run_fio w4k randwrite 4k 1 1 qd1_baseline
run_fio s128k read 128k 1 1 qd1_baseline
run_fio sw128k write 128k 1 1 qd1_baseline

settle
echo "== [2/8] queue-depth / parallelism sweep =="
for rw in randread randwrite; do
  for qd in $QDS; do
    run_fio "${rw}_qd${qd}" "$rw" 4k "$qd" 1 qd_sweep
  done
done
for nj in $NJS; do
  run_fio "randread_nj${nj}_qd8"  randread  4k 8 "$nj" parallel_sweep
  run_fio "randwrite_nj${nj}_qd8" randwrite 4k 8 "$nj" parallel_sweep
done

settle
echo "== [3/8] working-set / LBA-range sweep =="
for sz in $LBA_SIZES; do
  for qd in $LBA_QDS; do
    run_fio "randread_${sz}_qd${qd}" randread 4k "$qd" 1 lba_range "$RUNTIME" "" "$DIRECT" "$sz"
  done
done

settle
echo "== [4/8] misleading-setup case: buffered vs direct I/O =="
run_fio buffered_4k randread 4k 1 1 pitfall_buffered "$RUNTIME" "" 0
run_fio direct_4k   randread 4k 1 1 pitfall_buffered "$RUNTIME" "" 1

echo "== [5/8] misleading-setup case: effective queue depth =="
run_fio iodepth8_nj1 randread 4k 8 1 pitfall_effective_qd
run_fio iodepth1_nj8 randread 4k 1 8 pitfall_effective_qd

settle
echo "== [6/8] block-size sweep (random and sequential, qd1/qd32) =="
for bs in $BSS; do
  run_fio "randread_${bs}_qd1"  randread  "$bs" 1  1 bs_sweep
  run_fio "randwrite_${bs}_qd1" randwrite "$bs" 1  1 bs_sweep
  run_fio "randread_${bs}_qd32" randread  "$bs" 32 1 bs_sweep
  run_fio "randwrite_${bs}_qd32" randwrite "$bs" 32 1 bs_sweep
  run_fio "read_${bs}_qd1"      read      "$bs" 1  1 bs_sweep
  run_fio "write_${bs}_qd1"     write     "$bs" 1  1 bs_sweep
done

settle
echo "== [7/8] read/write mix sweep =="
for mix in $(echo $MIXES | tr " " "\n" | sort -n); do
  for qd in 1 8 32; do
    rw=randrw; [ "$mix" = 100 ] && rw=randread
    [ "$mix" = 0 ] && rw=randwrite
    if [ "$mix" = 100 ] || [ "$mix" = 0 ]; then
      run_fio "mix${mix}_qd${qd}" "$rw" 4k "$qd" 1 rw_mix
    else
      run_fio "mix${mix}_qd${qd}" randrw 4k "$qd" 1 rw_mix "$RUNTIME" "$mix"
    fi
    if [ "$DRY_RUN" = "1" ]; then
      echo "DRY RUN: would settle ${MIX_SETTLE}s after mix${mix}_qd${qd}"
    else
      sleep "$MIX_SETTLE"
    fi
  done
done

settle
echo "== [8/8] steady-state / device-state pair =="
run_fio state_a randwrite 128k 32 1 device_state
run_fio state_b randwrite 128k 32 1 device_state

ROOT="$(cd "$DIR/.." && pwd -P)"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3
if [ "$DRY_RUN" != "1" ]; then
  "$PY" "$DIR/scripts/parse_fio.py"
  echo "done; raw JSON in $RAW"
else
  echo "dry run complete"
fi
