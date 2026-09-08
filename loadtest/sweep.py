"""
Scheduler saturation sweep for idt4gdc_dc1.

Submits a series of simulations directly to the ExaDigiT simulation-server at increasing offered job-arrival rates, and
reports queue depth / wait time / utilization at each level so you can see where the scheduler stops keeping up.

Usage:
    python loadtest/sweep.py --rates 10 20 40 80 160 320 --duration-hours 24
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from trace_gen import generate_trace, offered_node_hours


def _request_with_retry(method: str, url: str, *, attempts: int = 5, backoff_s: float = 3.0, **kwargs) -> requests.Response:
    """
    Retries on any requests.RequestException (timeouts, connection resets, etc.) and on 5xx responses,
    with linear backoff. Non-5xx HTTP errors (4xx) are not retried -- those are real
    request problems, not transient ones.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            r = requests.request(method, url, **kwargs)
            if r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} Server Error for url: {url}", response=r)
            return r
        except requests.RequestException as e:
            last_exc = e
            if attempt == attempts:
                raise
            print(f"    (request to {url} failed: {e}; retry {attempt}/{attempts - 1})", file=sys.stderr)
            time.sleep(backoff_s * attempt)
    raise last_exc  # unreachable, satisfies type checkers


def submit_run(base_url: str, system: str, start: datetime, end: datetime, jobs: list[dict], *, cooling: bool = False) -> str:
    """
    cooling defaults False here to keep sweep.py's saturation runs (which don't use
    cooling output) at their existing cost -- SimpleCoolingModel is cheap, but there's
    no reason to pay it on every sweep level when only export_sim.py's replay exports
    need real cooling data. Pass cooling=True explicitly for that case.
    """
    payload = {
        "system": system,
        "realtime": False,
        "cooling": cooling,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "replay": True,
        "jobs": jobs,
    }
    r = _request_with_retry("POST", f"{base_url}/simulation/run", json=payload, timeout=60)
    r.raise_for_status()
    return r.json()["id"]


