#!/bin/bash
# Verify that the widest build actually emits vector instructions in the
# benchmark kernel, and capture compiler reports for all kernels/options.
# Full logs live in results/logs/; the summary is the concise evidence.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS="$DIR/results/logs"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$LOGS"

# Kernel source line range (kernel_unit .. end of kernel_stride) so we do not
# count vectorised statistics/re-initialisation loops in main().
START=$(grep -n 'kernel_unit(size_t' "$DIR/main.c" | head -1 | cut -d: -f1)
END=$(awk -v s="$START" 'NR>s && /^}/ {print NR; exit}' "$DIR/main.c")

filter_report() {
  local file=$1
  [ -f "$file" ] || return 0
  awk -F: -v a="$START" -v b="$END" '$1 ~ /main\.c$/ {ln=$2+0; if (ln>=a && ln<=b) print}' "$file"
}

SUMMARY="$LOGS/vectorization_summary.csv"
echo "kernel,optimization,kernel_xmm_refs,kernel_ymm_refs,kernel_zmm_refs,kernel_fma_ymm,vectorized_kernel_reports,missed_kernel_reports" > "$SUMMARY"

for k in saxpy dot multiply stencil; do
  K=$(echo "$k" | tr 'a-z' 'A-Z')
  for o in scalar default widest; do
    case "$o" in
      scalar) OFLAGS="-march=x86-64 -O2 -fno-tree-vectorize -fno-tree-slp-vectorize" ;;
      default) OFLAGS="-march=x86-64 -O3 -ftree-vectorize" ;;
      widest) OFLAGS="-O3 -march=native -mprefer-vector-width=256 -ffast-math -fno-math-errno" ;;
    esac
    base="$LOGS/vec_${k}_${o}"
    rm -f "$base.missed.txt"
    gcc -D${K} -DTYPE=double -DOPT_NAME="$o" $OFLAGS \
        -fopt-info-vec-optimized="$base.optimized.txt" \
        -fopt-info-vec-missed="$base.missed.txt" \
        "$DIR/main.c" -o "$TMP/bin_${k}_${o}" -lm 2>/dev/null || true
    objdump -d "$TMP/bin_${k}_${o}" > "$base.disasm.txt" 2>/dev/null || true
    awk '/<kernel_unit.*>:/{f=1} f{print} f && /^$/{exit}' "$base.disasm.txt" \
        > "$base.kernel_unit.disasm.txt" 2>/dev/null || true
    kd="$base.kernel_unit.disasm.txt"
    xmm=$(grep -c '%xmm' "$kd" 2>/dev/null || true)
    ymm=$(grep -c '%ymm' "$kd" 2>/dev/null || true)
    zmm=$(grep -c '%zmm' "$kd" 2>/dev/null || true)
    fma=$(grep -cE 'vfmadd[^ ]*[[:space:]].*%ymm' "$kd" 2>/dev/null || true)
    opt=$(filter_report "$base.optimized.txt" | grep -c 'loop vectorized' || true)
    miss=$(filter_report "$base.missed.txt" | grep -c 'not vectorized' || true)
    echo "$k,$o,$xmm,$ymm,$zmm,$fma,$opt,$miss" >> "$SUMMARY"
  done
done

{
  echo "Project 1 vectorization verification summary"
  echo "Generated: $(date -Is)"
  echo
  column -s, -t "$SUMMARY"
  echo
  echo "kernel_unit disassembly: xmm_refs/ymm_refs count only the timed kernel."
  echo "Expected: scalar=0, default has baseline-ISA vectors, widest has 256-bit"
  echo "AVX2/FMA. The gather path uses indirect loads and is not expected to"
  echo "vectorise; these logs cover the unit-stride kernel."
  echo
  echo "Selected 'loop vectorized' lines inside the kernel (widest build):"
  for k in saxpy dot multiply stencil; do
    echo "--- $k ---"
    filter_report "$LOGS/vec_${k}_widest.optimized.txt" | grep 'loop vectorized' | head -3 || echo "(none)"
  done
  echo
  echo "Dot reduction, default build (no FP reassociation):"
  filter_report "$LOGS/vec_dot_default.missed.txt" | head -5 || true
  echo
  echo "Full reports and disassembly are kept beside this file; do not paste them."
} > "$LOGS/vectorization_summary.txt"

cp "$SUMMARY" "$DIR/results/data/vectorization_summary.csv"
echo "wrote $LOGS/vectorization_summary.txt"
cat "$LOGS/vectorization_summary.txt"
