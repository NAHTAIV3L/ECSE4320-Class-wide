#!/bin/bash
# Record host, fio, and target-device state.  Read-only; no writes to TARGET.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$DIR/results/env"
mkdir -p "$OUT"
TARGET="${TARGET:-}"

{
  echo "# Environment captured: $(date -Is)"
  echo "TARGET=${TARGET:-<unset>}"
  echo
  echo "## uname / OS"
  uname -a
  cat /etc/os-release 2>/dev/null | head -5 || true
  echo
  echo "## CPU / memory"
  lscpu | head -30
  free -h
  echo
  echo "## fio"
  fio --version 2>/dev/null || echo "fio MISSING"
  echo
  echo "## lsblk"
  lsblk -o NAME,MODEL,SIZE,ROTA,SCHED,TYPE,MOUNTPOINTS 2>/dev/null || lsblk
  echo
  echo "## df / mount"
  df -hT 2>/dev/null || true
  findmnt -T "${TARGET:-/tmp}" 2>/dev/null || true
  echo
  echo "## nvme (if present)"
  nvme list 2>/dev/null || echo "nvme-cli not installed"
  echo
  echo "## smart / health (if present)"
  if command -v smartctl >/dev/null && [ -n "$TARGET" ] && [ -b "$TARGET" ]; then
    smartctl -a "$TARGET" 2>/dev/null | head -60 || true
  fi
  echo
  echo "## transparent huge pages (not storage-critical, recorded for completeness)"
  cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
} > "$OUT/environment.txt" 2>&1

# Record queue/scheduler parameters for the block device backing TARGET.
if [ -n "$TARGET" ]; then
  SRC=$(df --output=source "$TARGET" 2>/dev/null | tail -1 || true)
  if [ -n "$SRC" ] && [ "$SRC" != "Source" ]; then
    DEV=$(basename "$(readlink -f "$SRC")")
    {
      echo "## queue parameters for $DEV"
      for f in scheduler rotational nr_requests max_sectors_kb max_hw_sectors_kb \
               nomerges rq_affinity io_poll; do
        printf '%-22s ' "$f"
        cat "/sys/block/$DEV/queue/$f" 2>/dev/null || echo unavailable
      done
    } >> "$OUT/environment.txt"
  fi
fi

echo "wrote $OUT/environment.txt"
