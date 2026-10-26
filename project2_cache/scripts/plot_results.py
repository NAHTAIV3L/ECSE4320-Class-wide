#!/usr/bin/env python3
"""Project 2 plots from results/data/summary.csv."""
import os
import re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(DIR, "results/data/summary.csv")
PERF = os.path.join(DIR, "results/data/perf_counters.csv")
OUT = os.path.join(DIR, "results/graphs")
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({"figure.dpi": 150, "font.size": 10, "axes.grid": True,
                     "grid.alpha": 0.35})
if not os.path.exists(DATA):
    raise SystemExit(f"missing {DATA}; run scripts/run_experiments.sh first")
df = pd.read_csv(DATA)

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


def save(fig, name):
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, name), bbox_inches="tight")
    plt.close(fig)


def cache_lines(ax):
    for lvl, size in CACHE.items():
        ax.axvline(size, color="gray", ls=":", lw=1)
        ax.text(size, ax.get_ylim()[1] * 0.9, lvl, ha="center", fontsize=8,
                color="gray")


# ------------------------------------------------- 01 zero-queue latency
lat = df[(df["test"] == "latency") & (df["exp_type"] == "latency_sweep") &
         (df["chains"] == 1) & (df["huge"] == 0) &
         (df["mode"] == "random")].sort_values("size_bytes")
if not lat.empty:
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.errorbar(lat["size_bytes"], lat["ns_per_access_mean"],
                yerr=lat["ns_per_access_sd"], marker="o", ms=4, lw=1.8,
                capsize=2, color="#1f77b4")
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xlabel("Working-set footprint (bytes)")
    ax.set_ylabel("Load-to-load latency (ns/access)")
    ax.set_title("Zero-queue latency: dependent random pointer chase")
    cache_lines(ax)
    save(fig, "01_latency_vs_working_set.png")

    fig, ax = plt.subplots(figsize=(8, 5))
    lv = lat.copy()
    lv["level"] = np.where(lv["size_bytes"] <= CACHE["L1"], "L1",
                           np.where(lv["size_bytes"] <= CACHE["L2"], "L2",
                                    np.where(lv["size_bytes"] <= CACHE["L3"],
                                             "L3", "DRAM")))
    means = lv.groupby("level", sort=False)["ns_per_access_mean"].mean()
    sds = lv.groupby("level", sort=False)["ns_per_access_mean"].std()
    order = [x for x in ("L1", "L2", "L3", "DRAM") if x in means.index]
    ax.bar(order, [means[x] for x in order],
           yerr=[0 if np.isnan(sds[x]) else sds[x] for x in order],
           capsize=4, color=["#4c78a8", "#f58518", "#54a24b", "#e45756"])
    ax.set_ylabel("Mean latency (ns/access)")
    ax.set_title("Latency by cache level")
    for i, x in enumerate(order):
        ax.text(i, means[x], f"{means[x]:.1f}", ha="center", va="bottom",
                fontsize=9)
    save(fig, "02_latency_by_level.png")

# ------------------------------------------------- 03 bandwidth vs working set
bw = df[(df["test"] == "bandwidth") & (df["exp_type"] == "bw_sweep")].copy()
if not bw.empty:
    fig, ax = plt.subplots(figsize=(8, 5))
    for rw in ["read", "write", "copy", "mix70", "mix50"]:
        s = bw[bw["rw"] == rw].sort_values("size_bytes")
        if s.empty:
            continue
        ax.errorbar(s["size_bytes"], s["GiB_s_mean"], yerr=s["GiB_s_sd"],
                    marker="o", ms=3, lw=1.6, capsize=2, label=rw)
    ax.set_xscale("log", base=2)
    ax.set_xlabel("Working-set footprint (bytes per stream)")
    ax.set_ylabel("Payload bandwidth (GiB/s)")
    ax.set_title("Bandwidth vs working set (sequential, stride=8 B)")
    ax.legend()
    cache_lines(ax)
    save(fig, "03_bandwidth_vs_working_set_all.png")
    for rw in ["read", "write", "copy", "mix70", "mix50"]:
        s = bw[bw["rw"] == rw].sort_values("size_bytes")
        if s.empty:
            continue
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.errorbar(s["size_bytes"], s["GiB_s_mean"], yerr=s["GiB_s_sd"],
                    marker="o", ms=4, lw=1.8, capsize=2, label="payload")
        ax.plot(s["size_bytes"], s["GiB_s_traffic_mean"], marker="s", ms=3,
                lw=1.4, ls="--", label="estimated line traffic")
        ax.set_xscale("log", base=2)
        ax.set_xlabel("Working-set footprint (bytes)")
        ax.set_ylabel("GiB/s")
        ax.set_title(f"Bandwidth vs working set: {rw}")
        ax.legend()
        cache_lines(ax)
        save(fig, f"03_bandwidth_vs_working_set_{rw}.png")

