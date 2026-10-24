#!/usr/bin/env python3
"""Project 1 plotting / analysis.

Reads results/data/measurements.csv and emits every figure required by the
project handout:

  01 throughput vs working set (scalar/default/widest, both metrics)
  02 speedup vs working set
  03 locality sweep with cache-level annotations
  04 alignment / unaligned-start study
  05 tail-handling study
  06 stride / gather study + SIMD efficiency
  07 data-type comparison
  08 roofline (measured bandwidth, estimated single-core peak FLOP/s)
  09 negative examples (memory-bound compression)
  10 gather vs unit stride + gather SIMD efficiency
  11 summary speedup table
"""
import os
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(DIR, "results/data/measurements.csv")
OUT = os.path.join(DIR, "results/graphs")
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({"figure.dpi": 150, "font.size": 10, "axes.grid": True,
                     "grid.alpha": 0.35})

if not os.path.exists(DATA):
    sys.exit(f"missing {DATA}; run scripts/run_experiments.sh first")
df = pd.read_csv(DATA)
for c in ("type_size", "n", "stride", "align_shift_elems", "mean_s", "sd_s",
          "GiB_s", "GFLOP_s", "arithmetic_intensity"):
    df[c] = pd.to_numeric(df[c], errors="coerce")
df = df[df["valid"] == 1].copy()

KERNELS = [k for k in ["saxpy", "dot", "multiply", "stencil"]
           if k in set(df["kernel"])]
OPTS = ["scalar", "default", "widest"]
COLORS = {"scalar": "#444444", "default": "#1f77b4", "widest": "#d62728"}
DTYPES = sorted(df["type_size"].unique())
DT_LABEL = {4: "float32", 8: "float64"}

# ---------------------------------------------------------------- cache info
CACHE = {"L1": 48 * 1024, "L2": 2048 * 1024, "L3": 24576 * 1024}
cache_csv = os.path.join(DIR, "results/env/cache_sizes.csv")
if os.path.exists(cache_csv):
    try:
        cdf = pd.read_csv(cache_csv)
        for lvl in ("L1", "L2", "L3"):
            row = cdf[(cdf["level"].astype(str) == lvl[1]) &
                      (cdf["type"].str.lower().str.startswith("data") |
                       cdf["type"].str.lower().str.startswith("unified"))]
            if not row.empty:
                CACHE[lvl] = int(row["size_bytes"].iloc[0])
    except Exception:
        pass


def aggregate(frame, keys):
    """Average process repetitions and combine within/between-run variance."""
    rows = []
    for key, g in frame.groupby(keys, dropna=False):
        if not isinstance(key, tuple):
            key = (key,)
        within = (g["GiB_s"] * g["sd_s"] / g["mean_s"].replace(0, np.nan)) ** 2
        row = dict(zip(keys, key))
        row["n"] = g["n"].iloc[0]
        row["mean_s"] = g["mean_s"].mean()
        row["GiB_s"] = g["GiB_s"].mean()
        row["GFLOP_s"] = g["GFLOP_s"].mean()
        row["GiB_s_sd"] = np.sqrt(g["GiB_s"].var(ddof=1)
                                  if len(g) > 1 else 0.0
                                  + within.mean())
        row["GFLOP_s_sd"] = np.sqrt(g["GFLOP_s"].var(ddof=1)
                                    if len(g) > 1 else 0.0
                                    + ((g["GFLOP_s"] * g["sd_s"] /
                                        g["mean_s"].replace(0, np.nan)) ** 2).mean())
        row["arithmetic_intensity"] = g["arithmetic_intensity"].iloc[0]
        row["reps"] = len(g)
        rows.append(row)
    return pd.DataFrame(rows)


def save(fig, name):
    path = os.path.join(OUT, name)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def exp(frame, name):
    return frame[frame["exp_type"] == name].copy()


