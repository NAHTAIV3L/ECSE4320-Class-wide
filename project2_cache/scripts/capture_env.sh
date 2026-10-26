#!/bin/bash
# Record host, cache/TLB-relevant settings, and tool availability.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$DIR/results/env"
mkdir -p "$OUT"

{
  echo "# Environment captured: $(date -Is)"
  echo
  echo "## uname / OS"
  uname -a
  cat /etc/os-release 2>/dev/null | head -5 || true
  echo
  echo "## CPU"
  lscpu
  echo
  echo "## Memory"
  free -h
  echo
  echo "## Compiler"
  gcc --version | head -2
  echo
  echo "## Governor / frequency"
  for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo "$f: $(cat "$f" 2>/dev/null || echo unavailable)"
  done
  echo
  echo "## SMT topology"
  for c in /sys/devices/system/cpu/cpu[0-9]*/topology/thread_siblings_list; do
    echo "$c: $(cat "$c" 2>/dev/null || true)"
  done
  echo
  echo "## NUMA"
  lscpu | grep -i numa || true
  echo
  echo "## Transparent huge pages"
  cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
  cat /sys/kernel/mm/transparent_hugepage/defrag 2>/dev/null || true
  echo
  echo "## Tool availability"
  for t in gcc make taskset perf numactl python3; do
    printf '%-10s ' "$t"; command -v "$t" || echo MISSING
  done
  echo
  echo "## perf_event_paranoid"
  cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || true
  echo
  echo "## HugePages"
  grep -i huge /proc/meminfo || true
} > "$OUT/environment.txt" 2>&1

python3 - "$OUT/cache_sizes.csv" <<'PY'
import glob, os, re, sys
rows = []
for idx in sorted(glob.glob('/sys/devices/system/cpu/cpu0/cache/index*')):
    def rd(name):
        try:
            return open(os.path.join(idx, name)).read().strip()
        except OSError:
            return ''
    m = re.match(r'(\d+)([KMG])', rd('size'))
    if not m:
        continue
    n = int(m.group(1)) * {'K': 1024, 'M': 1024**2, 'G': 1024**3}[m.group(2)]
    rows.append((int(rd('level') or 0), rd('type'), n,
                 rd('coherency_line_size'), rd('number_of_sets'),
                 rd('ways_of_associativity')))
with open(sys.argv[1], 'w') as f:
    f.write('level,type,size_bytes,line_bytes,sets,ways\n')
    for r in sorted(rows):
        f.write(','.join(map(str, r)) + '\n')
PY

echo "wrote $OUT/environment.txt and $OUT/cache_sizes.csv"
