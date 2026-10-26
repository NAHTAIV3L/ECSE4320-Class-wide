#!/usr/bin/env python3
"""Project 3 plots from results/data/fio_summary.csv."""
import os
import re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("FIO_DATA", os.path.join(DIR, "results/data/fio_summary.csv"))
OUT = os.environ.get("FIO_GRAPH_DIR", os.path.join(DIR, "results/graphs"))
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({"figure.dpi": 150, "font.size": 10, "axes.grid": True,
                     "grid.alpha": 0.35})
if not os.path.exists(DATA):
    raise SystemExit(f"missing {DATA}; run scripts/parse_fio.py first")
df = pd.read_csv(DATA)
for c in ("iodepth", "numjobs", "total_iops", "total_bw_MiB",
          "read_clat_mean_us", "write_clat_mean_us"):
    df[c] = pd.to_numeric(df[c], errors="coerce")


def save(fig, name):
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, name), bbox_inches="tight")
    plt.close(fig)


def size_bytes(s):
    if not isinstance(s, str):
        return np.nan
    m = re.match(r"(\d+(?:\.\d+)?)([kKmMgG]?)", s.strip())
    if not m:
        return np.nan
    v = float(m.group(1))
    return v * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[m.group(2).lower()]


def rw_prefix(rw):
    rw = str(rw)
    return "write" if rw in ("write", "randwrite") or rw.startswith("randw") else "read"


def pick(row, suffix):
    return row[f"{rw_prefix(row['rw'])}_{suffix}"]


def lat_col(row):
    if row["rw"] in ("randread", "read") or str(row["rw"]).startswith("randr"):
        return row["read_clat_mean_us"]
    if row["rw"] in ("randwrite", "write") or str(row["rw"]).startswith("randw"):
        return row["write_clat_mean_us"]
    return max(row["read_clat_mean_us"], row["write_clat_mean_us"])


df["lat_us"] = df.apply(lat_col, axis=1)

# ------------------------------------------------- 01 QD=1 baselines
qd1 = df[df["exp_type"] == "qd1_baseline"]
if not qd1.empty:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    x = np.arange(len(qd1))
    axes[0].bar(x, qd1["total_iops"], color="#4c78a8")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(qd1["jobname"], rotation=25, ha="right")
    axes[0].set_ylabel("IOPS")
    axes[0].set_title("QD=1 baseline IOPS")
    for i, v in enumerate(qd1["total_iops"]):
        axes[0].text(i, v, f"{v:.0f}", ha="center", va="bottom", fontsize=8)
    w = 0.26
    for j, (suffix, lab) in enumerate((("clat_mean_us", "mean"),
                                      ("p99_us", "p99"),
                                      ("p99_9_us", "p99.9"))):
        vals = qd1.apply(lambda r: pick(r, suffix), axis=1).fillna(0).values
        axes[1].bar(x + (j - 1) * w, vals, width=w, label=lab)
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(qd1["jobname"], rotation=25, ha="right")
    axes[1].set_ylabel("Completion latency (us, active direction)")
    axes[1].set_title("QD=1 latency and tail")
    axes[1].legend(fontsize=8)
    save(fig, "01_qd1_baselines.png")

# ------------------------------------------------- 02 block-size sweep
bs = df[df["exp_type"] == "bs_sweep"].copy()
if not bs.empty:
    bs["bs_bytes"] = bs["bs"].map(size_bytes)
    for pattern, title in (("rand", "random"), ("seq", "sequential")):
        sub = bs[bs["rw"].str.startswith("rand") == (pattern == "rand")]
        if sub.empty:
            continue
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
        for rw in sorted(sub["rw"].unique()):
            for qd in sorted(sub["iodepth"].dropna().unique()):
                s = sub[(sub["rw"] == rw) & (sub["iodepth"] == qd)]
                if s.empty:
                    continue
                s = s.sort_values("bs_bytes")
                axes[0].plot(s["bs_bytes"], s["total_bw_MiB"], marker="o",
                             ms=3, lw=1.5, label=f"{rw} qd={int(qd)}")
                axes[1].plot(s["bs_bytes"], s["lat_us"], marker="o", ms=3,
                             lw=1.5, label=f"{rw} qd={int(qd)}")
        for ax, ylabel in zip(axes, ("Throughput (MiB/s)", "Mean latency (us)")):
            ax.set_xscale("log", base=2)
            ax.set_xlabel("Block size (bytes)")
            ax.set_ylabel(ylabel)
            ax.legend(fontsize=7, ncol=2)
        axes[0].set_title(f"{title.capitalize()} block-size sweep")
        save(fig, f"02_block_size_{pattern}.png")