def poll_until_done(base_url: str, sim_id: str, timeout_s: float, poll_interval_s: float) -> dict:
    """
    Polls via /simulation/list with an explicit field selector rather than GET
    /simulation/{id}. The latter always fetches the `config` field, which embeds the
    full submitted job list -- Druid's ANY_VALUE aggregation for that column is capped
    at 4KB (simulation_server/server/service.py:230), so once a job trace pushes the
    serialized config past ~35 jobs, the truncated JSON fails to parse server-side and
    GET /simulation/{id} 500s even though the simulation itself is running/succeeding
    fine. Excluding `config` from the field selector avoids the bug entirely.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        r = _request_with_retry(
            "GET",
            f"{base_url}/simulation/list",
            params={"id": f"eq:{sim_id}", "fields": "state,progress,error_messages", "limit": 1},
            timeout=30,
        )
        r.raise_for_status()
        results = r.json()["results"]
        if not results:
            raise RuntimeError(f"sim {sim_id} not found while polling")
        sim = results[0]
        if sim["state"] != "running":
            return sim
        if time.monotonic() > deadline:
            raise TimeoutError(f"sim {sim_id} still running after {timeout_s}s (progress={sim.get('progress')})")
        time.sleep(poll_interval_s)


def fetch_system_timeseries(base_url: str, sim_id: str, granularity_s: int) -> list[dict]:
    r = _request_with_retry(
        "GET",
        f"{base_url}/simulation/{sim_id}/scheduler/system",
        params={"granularity": granularity_s},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["data"]


def fetch_cooling_cdu(base_url: str, sim_id: str, granularity_s: int) -> list[dict]:
    """
    Per-CDU cooling timeseries from SimpleCoolingModel (rack/facility supply & return
    temps, flow, pressure -- see raps/raps/simple_cooling.py). Only populated when the
    sim was submitted with cooling=True; the endpoint returns an empty series otherwise.
    idt4gdc systems are configured with num_cdus=1, so this is one system-wide reading
    per timestamp, not per-rack.
    """
    r = _request_with_retry(
        "GET",
        f"{base_url}/simulation/{sim_id}/cooling/cdu",
        params={"granularity": granularity_s},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["data"]


def fetch_all_jobs(base_url: str, sim_id: str, page_size: int = 5000) -> list[dict]:
    jobs, offset = [], 0
    while True:
        r = _request_with_retry(
            "GET",
            f"{base_url}/simulation/{sim_id}/scheduler/jobs",
            params={"limit": page_size, "offset": offset},
            timeout=60,
        )
        r.raise_for_status()
        body = r.json()
        jobs.extend(body["results"])
        offset += page_size
        if offset >= body["total_results"]:
            break
    return jobs


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def compute_metrics(offered_jobs: list[dict], sim_jobs: list[dict], system_ts: list[dict]) -> dict:
    started = [j for j in sim_jobs if j.get("time_start")]
    wait_s = sorted(
        (parse_iso(j["time_start"]) - parse_iso(j["time_submission"])).total_seconds()
        for j in started
    )
    never_started = len(sim_jobs) - len(started)

    queue_depths = [p["jobs_pending"] for p in system_ts]
    utils = [p["system_util"] for p in system_ts]

    def pct(sorted_vals, p):
        if not sorted_vals:
            return 0.0
        idx = min(int(len(sorted_vals) * p), len(sorted_vals) - 1)
        return sorted_vals[idx]

    return {
        "offered_jobs": len(offered_jobs),
        "never_started": never_started,
        "completed": sum(1 for j in sim_jobs if j["state_current"] == "COMPLETED"),
        "avg_wait_s": sum(wait_s) / len(wait_s) if wait_s else 0.0,
        "p95_wait_s": pct(wait_s, 0.95),
        "max_wait_s": wait_s[-1] if wait_s else 0.0,
        "max_queue_depth": max(queue_depths) if queue_depths else 0,
        "avg_queue_depth": sum(queue_depths) / len(queue_depths) if queue_depths else 0.0,
        "avg_system_util": sum(utils) / len(utils) if utils else 0.0,
        "peak_system_util": max(utils) if utils else 0.0,
    }


def run_sweep(args) -> list[dict]:
    start = datetime.now(timezone.utc)
    end = start + timedelta(hours=args.duration_hours)
    rows = []

    for rate in args.rates:
        jobs = generate_trace(
            duration_hours=args.duration_hours,
            rate_per_hour=rate,
            min_nodes=args.min_nodes,
            max_nodes=args.max_nodes,
            gpu_fraction=args.gpu_fraction,
            min_runtime_s=args.min_runtime_s,
            max_runtime_s=args.max_runtime_s,
            seed=args.seed,
        )
        node_hours = offered_node_hours(jobs)
        offered_util_pct = 100.0 * node_hours / (args.cluster_nodes * args.duration_hours)
        print(
            f"\n=== rate={rate}/hr  jobs={len(jobs)}  "
            f"offered_util={offered_util_pct:.0f}% of {args.cluster_nodes} nodes ===",
            file=sys.stderr,
        )

        if not jobs:
            print("  no jobs generated at this rate, skipping", file=sys.stderr)
            continue

        base_row = {
            "sim_id": None,
            "rate_per_hour": rate,
            "offered_jobs": len(jobs),
            "offered_node_hours": round(node_hours, 1),
            "offered_util_pct": round(offered_util_pct, 1),
        }

        try:
            sim_id = submit_run(args.base_url, args.system, start, end, jobs)
            base_row["sim_id"] = sim_id
            print(f"  submitted sim_id={sim_id}, polling...", file=sys.stderr)
            sim = poll_until_done(args.base_url, sim_id, args.timeout_s, args.poll_interval_s)
        except Exception as e:
            # Retries inside _request_with_retry already absorb transient blips; if we
            # still land here (retries exhausted, or a non-HTTP error like our own
            # TimeoutError from poll_until_done), don't let it take down the rest of the
            # sweep -- record it and move to the next load level.
            print(f"  -> SCRIPT ERROR: {e}", file=sys.stderr)
            rows.append({
                **base_row, "status": "ERROR", "error": str(e),
                "completed": None, "never_started": None,
                "avg_wait_s": None, "p95_wait_s": None, "max_wait_s": None,
                "avg_queue_depth": None, "max_queue_depth": None,
                "avg_system_util_pct": None, "peak_system_util_pct": None,
            })
            continue

        if sim["state"] != "success":
            # A hard scheduling failure (e.g. resource manager raising instead of
            # queueing) IS a saturation data point -- record it rather than dropping it,
            # since "the scheduler crashed" is a more severe capacity signal than "the
            # queue grew".
            print(f"  -> sim FAILED: {sim.get('error_messages')}", file=sys.stderr)
            rows.append({
                **base_row, "status": "FAILED", "error": sim.get("error_messages"),
                "completed": None, "never_started": None,
                "avg_wait_s": None, "p95_wait_s": None, "max_wait_s": None,
                "avg_queue_depth": None, "max_queue_depth": None,
                "avg_system_util_pct": None, "peak_system_util_pct": None,
            })
            continue

        try:
            sim_jobs = fetch_all_jobs(args.base_url, sim_id)
            system_ts = fetch_system_timeseries(args.base_url, sim_id, args.granularity_s)
        except Exception as e:
            print(f"  -> SCRIPT ERROR fetching results: {e}", file=sys.stderr)
            rows.append({
                **base_row, "status": "ERROR", "error": str(e),
                "completed": None, "never_started": None,
                "avg_wait_s": None, "p95_wait_s": None, "max_wait_s": None,
                "avg_queue_depth": None, "max_queue_depth": None,
                "avg_system_util_pct": None, "peak_system_util_pct": None,
            })
            continue
        metrics = compute_metrics(jobs, sim_jobs, system_ts)
        row = {
            **base_row, "status": "OK", "error": None,
            "completed": metrics["completed"],
            "never_started": metrics["never_started"],
            "avg_wait_s": round(metrics["avg_wait_s"], 1),
            "p95_wait_s": round(metrics["p95_wait_s"], 1),
            "max_wait_s": round(metrics["max_wait_s"], 1),
            "avg_queue_depth": round(metrics["avg_queue_depth"], 2),
            "max_queue_depth": metrics["max_queue_depth"],
            # system_util from RAPS is already a 0-100 percentage (engine.py:
            # num_active_nodes / AVAILABLE_NODES * 100), not a 0-1 fraction -- do not
            # rescale it again here.
            "avg_system_util_pct": round(metrics["avg_system_util"], 1),
            "peak_system_util_pct": round(metrics["peak_system_util"], 1),
        }
        rows.append(row)

        print(
            f"  -> max_queue_depth={row['max_queue_depth']}  "
            f"avg_wait={row['avg_wait_s']:.0f}s  p95_wait={row['p95_wait_s']:.0f}s  "
            f"peak_util={row['peak_system_util_pct']:.0f}%  "
            f"never_started={row['never_started']}",
            file=sys.stderr,
        )

    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://localhost:8081", help="ExaDigiT simulation-server URL (bypass the bridge)")
    p.add_argument("--system", default="idt4gdc_dc1")
    p.add_argument("--cluster-nodes", type=int, default=512, help="Total nodes, for offered-utilization context (idt4gdc_dc1: 512)")
    p.add_argument("--rates", type=float, nargs="+", required=True, help="Offered load levels to sweep, in jobs/hour")
    p.add_argument("--duration-hours", type=float, default=24.0, help="Simulated window per run")
    p.add_argument("--min-nodes", type=int, default=1)
    p.add_argument("--max-nodes", type=int, default=16, help="Cap at scheduler.max_nodes_per_job in idt4gdc_dc1.yaml")
    p.add_argument("--gpu-fraction", type=float, default=0.2)
    p.add_argument("--min-runtime-s", type=int, default=300)
    p.add_argument("--max-runtime-s", type=int, default=14400)
    p.add_argument("--granularity-s", type=int, default=60, help="scheduler/system timeseries resolution")
    p.add_argument("--timeout-s", type=float, default=300.0, help="Max real-time seconds to wait per sim run")
    p.add_argument("--poll-interval-s", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("-o", "--output", default=None, help="Write results as CSV to this path")
    args = p.parse_args()

    rows = run_sweep(args)

    if not rows:
        print("\nNo successful runs.", file=sys.stderr)
        sys.exit(1)

    cols = [
        "rate_per_hour", "status", "offered_jobs", "offered_node_hours", "offered_util_pct",
        "completed", "never_started",
        "avg_wait_s", "p95_wait_s", "max_wait_s",
        "avg_queue_depth", "max_queue_depth",
        "avg_system_util_pct", "peak_system_util_pct",
        "error", "sim_id",
    ]

    print("\n" + ",".join(cols))
    for row in rows:
        print(",".join(str(row[c]) for c in cols))

    if args.output:
        with open(args.output, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for row in rows:
                w.writerow({c: row[c] for c in cols})
        print(f"\nWrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