# ------------------------------------------------- 01/02 working-set curves
ws = exp(df, "working_set")
for kernel in KERNELS:
    for dt in DTYPES:
        sub = ws[(ws.kernel == kernel) & (ws.type_size == dt)]
        if sub.empty:
            continue
        a = aggregate(sub, ["optimization", "n"]).sort_values("n")
        fig, axes = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
        for opt in OPTS:
            s = a[a.optimization == opt]
            if s.empty:
                continue
            axes[0].errorbar(s.n, s.GFLOP_s, yerr=s.GFLOP_s_sd, marker="o",
                             ms=3, lw=1.7, capsize=2, color=COLORS[opt], label=opt)
            axes[1].errorbar(s.n, s.GiB_s, yerr=s.GiB_s_sd, marker="o",
                             ms=3, lw=1.7, capsize=2, color=COLORS[opt], label=opt)
        axes[0].set_ylabel("GFLOP/s")
        axes[1].set_ylabel("Effective GiB/s")
        axes[1].set_xlabel("Working-set size n (elements)")
        axes[1].set_xscale("log", base=2)
        axes[0].set_xscale("log", base=2)
        axes[0].legend(ncol=3)
        fig.suptitle(f"{kernel.upper()} -- scalar vs autovectorized vs widest "
                     f"({DT_LABEL.get(dt, dt)})")
        save(fig, f"01_throughput_vs_working_set_{kernel}_{dt}B.png")

        # speedup
        piv = a.pivot_table(index="n", columns="optimization", values="GFLOP_s")
        if "scalar" not in piv:
            continue
        fig, ax = plt.subplots(figsize=(7.5, 4.5))
        for opt in ("default", "widest"):
            if opt in piv:
                ax.plot(piv.index, piv[opt] / piv["scalar"], marker="o", ms=4,
                        lw=1.8, color=COLORS[opt], label=f"{opt}/scalar")
        ax.axhline(1.0, color="k", ls="--", lw=1)
        ax.set_xscale("log", base=2)
        ax.set_xlabel("Working-set size n (elements)")
        ax.set_ylabel("Speedup vs scalar")
        ax.set_title(f"{kernel.upper()} speedup ({DT_LABEL.get(dt, dt)})")
        ax.legend()
        save(fig, f"02_speedup_vs_working_set_{kernel}_{dt}B.png")

# ------------------------------------------------- 03 locality sweep
for kernel in [k for k in ("dot", "multiply", "stencil") if k in KERNELS]:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    for dt in DTYPES:
        sub = ws[(ws.kernel == kernel) & (ws.type_size == dt)]
        if sub.empty:
            continue
        a = aggregate(sub, ["optimization", "n"]).sort_values("n")
        for opt in ("scalar", "widest"):
            s = a[a.optimization == opt]
            if s.empty:
                continue
            ls = "-" if dt == 4 else "--"
            axes[0].plot(s.n * dt, s.GFLOP_s, ls, marker="o", ms=3,
                         color=COLORS[opt], label=f"{opt} {DT_LABEL.get(dt, dt)}")
        piv = a.pivot_table(index="n", columns="optimization", values="GFLOP_s")
        if "widest" in piv and "scalar" in piv:
            axes[1].plot(piv.index * dt, piv["widest"] / piv["scalar"],
                         "-" if dt == 4 else "--", marker="o", ms=3,
                         label=DT_LABEL.get(dt, dt))
    for lvl, size in CACHE.items():
        for ax in axes:
            ax.axvline(size, color="gray", ls=":", lw=1)
            ax.text(size, ax.get_ylim()[1] * 0.92, lvl, ha="center",
                    fontsize=9, color="gray")
    axes[0].set_xscale("log", base=2)
    axes[0].set_xlabel("Footprint per array (bytes)")
    axes[0].set_ylabel("GFLOP/s")
    axes[0].set_title("Locality sweep: SIMD gains compress when memory-bound")
    axes[0].legend(fontsize=8)
    axes[1].set_xscale("log", base=2)
    axes[1].axhline(1.0, color="k", ls="--", lw=1)
    axes[1].set_xlabel("Footprint per array (bytes)")
    axes[1].set_ylabel("widest / scalar speedup")
    axes[1].set_title("Speedup across cache levels")
    axes[1].legend(fontsize=8)
    save(fig, f"03_locality_sweep_{kernel}.png")

