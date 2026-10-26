#!/usr/bin/env python3
"""Aggregate raw membench trials into summary tables."""
import os
import numpy as np
import pandas as pd

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(DIR, "results/data/raw_measurements.csv")
OUT = os.path.join(DIR, "results/data/summary.csv")
CACHE_CSV = os.path.join(DIR, "results/env/cache_sizes.csv")

if not os.path.exists(RAW):
    raise SystemExit(f"missing {RAW}; run scripts/run_experiments.sh first")

df = pd.read_csv(RAW)
keys = ["test", "size_bytes", "stride_bytes", "mode", "rw", "chains",
        "huge", "exp_type"]
agg = (df.groupby(keys, dropna=False)
         .agg(trials=("trial", "count"),
              time_mean_s=("time_s", "mean"),
              time_sd_s=("time_s", "std"),
              time_min_s=("time_s", "min"),
              ns_per_step_mean=("ns_per_step", "mean"),
              ns_per_step_sd=("ns_per_step", "std"),
              ns_per_access_mean=("ns_per_access", "mean"),
              ns_per_access_sd=("ns_per_access", "std"),
              GiB_s_mean=("GiB_s", "mean"),
              GiB_s_sd=("GiB_s", "std"),
              GiB_s_traffic_mean=("GiB_s_traffic", "mean"),
              GiB_s_traffic_sd=("GiB_s_traffic", "std"))
         .reset_index())
agg.to_csv(OUT, index=False)

# Zero-queue latency table with cache-level labels.
cache = {"L1": 48 * 1024, "L2": 2048 * 1024, "L3": 24576 * 1024}
if os.path.exists(CACHE_CSV):
    try:
        cdf = pd.read_csv(CACHE_CSV)
        for lvl in ("L1", "L2", "L3"):
            row = cdf[(cdf["level"].astype(str) == lvl[1]) &
                      (cdf["type"].str.lower().str.startswith("data") |
                       cdf["type"].str.lower().str.startswith("unified"))]
            if not row.empty:
                cache[lvl] = int(row["size_bytes"].iloc[0])
    except Exception:
        pass


def level(size):
    if size <= cache["L1"]:
        return "L1"
    if size <= cache["L2"]:
        return "L2"
    if size <= cache["L3"]:
        return "L3"
    return "DRAM"


lat = agg[(agg["test"] == "latency") & (agg["chains"] == 1) &
          (agg["huge"] == 0) & (agg["mode"] == "random") &
          (agg["exp_type"] == "latency_sweep")].copy()
if not lat.empty:
    lat["level"] = lat["size_bytes"].map(level)
    lat["cache_bytes"] = lat["level"].map(lambda x: cache.get(x, 0))
    lat = lat.sort_values("size_bytes")
    cols = ["level", "size_bytes", "cache_bytes", "trials", "ns_per_access_mean",
            "ns_per_access_sd", "GiB_s_mean"]
    lat[cols].to_csv(os.path.join(DIR, "results/data/latency_table.csv"),
                     index=False)
    print("\nZero-queue latency (random pointer chase, chains=1, huge=0):")
    print(lat[cols].to_string(index=False))

# Little's-law table for the MLP sweep.
mlp = agg[agg["exp_type"] == "mlp"].copy()
if not mlp.empty:
    mlp["lines_per_s"] = mlp["GiB_s_mean"] * (1e9 / 64.0)
    cols = ["size_bytes", "chains", "ns_per_step_mean", "ns_per_access_mean",
            "GiB_s_mean", "lines_per_s"]
    mlp[cols].sort_values(["size_bytes", "chains"]).to_csv(
        os.path.join(DIR, "results/data/mlp_table.csv"), index=False)

print(f"\nwrote {OUT} ({len(agg)} configurations)")