# ------------------------------------------------- 03 read/write mix
mix = df[df["exp_type"] == "rw_mix"].copy()
if not mix.empty:
    mix["mix_read"] = mix["jobname"].str.extract(r"mix(\d+)").astype(float)
    mix["qd"] = mix["jobname"].str.extract(r"qd(\d+)").astype(float)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    for qd in sorted(mix["qd"].dropna().unique()):
        s = mix[mix["qd"] == qd].sort_values("mix_read")
        axes[0].plot(s["mix_read"], s["total_bw_MiB"], marker="o", ms=4,
                     lw=1.6, label=f"qd={int(qd)}")
        axes[1].plot(s["mix_read"], s["lat_us"], marker="o", ms=4, lw=1.6,
                     label=f"qd={int(qd)}")
    axes[0].set_xlabel("Read share (%)")
    axes[0].set_ylabel("Throughput (MiB/s)")
    axes[0].set_title("Read/write mix: throughput")
    axes[1].set_xlabel("Read share (%)")
    axes[1].set_ylabel("Mean latency (us)")
    axes[1].set_title("Read/write mix: latency")
    for ax in axes:
        ax.legend(fontsize=8)
    save(fig, "03_read_write_mix.png")

# ------------------------------------------------- 04 QD / parallelism sweep
qd = df[df["exp_type"] == "qd_sweep"].copy()
if not qd.empty:
    for rw in sorted(qd["rw"].unique()):
        s = qd[qd["rw"] == rw].sort_values("iodepth")
        if s.empty:
            continue
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
        axes[0].plot(s["iodepth"], s["total_iops"], marker="o", ms=4, lw=1.7)
        axes[0].set_xscale("log", base=2)
        axes[0].set_xlabel("Queue depth")
        axes[0].set_ylabel("IOPS")
        axes[0].set_title(f"{rw}: IOPS vs QD")
        axes[1].plot(s["iodepth"], s["lat_us"], marker="o", ms=4, lw=1.7,
                     color="#d62728")
        axes[1].set_xscale("log", base=2)
        axes[1].set_xlabel("Queue depth")
        axes[1].set_ylabel("Mean latency (us)")
        axes[1].set_title(f"{rw}: latency vs QD")
        axes[2].plot(s["lat_us"], s["total_iops"], marker="o", ms=4, lw=1.7,
                     color="#54a24b")
        for _, row in s.iterrows():
            axes[2].annotate(f"QD={int(row['iodepth'])}",
                             (row["lat_us"], row["total_iops"]),
                             textcoords="offset points", xytext=(4, 4),
                             fontsize=7)
        # Knee: first QD at >=90% of peak IOPS.
        peak = s["total_iops"].max()
        knee = s[s["total_iops"] >= 0.9 * peak].iloc[0]
        axes[2].axvline(knee["lat_us"], color="k", ls=":", lw=1)
        axes[2].set_xscale("log")
        axes[2].set_yscale("log")
        axes[2].set_xlabel("Mean latency (us)")
        axes[2].set_ylabel("IOPS")
        axes[2].set_title("Throughput-latency (knee at 90% peak)")
        save(fig, f"04_qd_sweep_{rw}.png")

    # Little's law cross-check for randread.
    s = qd[qd["rw"] == "randread"].sort_values("iodepth")
    if not s.empty:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(s["iodepth"], s["total_iops"], marker="o", ms=4, lw=1.8,
                label="measured")
        pred = s["iodepth"] / (s["lat_us"] * 1e-6)
        ax.plot(s["iodepth"], pred, "k--", lw=1.5,
                label="Little: QD / latency")
        ax.set_xscale("log", base=2)
        ax.set_yscale("log")
        ax.set_xlabel("Queue depth")
        ax.set_ylabel("IOPS")
        ax.set_title("Little's Law check (randread 4 KiB)")
        ax.legend()
        save(fig, "05_little_law_qd.png")

    # Tail latency at QD=1, mid (32), near-knee (128 or peak QD).
    sel = qd[qd["rw"] == "randread"].copy()
    if not sel.empty:
        want = [1, 32, int(sel["iodepth"].max())]
        want = sorted(set([w for w in want if w in set(sel["iodepth"])]))
        fig, ax = plt.subplots(figsize=(8, 5))
        labels = []
        vals = []
        for q in want:
            row = sel[sel["iodepth"] == q].iloc[0]
            labels.append(f"QD={q}")
            vals.append([row["read_clat_mean_us"], row["read_p50_us"],
                         row["read_p95_us"], row["read_p99_us"],
                         row["read_p99_9_us"]])
        vals = np.array(vals)
        x = np.arange(len(labels))
        for j, lab in enumerate(("mean", "p50", "p95", "p99", "p99.9")):
            ax.bar(x + (j - 2) * 0.16, vals[:, j], width=0.16, label=lab)
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_yscale("log")
        ax.set_ylabel("Latency (us)")
        ax.set_title("Tail latency at QD=1, mid, and near-knee")
        ax.legend(fontsize=8)
        save(fig, "06_tail_latency.png")