# ------------------------------------------------- 04 stride / granularity
for exp_type, fname in (("stride_sweep", "04_stride_granularity"),
                        ("stride_random", "05_stride_random")):
    st = df[df["exp_type"] == exp_type].copy()
    if st.empty:
        continue
    sizes = sorted(st["size_bytes"].unique())
    fig, axes = plt.subplots(1, len(sizes), figsize=(6 * len(sizes), 4.6),
                             squeeze=False)
    for ax, size in zip(axes[0], sizes):
        s0 = st[st["size_bytes"] == size]
        for mode, marker in (("seq", "o"), ("random", "s")):
            s = s0[s0["mode"] == mode].sort_values("stride_bytes")
            if s.empty:
                continue
            ax.plot(s["stride_bytes"], s["GiB_s_mean"], marker=marker, ms=4,
                    lw=1.7, label=f"{mode} payload")
            ax.plot(s["stride_bytes"], s["GiB_s_traffic_mean"], marker=marker,
                    ms=3, lw=1.2, ls="--", label=f"{mode} line traffic")
        ax.set_xscale("log", base=2)
        ax.set_xlabel("Stride (bytes)")
        ax.set_ylabel("GiB/s")
        ax.set_title(f"footprint={size} B")
        ax.legend(fontsize=7)
    save(fig, fname + ".png")

# ------------------------------------------------- 06 read/write mix
mix = df[df["exp_type"] == "rw_mix"].copy()
if not mix.empty:
    sizes = sorted(mix["size_bytes"].unique())
    fig, axes = plt.subplots(1, len(sizes), figsize=(6 * len(sizes), 4.4),
                             squeeze=False)
    for ax, size in zip(axes[0], sizes):
        s = mix[mix["size_bytes"] == size]
        order = ["read", "write", "mix70", "mix50", "copy"]
        vals = [s[s["rw"] == x]["GiB_s_mean"].mean() for x in order]
        errs = [s[s["rw"] == x]["GiB_s_sd"].mean() for x in order]
        ax.bar(order, vals, yerr=errs, capsize=3,
               color=["#4c78a8", "#e45756", "#f58518", "#54a24b", "#72b7b2"])
        ax.set_ylabel("GiB/s")
        ax.set_title(f"footprint={size} B")
        ax.tick_params(axis="x", rotation=30)
    fig.suptitle("Read/write mix at fixed block size and pattern")
    save(fig, "06_read_write_mix.png")

# ------------------------------------------------- 07 intensity / Little's law
mlp = df[df["exp_type"] == "mlp"].copy()
if not mlp.empty:
    for size in sorted(mlp["size_bytes"].unique()):
        s = mlp[mlp["size_bytes"] == size].sort_values("chains")
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.7))
        axes[0].plot(s["ns_per_step_mean"], s["GiB_s_mean"], marker="o", ms=5,
                     lw=1.8, color="#d62728")
        for _, row in s.iterrows():
            axes[0].annotate(f"K={int(row['chains'])}",
                             (row["ns_per_step_mean"], row["GiB_s_mean"]),
                             textcoords="offset points", xytext=(5, 5),
                             fontsize=8)
        axes[0].set_xscale("log")
        axes[0].set_yscale("log")
        axes[0].set_xlabel("Per-chain step latency (ns)")
        axes[0].set_ylabel("Throughput (GiB/s of 64 B lines)")
        axes[0].set_title(f"Throughput-latency curve, footprint={size} B")
        axes[1].plot(s["chains"], s["GiB_s_mean"], marker="o", ms=5, lw=1.8,
                     color="#1f77b4", label="measured")
        base = s[s["chains"] == 1]
        if not base.empty:
            lat1 = float(base["ns_per_step_mean"].iloc[0])
            ideal = (s["chains"] * 64.0 / lat1) / 1e9
            axes[1].plot(s["chains"], ideal, "k--", lw=1.5,
                         label="Little's law (K/latency at K=1)")
        axes[1].set_xscale("log", base=2)
        axes[1].set_yscale("log")
        axes[1].set_xlabel("Independent chains K")
        axes[1].set_ylabel("Throughput (GiB/s)")
        axes[1].set_title("Concurrency vs throughput")
        axes[1].legend(fontsize=8)
        save(fig, f"07_intensity_little_law_{size}B.png")

# ------------------------------------------------- 08 cache/TLB counters
def event_base(name):
    """Normalise perf names such as cpu_core/cache-misses/u -> cache-misses."""
    name = str(name).strip()
    m = re.match(r"^(?:cpu_core|cpu_atom)/(.+?)(?:/[uk])?$", name)
    if m:
        return m.group(1)
    return re.sub(r"/[uk]$", "", name)