# ------------------------------------------------- 04 alignment
al = exp(df, "alignment")
for kernel in KERNELS:
    for dt in DTYPES:
        sub = al[(al.kernel == kernel) & (al.type_size == dt)]
        if sub.empty:
            continue
        sizes = sorted(sub.n.unique())
        fig, axes = plt.subplots(1, len(sizes), figsize=(6 * len(sizes), 4.4),
                                 squeeze=False)
        for ax, n in zip(axes[0], sizes):
            s0 = sub[sub.n == n]
            a = aggregate(s0, ["optimization", "align_offset_bytes"]).sort_values(
                "align_offset_bytes")
            for opt in OPTS:
                s = a[a.optimization == opt]
                if s.empty:
                    continue
                ax.plot(s.align_offset_bytes, s.GiB_s, marker="o", ms=4, lw=1.7,
                        color=COLORS[opt], label=opt)
            ax.set_title(f"{kernel.upper()} n={n} ({DT_LABEL.get(dt, dt)})")
            ax.set_xlabel("Array start offset (bytes, element-rounded)")
            ax.set_ylabel("Effective GiB/s")
            ax.legend(fontsize=8)
        save(fig, f"04_alignment_{kernel}_{dt}B.png")

# ------------------------------------------------- 05 tail handling
tl = exp(df, "tail")
for kernel in KERNELS:
    for dt in DTYPES:
        sub = tl[(tl.kernel == kernel) & (tl.type_size == dt)]
        if sub.empty:
            continue
        a = aggregate(sub, ["optimization", "n"]).sort_values("n")
        fig, ax = plt.subplots(figsize=(7.5, 4.5))
        for opt in OPTS:
            s = a[a.optimization == opt]
            if s.empty:
                continue
            ax.plot(s.n, s.GFLOP_s, marker="o", ms=4, lw=1.7,
                    color=COLORS[opt], label=opt)
        ax.set_xlabel("n (elements around 2^20)")
        ax.set_ylabel("GFLOP/s")
        ax.set_title(f"{kernel.upper()} tail/prologue-epilogue handling "
                     f"({DT_LABEL.get(dt, dt)})")
        ax.legend()
        save(fig, f"05_tail_handling_{kernel}_{dt}B.png")

# ------------------------------------------------- 06 stride / gather
st = exp(df, "stride")
for kernel in KERNELS:
    for dt in DTYPES:
        sub = st[(st.kernel == kernel) & (st.type_size == dt)]
        if sub.empty:
            continue
        sizes = sorted(sub.n.unique())
        fig, axes = plt.subplots(1, len(sizes), figsize=(6 * len(sizes), 4.4),
                                 squeeze=False)
        for ax, n in zip(axes[0], sizes):
            s0 = sub[sub.n == n]
            a = aggregate(s0, ["optimization", "stride"]).sort_values("stride")
            for opt in OPTS:
                s = a[a.optimization == opt]
                if s.empty:
                    continue
                ax.plot(s.stride, s.GiB_s, marker="o", ms=4, lw=1.7,
                        color=COLORS[opt], label=opt)
            ax.set_title(f"{kernel.upper()} n={n} ({DT_LABEL.get(dt, dt)})")
            ax.set_xlabel("Stride (elements)")
            ax.set_ylabel("Effective GiB/s")
            ax.set_xscale("log", base=2)
            ax.legend(fontsize=8)
        save(fig, f"06_stride_{kernel}_{dt}B.png")

        # SIMD efficiency: widest/scalar at each stride
        fig, ax = plt.subplots(figsize=(7.5, 4.5))
        for n in sizes:
            s0 = sub[sub.n == n]
            a = aggregate(s0, ["optimization", "stride"])
            piv = a.pivot_table(index="stride", columns="optimization",
                                values="GiB_s")
            if "widest" in piv and "scalar" in piv:
                ax.plot(piv.index, piv["widest"] / piv["scalar"], marker="o",
                        ms=4, lw=1.7, label=f"n={n}")
        ax.axhline(1.0, color="k", ls="--", lw=1)
        ax.set_xscale("log", base=2)
        ax.set_xlabel("Stride (elements)")
        ax.set_ylabel("widest / scalar throughput")
        ax.set_title(f"{kernel.upper()} SIMD efficiency vs stride "
                     f"({DT_LABEL.get(dt, dt)})")
        ax.legend(fontsize=8)
        save(fig, f"07_stride_efficiency_{kernel}_{dt}B.png")