# ------------------------------------------------- 06b numjobs parallelism
par = df[df["exp_type"] == "parallel_sweep"].copy()
if not par.empty:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for rw in sorted(par["rw"].unique()):
        s = par[par["rw"] == rw].sort_values("numjobs")
        axes[0].plot(s["numjobs"], s["total_iops"], marker="o", ms=4, lw=1.6,
                     label=rw)
        axes[1].plot(s["numjobs"], s["lat_us"], marker="o", ms=4, lw=1.6,
                     label=rw)
    for ax, ylabel in zip(axes, ("IOPS", "Mean latency (us)")):
        ax.set_xlabel("numjobs (iodepth fixed at 8)")
        ax.set_ylabel(ylabel)
        ax.legend(fontsize=8)
    axes[0].set_title("Parallel jobs at fixed per-job queue depth")
    save(fig, "10_parallelism.png")

# ------------------------------------------------- 07 LBA range / working set
lba = df[df["exp_type"] == "lba_range"].copy()
if not lba.empty:
    lba["size_bytes"] = lba["size"].map(size_bytes)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for qd in sorted(lba["iodepth"].dropna().unique()):
        s = lba[lba["iodepth"] == qd].sort_values("size_bytes")
        axes[0].plot(s["size_bytes"], s["total_iops"], marker="o", ms=4,
                     lw=1.6, label=f"qd={int(qd)}")
        axes[1].plot(s["size_bytes"], s["lat_us"], marker="o", ms=4, lw=1.6,
                     label=f"qd={int(qd)}")
    for ax, ylabel in zip(axes, ("IOPS", "Mean latency (us)")):
        ax.set_xscale("log", base=2)
        ax.set_xlabel("Active LBA range (bytes)")
        ax.set_ylabel(ylabel)
        ax.legend(fontsize=8)
    axes[0].set_title("Working-set / LBA-range effect")
    save(fig, "07_lba_range.png")

# ------------------------------------------------- 08 device state
state = df[df["exp_type"] == "device_state"]
if not state.empty:
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    ax.bar(state["jobname"], state["total_bw_MiB"], color="#f58518")
    ax.set_ylabel("Throughput (MiB/s)")
    ax.set_title("Burst vs steady-state (two back-to-back identical runs)")
    save(fig, "08_device_state.png")

# ------------------------------------------------- 09 pitfalls
pit = df[df["exp_type"].isin(["pitfall_buffered", "pitfall_effective_qd"])]
if not pit.empty:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
    b = pit[pit["exp_type"] == "pitfall_buffered"]
    axes[0].bar(b["jobname"], b["total_iops"],
                color=["#e45756", "#54a24b"][:len(b)])
    for i, (_, row) in enumerate(b.iterrows()):
        axes[0].text(i, row["total_iops"], f"{row['lat_us']:.1f} us",
                     ha="center", va="bottom", fontsize=8)
    axes[0].set_ylabel("IOPS")
    axes[0].set_title("Pitfall: buffered I/O (direct=0) vs O_DIRECT\n(label = mean latency)")
    axes[0].tick_params(axis="x", rotation=20)
    e = pit[pit["exp_type"] == "pitfall_effective_qd"]
    axes[1].bar(e["jobname"], e["total_iops"], color="#4c78a8")
    for i, (_, row) in enumerate(e.iterrows()):
        axes[1].text(i, row["total_iops"], f"{row['lat_us']:.0f} us",
                     ha="center", va="bottom", fontsize=8)
    axes[1].set_ylabel("IOPS")
    axes[1].set_title("Pitfall: iodepth=8,nj=1 vs iodepth=1,nj=8")
    axes[1].tick_params(axis="x", rotation=20)
    save(fig, "09_pitfalls.png")

print(f"wrote plots to {OUT}")