if os.path.exists(PERF) and os.path.getsize(PERF) > 0:
    try:
        pf = pd.read_csv(PERF)
        pf["event"] = pf["event"].map(event_base)
        for exp_type in sorted(pf["exp_type"].unique()):
            s0 = pf[pf["exp_type"] == exp_type].copy()
            if s0.empty:
                continue
            piv = s0.pivot_table(index="size_bytes", columns="event",
                                 values="value", aggfunc="mean").sort_index()
            fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
            if "LLC-loads" in piv and "LLC-load-misses" in piv:
                r = piv["LLC-load-misses"] / piv["LLC-loads"].replace(0, np.nan)
                axes[0].plot(r.index, r, marker="s", ms=4, lw=1.6,
                             label="LLC load miss rate")
            if "dTLB-loads" in piv and "dTLB-load-misses" in piv:
                r = piv["dTLB-load-misses"] / piv["dTLB-loads"].replace(0, np.nan)
                axes[0].plot(r.index, r, marker="^", ms=4, lw=1.6,
                             label="dTLB load miss rate")
            if "cache-references" in piv and "cache-misses" in piv:
                r = piv["cache-misses"] / piv["cache-references"].replace(0, np.nan)
                axes[0].plot(r.index, r, marker="o", ms=4, lw=1.6,
                             label="cache miss rate")
            axes[0].set_xscale("log", base=2)
            axes[0].set_yscale("log")
            axes[0].set_xlabel("Working-set footprint (bytes)")
            axes[0].set_ylabel("Miss rate (per access or per load)")
            axes[0].set_title(f"perf miss rates: {exp_type}")
            axes[0].legend(fontsize=8)
            cache_lines(axes[0])

            lat = (s0.groupby("size_bytes")["ns_per_access"].mean()
                   .sort_index())
            axes[1].plot(lat.index, lat, marker="o", ms=5, lw=1.8,
                         color="#d62728")
            axes[1].set_xscale("log", base=2)
            axes[1].set_xlabel("Working-set footprint (bytes)")
            axes[1].set_ylabel("ns/access")
            axes[1].set_title("Runtime vs footprint (same runs)")
            cache_lines(axes[1])
            save(fig, f"08_perf_counters_{exp_type}.png")
    except Exception as e:
        print("perf plotting skipped:", e)

# ------------------------------------------------- 09 TLB / huge pages
tl = df[df["exp_type"] == "huge_pages"].copy()
if not tl.empty:
    fig, ax = plt.subplots(figsize=(8, 5))
    for stride, ls in ((64, "-"), (4096, "--")):
        for huge, marker in ((0, "o"), (1, "s")):
            s = tl[(tl["stride_bytes"] == stride) & (tl["huge"] == huge)]
            if s.empty:
                continue
            s = s.sort_values("size_bytes")
            ax.errorbar(s["size_bytes"], s["ns_per_access_mean"],
                        yerr=s["ns_per_access_sd"], marker=marker, ls=ls,
                        ms=4, lw=1.6, capsize=2,
                        label=f"stride={stride} huge={huge}")
    ax.set_xscale("log", base=2)
    ax.set_xlabel("Working-set footprint (bytes)")
    ax.set_ylabel("ns/access")
    ax.set_title("Page locality: normal pages vs MADV_HUGEPAGE")
    ax.legend(fontsize=8)
    save(fig, "09_tlb_huge_pages.png")

# ------------------------------------------------- 10 pitfalls
pit = df[df["exp_type"].isin(["pitfall_cache_reuse", "pitfall_prefetch"])]
if not pit.empty:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    cr = pit[pit["exp_type"] == "pitfall_cache_reuse"].sort_values("size_bytes")
    axes[0].bar([f"{int(x/1024/1024)} MiB" for x in cr["size_bytes"]],
                cr["GiB_s_mean"], yerr=cr["GiB_s_sd"], capsize=4,
                color=["#54a24b", "#e45756"])
    axes[0].set_ylabel("Reported GiB/s")
    axes[0].set_title("Pitfall: L3-resident vs DRAM 'bandwidth'")
    pr = pit[pit["exp_type"] == "pitfall_prefetch"].sort_values("size_bytes")
    for mode, marker in (("seq", "o"), ("random", "s")):
        s = pr[pr["mode"] == mode]
        if not s.empty:
            axes[1].bar(mode, s["ns_per_access_mean"].mean(),
                        yerr=s["ns_per_access_sd"].mean(), capsize=4,
                        color="#4c78a8" if mode == "seq" else "#f58518")
    axes[1].set_ylabel("ns/access")
    axes[1].set_title("Pitfall: prefetcher hides DRAM latency (seq chase)")
    save(fig, "10_pitfalls.png")

print(f"wrote plots to {OUT}")
