#!/usr/bin/env python3
"""Parse fio JSON outputs into processed CSV tables."""
import glob
import json
import os
import pandas as pd

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.environ.get("FIO_RAW_DIR", os.path.join(DIR, "results/raw"))
OUT = os.environ.get("FIO_DATA_OUT", os.path.join(DIR, "results/data/fio_summary.csv"))
TAIL = os.environ.get("FIO_TAIL_OUT", os.path.join(DIR, "results/data/tail_latency.csv"))

files = sorted(glob.glob(os.path.join(RAW, "*.json")))
if not files:
    raise SystemExit(f"no fio JSON files in {RAW}; run scripts/run_experiments.sh first")

TARGETS = (50.0, 95.0, 99.0, 99.9)
TAGS = {50.0: "50", 95.0: "95", 99.0: "99", 99.9: "99_9"}
TYPES = ("pitfall_effective_qd", "pitfall_buffered", "qd1_baseline",
         "parallel_sweep", "device_state", "bs_sweep", "rw_mix",
         "lba_range", "qd_sweep")


def exp_type(text):
    for t in TYPES:
        if text == t or text.startswith(t + "_") or f"_{t}_" in text:
            return t
    return ""


def pct(section, target):
    if not isinstance(section, dict):
        return None
    p = section.get("percentile", {})
    for k, v in p.items():
        try:
            if abs(float(k) - target) < 0.01:
                return float(v)
        except (TypeError, ValueError):
            pass
    return None


def bw_bytes(section):
    if not isinstance(section, dict):
        return 0.0
    if "bw_bytes" in section:
        return float(section["bw_bytes"])
    if "bw" in section:
        return float(section["bw"]) * 1024.0
    return 0.0


rows = []
for path in files:
    if os.path.basename(path) == "precondition.json":
        continue
    try:
        with open(path) as f:
            doc = json.load(f)
    except Exception as e:
        print(f"skip {path}: {e}")
        continue
    doc_desc = doc.get("desc", "") or ""
    for job in doc.get("jobs", []):
        opt = job.get("job options", {})
        rd = job.get("read", {}) or {}
        wr = job.get("write", {}) or {}
        r_iops = float(rd.get("iops", 0.0) or 0.0)
        w_iops = float(wr.get("iops", 0.0) or 0.0)
        r_bw = bw_bytes(rd) / (1024.0 * 1024.0)
        w_bw = bw_bytes(wr) / (1024.0 * 1024.0)
        r_cl = rd.get("clat_ns", {}) or {}
        w_cl = wr.get("clat_ns", {}) or {}
        name = os.path.basename(path)
        et = (job.get("desc", "") or job.get("description", "")
              or opt.get("description", "") or doc_desc or exp_type(name))
        rt = rd.get("runtime") or wr.get("runtime") or None
        row = {
            "file": name,
            "exp_type": et,
            "jobname": job.get("jobname", ""),
            "rw": opt.get("rw", ""),
            "bs": opt.get("bs", ""),
            "iodepth": opt.get("iodepth", ""),
            "numjobs": opt.get("numjobs", ""),
            "size": opt.get("size", ""),
            "direct": opt.get("direct", ""),
            "runtime_s": (float(rt) / 1000.0) if rt is not None else None,
            "read_iops": r_iops,
            "write_iops": w_iops,
            "total_iops": r_iops + w_iops,
            "read_bw_MiB": r_bw,
            "write_bw_MiB": w_bw,
            "total_bw_MiB": r_bw + w_bw,
            "read_clat_mean_us": (r_cl.get("mean", 0.0) or 0.0) / 1000.0,
            "write_clat_mean_us": (w_cl.get("mean", 0.0) or 0.0) / 1000.0,
            "errors": job.get("error", 0),
        }
        for target in TARGETS:
            tag = TAGS[target]
            row[f"read_p{tag}_us"] = (pct(r_cl, target) or 0.0) / 1000.0
            row[f"write_p{tag}_us"] = (pct(w_cl, target) or 0.0) / 1000.0
        rows.append(row)

if not rows:
    raise SystemExit("no jobs found in fio JSON")

df = pd.DataFrame(rows).sort_values(["exp_type", "file"])
os.makedirs(os.path.dirname(OUT), exist_ok=True)
df.to_csv(OUT, index=False)

# Tail-latency table for the QD=1 and QD-sweep runs.
cols = ["exp_type", "jobname", "rw", "bs", "iodepth", "numjobs",
        "total_iops", "total_bw_MiB", "read_clat_mean_us", "write_clat_mean_us"]
for target in TARGETS:
    tag = TAGS[target]
    cols += [f"read_p{tag}_us", f"write_p{tag}_us"]
tail = df[df["exp_type"].isin(["qd1_baseline", "qd_sweep", "rw_mix",
                               "device_state"])]
tail[cols].to_csv(TAIL, index=False)
print(f"wrote {OUT} ({len(df)} jobs)")
print(f"wrote {TAIL}")
