#!/bin/bash
# Record the measurement environment and cache geometry for Project 1.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$DIR/results/env"
mkdir -p "$OUT"

{
  echo "# Environment captured: $(date -Is)"
  echo
  echo "## uname"
  uname -a
  echo
  echo "## CPU"
  lscpu
  echo
  echo "## Compiler"
  gcc --version | head -2
  echo
  echo "## Governor / frequency"
  for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo "$f: $(cat "$f" 2>/dev/null || echo unavailable)"
  done
  echo
  echo "## SMT topology (CPU -> thread siblings)"
  for c in /sys/devices/system/cpu/cpu[0-9]*/topology/thread_siblings_list; do
    echo "$c: $(cat "$c" 2>/dev/null || true)"
  done
  echo
  echo "## Transparent huge pages"
  cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
  echo
  echo "## Tool availability"
  for t in gcc ninja taskset perf objdump python3; do
    printf '%-10s ' "$t"; command -v "$t" || echo MISSING
  done
  echo
  echo "## Build flags (from build.ninja)"
  grep -E '^cflags-.*(widest|scalar)-double' "$DIR/build.ninja" 2>/dev/null || true
} > "$OUT/environment.txt" 2>&1

# Cache geometry as CSV, useful to annotate working-set plots.
python3 - "$OUT/cache_sizes.csv" <<'PY'
import glob, os, re, sys
rows = []
for idx in sorted(glob.glob('/sys/devices/system/cpu/cpu0/cache/index*')):
    def rd(name):
        try:
            return open(os.path.join(idx, name)).read().strip()
        except OSError:
            return ''
    size = rd('size')
    m = re.match(r'(\d+)([KMG])', size)
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