# ------------------------------------------------- 08 data-type comparison
for kernel in KERNELS:
    sub = ws[(ws.kernel == kernel) & (ws.optimization == "widest")]
    if sub.type_size.nunique() < 2:
        continue
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    for dt in DTYPES:
        s = aggregate(sub[sub.type_size == dt], ["n"]).sort_values("n")
        if s.empty:
            continue
        axes[0].plot(s.n, s.GFLOP_s, marker="o", ms=3, lw=1.7,
                     label=DT_LABEL.get(dt, dt))
        axes[1].plot(s.n, s.GiB_s, marker="o", ms=3, lw=1.7,
                     label=DT_LABEL.get(dt, dt))
    for ax, ylabel in zip(axes, ("GFLOP/s", "Effective GiB/s")):
        ax.set_xscale("log", base=2)
        ax.set_xlabel("Working-set size n (elements)")
        ax.set_ylabel(ylabel)
        ax.legend()
    axes[0].set_title(f"{kernel.upper()} float32 vs float64 (widest)")
    save(fig, f"08_dtype_comparison_{kernel}.png")

# ------------------------------------------------- 09 roofline
# Measured streaming bandwidth: best observed effective GiB/s at large N.
bw_candidates = ws[(ws.optimization == "widest") & (ws.n * ws.type_size >= CACHE["L3"])]
bw_meas = float(bw_candidates.GiB_s.max()) if not bw_candidates.empty else 20.0
# Single-core AVX2 peak estimates.  Override with env if needed.
PEAK = {4: float(os.environ.get("PEAK_GFLOPS_F32", 160.0)),
        8: float(os.environ.get("PEAK_GFLOPS_F64", 80.0))}
ks = KERNELS
for dt in DTYPES:
    sub = ws[(ws.type_size == dt) & (ws.optimization == "widest") &
             (ws.kernel.isin(ks))]
    if sub.empty:
        continue
    best = (sub.groupby("kernel", as_index=False)
               .agg(ai=("arithmetic_intensity", "first"),
                    gflops=("GFLOP_s", "max")))
    fig, ax = plt.subplots(figsize=(7.5, 5))
    x = np.logspace(-3, 1.5, 400)
    roof = np.minimum(PEAK[dt], bw_meas * x)
    ax.plot(x, roof, "k--", lw=2,
            label=f"roofline (BW={bw_meas:.1f} GiB/s, peak={PEAK[dt]:.0f} GFLOP/s)")
    ax.scatter(best.ai, best.gflops, s=70, zorder=3, color="#d62728")
    for _, row in best.iterrows():
        ax.annotate(row.kernel, (row.ai, row.gflops), textcoords="offset points",
                    xytext=(5, 5), fontsize=8)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Arithmetic intensity (FLOP/byte)")
    ax.set_ylabel("Achieved GFLOP/s")
    ax.set_title(f"Roofline interpretation ({DT_LABEL.get(dt, dt)}, widest)")
    ax.legend(fontsize=8)
    save(fig, f"09_roofline_{dt}B.png")

# ------------------------------------------------- 10 negative examples
fig, ax = plt.subplots(figsize=(7.5, 4.6))
for kernel in ("multiply", "stencil"):
    sub = ws[ws.kernel == kernel]
    if sub.empty:
        continue
    a = aggregate(sub, ["optimization", "n", "type_size"])
    for dt in DTYPES:
        s = a[a.type_size == dt]
        piv = s.pivot_table(index="n", columns="optimization", values="GFLOP_s")
        if "widest" in piv and "scalar" in piv:
            ax.plot(piv.index, piv["widest"] / piv["scalar"], marker="o",
                    ms=3, lw=1.7, label=f"{kernel} {DT_LABEL.get(dt, dt)}")
ax.axhline(1.0, color="k", ls="--", lw=1)
ax.set_xscale("log", base=2)
ax.set_xlabel("Working-set size n")
ax.set_ylabel("widest / scalar")
ax.set_title("Negative: vectorized but DRAM-bandwidth bound")
ax.legend(fontsize=8)
save(fig, "10_negative_examples.png")

