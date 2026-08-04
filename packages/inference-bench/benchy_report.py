#!/usr/bin/env python3
"""Summarise llama-benchy JSON results into comparison tables.

llama-benchy writes one JSON per invocation with a `benchmarks` list of runs.
Reading a directory of those by eye does not surface the thing you actually want
— how a number MOVES across depth, concurrency, or a config change (MTP on/off).
This groups runs on the axis that varies and prints the deltas.

Usage:
    benchy_report.py <results-dir-or-files>...
    benchy_report.py /data/bench/llama-benchy

Metric notes (these are easy to misread):
  * pp_throughput / tg_throughput are AGGREGATE across concurrent requests.
    *_req_throughput are PER REQUEST. At concurrency 4 the aggregate can rise
    while per-request falls; both matter and they answer different questions.
  * e2e_ttft includes queueing, so at concurrency above the server's slot count
    it measures the queue, not the server.
"""
import json
import sys
from pathlib import Path


def m(metric, key="mean"):
    """Pull a value out of a BenchmarkMetric, tolerating nulls."""
    if not metric:
        return None
    return metric.get(key)


def fmt(v, nd=1):
    return "-" if v is None else f"{v:,.{nd}f}"


def load(paths):
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.glob("*.json")))
        elif p.exists():
            files.append(p)
    out = []
    for f in files:
        try:
            d = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"  !! unreadable: {f.name}: {e}", file=sys.stderr)
            continue
        # Suite and target are encoded in the filename by llama-benchy-suite:
        # <stamp>_<suite>_<target>.json
        parts = f.stem.split("_")
        suite = parts[1] if len(parts) > 2 else "?"
        target = parts[2] if len(parts) > 2 else "?"
        out.append((f, suite, target, d))
    return out


def table(rows, headers):
    widths = [max(len(str(r[i])) for r in ([headers] + rows)) for i in range(len(headers))]
    line = "  " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  " + "-+-".join("-" * w for w in widths))
    for r in rows:
        print("  " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)))


def report_one(path, suite, target, d):
    runs = [r for r in d.get("benchmarks", []) if not r.get("is_context_prefill_phase")]
    if not runs:
        return
    print()
    print("=" * 78)
    print(f"  {suite}  [{target}]   model={d.get('model')}   "
          f"prefix_caching={d.get('prefix_caching_enabled')}")
    print(f"  {path.name}")
    print("=" * 78)

    depths = {r.get("context_size") for r in runs}
    concs = {r.get("concurrency") for r in runs}

    # Choose the axis that actually varies so the table reads as a trend.
    if len(depths) > 1:
        axis, label = "context_size", "depth"
    elif len(concs) > 1:
        axis, label = "concurrency", "conc"
    else:
        axis, label = "concurrency", "conc"

    rows = []
    base_tg = base_pp = None
    for r in sorted(runs, key=lambda x: (x.get(axis) or 0)):
        tg = m(r.get("tg_throughput"))
        tgr = m(r.get("tg_req_throughput"))
        pp = m(r.get("pp_throughput"))
        ttft = m(r.get("e2e_ttft"))
        tg_sd = m(r.get("tg_throughput"), "std")
        if base_tg is None:
            base_tg, base_pp = tg, pp
        d_tg = f"{(tg / base_tg - 1) * 100:+.0f}%" if (tg and base_tg) else "-"
        d_pp = f"{(pp / base_pp - 1) * 100:+.0f}%" if (pp and base_pp) else "-"
        rows.append([
            f"{r.get(axis)}",
            f"{r.get('prompt_size')}",
            f"{r.get('response_size')}",
            fmt(pp), d_pp,
            fmt(tg), f"±{fmt(tg_sd)}", d_tg,
            fmt(tgr),
            fmt(ttft, 0),
        ])
    table(rows, [label, "pp", "tg", "prefill t/s", "vs1st",
                 "gen t/s agg", "sd", "vs1st", "gen t/s/req", "ttft ms"])


def compare_mtp(loaded):
    """MTP on vs off, matched on (concurrency, depth). The whole point of the A/B."""
    on = {}
    off = {}
    for path, suite, target, d in loaded:
        if suite not in ("mtp-on", "mtp-off"):
            continue
        bucket = on if suite == "mtp-on" else off
        for r in d.get("benchmarks", []):
            if r.get("is_context_prefill_phase"):
                continue
            bucket[(r.get("concurrency"), r.get("context_size"))] = r
    keys = sorted(set(on) & set(off))
    if not keys:
        return
    print()
    print("=" * 78)
    print("  MTP A/B — same model, same ctx/slots, only --spec-type draft-mtp differs")
    print("=" * 78)
    rows = []
    for k in keys:
        a, b = on[k], off[k]
        ta, tb = m(a.get("tg_throughput")), m(b.get("tg_throughput"))
        pa, pb = m(a.get("pp_throughput")), m(b.get("pp_throughput"))
        fa, fb = m(a.get("e2e_ttft")), m(b.get("e2e_ttft"))
        delta = f"{(ta / tb - 1) * 100:+.1f}%" if (ta and tb) else "-"
        fdelta = f"{(fa / fb - 1) * 100:+.1f}%" if (fa and fb) else "-"
        rows.append([f"{k[0]}", f"{k[1]}", fmt(ta), fmt(tb), delta,
                     fmt(pa), fmt(pb), fmt(fa, 0), fmt(fb, 0), fdelta])
    table(rows, ["conc", "depth", "gen ON", "gen OFF", "MTP effect",
                 "pp ON", "pp OFF", "ttft ON", "ttft OFF", "ttft delta"])
    print()
    print("  'MTP effect' > 0 means MTP is FASTER at that concurrency.")


def main():
    args = sys.argv[1:] or ["/data/bench/llama-benchy"]
    loaded = load(args)
    if not loaded:
        print("no result JSON found", file=sys.stderr)
        return 1
    for path, suite, target, d in loaded:
        report_one(path, suite, target, d)
    compare_mtp(loaded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