# ------------------------------------------------- 11 gather vs unit stride
g = exp(df, "gather")
u = exp(df, "stride")
u = u[u.stride == 1]
for kernel in KERNELS:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), squeeze=False)
    for ax, dt in zip(axes[0], DTYPES):
        for opt in OPTS:
            su = aggregate(u[(u.kernel == kernel) & (u.type_size == dt) &
                             (u.optimization == opt)], ["n"]).sort_values("n")
            sg = aggregate(g[(g.kernel == kernel) & (g.type_size == dt) &
                             (g.optimization == opt)], ["n"]).sort_values("n")
            if not su.empty:
                ax.plot(su.n, su.GiB_s, "--", marker="o", ms=4, lw=1.5,
                        color=COLORS[opt], label=f"{opt} unit")
            if not sg.empty:
                ax.plot(sg.n, sg.GiB_s, "-", marker="s", ms=4, lw=1.5,
                        color=COLORS[opt], label=f"{opt} gather")
        ax.set_xscale("log", base=2)
        ax.set_xlabel("Working-set size n (elements)")
        ax.set_ylabel("Effective GiB/s")
        ax.set_title(f"{kernel.upper()} ({DT_LABEL.get(dt, dt)})")
        ax.legend(fontsize=7, ncol=2)
    save(fig, f"11_gather_vs_unit_{kernel}.png")

# ------------------------------------------------- 12 gather SIMD efficiency
for kernel in KERNELS:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), squeeze=False)
    for ax, dt in zip(axes[0], DTYPES):
        for label, frame, ls, marker in (("unit", u, "--", "o"),
                                         ("gather", g, "-", "s")):
            sub = frame[(frame.kernel == kernel) & (frame.type_size == dt)]
            if sub.empty:
                continue
            a = aggregate(sub, ["optimization", "n"])
            piv = a.pivot_table(index="n", columns="optimization",
                                values="GiB_s")
            if "widest" in piv and "scalar" in piv:
                ax.plot(piv.index, piv["widest"] / piv["scalar"], ls,
                        marker=marker, ms=4, lw=1.7, label=label)
        ax.axhline(1.0, color="k", ls=":", lw=1)
        ax.set_xscale("log", base=2)
        ax.set_xlabel("Working-set size n (elements)")
        ax.set_ylabel("widest / scalar")
        ax.set_title(f"{kernel.upper()} ({DT_LABEL.get(dt, dt)})")
        ax.legend(fontsize=8)
    save(fig, f"12_gather_simd_efficiency_{kernel}.png")

# ------------------------------------------------- 11 summary table
rows = []
if not ws.empty:
    for kernel in KERNELS:
        for dt in DTYPES:
            sub = ws[(ws.kernel == kernel) & (ws.type_size == dt)]
            if sub.empty:
                continue
            a = aggregate(sub, ["optimization", "n"])
            piv = a.pivot_table(index="n", columns="optimization",
                                values="GFLOP_s")
            if "scalar" not in piv or "widest" not in piv:
                continue
            fp = piv.index * dt
            regions = [("L1", fp < CACHE["L1"]), ("L2", (fp >= CACHE["L1"]) & (fp < CACHE["L2"])),
                       ("L3", (fp >= CACHE["L2"]) & (fp < CACHE["L3"])),
                       ("DRAM", fp >= CACHE["L3"])]
            for name, mask in regions:
                if not mask.any():
                    continue
                sp = (piv.loc[mask, "widest"] / piv.loc[mask, "scalar"]).median()
                rows.append({"kernel": kernel, "dtype": DT_LABEL.get(dt, dt),
                             "region": name,
                             "median_widest_speedup": round(float(sp), 3)})
summary = pd.DataFrame(rows)
summary.to_csv(os.path.join(DIR, "results/data/summary_speedup.csv"), index=False)

print(f"wrote plots to {OUT}")
print(f"measured streaming BW: {bw_meas:.2f} GiB/s")
if not summary.empty:
    print(summary.to_string(index=False))
