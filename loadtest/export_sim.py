"""
Runs an ExaDigiT simulation to completion and exports its results -- job
records, system-level timeseries, full per-job power-history, and (by default)
real per-CDU cooling telemetry -- to a flat export folder (default:
exports/sim_export_<sim_id>/), for later replay into the Grafana ops-dashboard
demo (idt4gdc-grafana-demo).

Cooling is submitted as cooling=True so raps/raps/simple_cooling.py's
SimpleCoolingModel actually runs and GET /simulation/{id}/cooling/cdu returns
real rack/facility supply & return temperatures instead of an empty series.
Pass --no-cooling to skip it (faster, matches old export shape without cooling_cdu.json
-- replay/app.py in idt4gdc-grafana-demo falls back to synthetic temperatures when that file is
absent).

Usage:
    python loadtest/export_sim.py --system idt4gdc_dc1 --rate-per-hour 20 --duration-hours 4
    python loadtest/export_sim.py --jobs-file my_trace.json --duration-hours 4
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sweep import (
    _request_with_retry, submit_run, poll_until_done, fetch_all_jobs,
    fetch_system_timeseries, fetch_cooling_cdu,
)
from trace_gen import generate_trace, offered_node_hours


def fetch_sim_summary(base_url: str, sim_id: str) -> dict:
    """
    Sim metadata without the `config` field -- GET /simulation/{id}
    """
    r = _request_with_retry(
        "GET",
        f"{base_url}/simulation/list",
        params={
            "id": f"eq:{sim_id}",
            "fields": "user,system,state,start,end,execution_start,execution_end,progress,progress_date",
            "limit": 1,
        },
        timeout=30,
    )
    r.raise_for_status()
    results = r.json()["results"]
    if not results:
        raise RuntimeError(f"sim {sim_id} not found while fetching summary")
    return results[0]


def fetch_job_power_histories(base_url: str, sim_id: str, jobs: list[dict]) -> list[dict]:
    """
    Full per-job power-history series (every point).

    Job-level power is NOT split across nodes here -- that's a modeling
    choice (the bridge divides evenly: power_w / len(nodes)) left to the
    consumer/replay script, so this stays a faithful export of what ExaDigiT
    reported.
    """
    started = [j for j in jobs if j.get("time_start")]
    skipped = len(jobs) - len(started)
    if skipped:
        print(f"  {skipped} job(s) never started, skipping power-history fetch for them", file=sys.stderr)

    out = []
    for i, j in enumerate(started, 1):
        job_id = j["job_id"]
        nodes = j.get("nodes") or []
        if not nodes:
            continue
        r = _request_with_retry(
            "GET",
            f"{base_url}/simulation/{sim_id}/scheduler/jobs/{job_id}/power-history",
            timeout=30,
        )
        r.raise_for_status()
        data = r.json().get("data") or []
        out.append({
            "job_id": job_id,
            "nodes": nodes,
            "power_history": [
                {"timestamp": d["timestamp"], "power_w": d["power"]}
                for d in data
            ],
        })
        if i % 25 == 0 or i == len(started):
            print(f"  fetched power-history for {i}/{len(started)} job(s)", file=sys.stderr)

    return out


def write_export(
        output_dir: Path,
        sim: dict,
        jobs_submitted: list[dict],
        jobs: list[dict],
        system_ts: list[dict],
        job_power: list[dict],
        cooling_cdu: list[dict] | None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "sim.json").write_text(json.dumps(sim, indent=2))
    (output_dir / "trace.json").write_text(json.dumps(jobs_submitted, indent=2))
    (output_dir / "scheduler_jobs.json").write_text(json.dumps({"results": jobs}, indent=2))
    (output_dir / "scheduler_system.json").write_text(json.dumps({"data": system_ts}, indent=2))
    with (output_dir / "job_power_history.jsonl").open("w") as f:
        for record in job_power:
            f.write(json.dumps(record) + "\n")
    # Only written when cooling was actually requested -- its presence/absence is how
    # replay/app.py in idt4gdc-grafana-demo decides real vs. synthetic temperatures.
    if cooling_cdu is not None:
        (output_dir / "cooling_cdu.json").write_text(json.dumps({"data": cooling_cdu}, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://localhost:8081",
                   help="ExaDigiT simulation-server URL (bypass the bridge)")
    p.add_argument("--system", default="idt4gdc_dc1")
    p.add_argument("--duration-hours", type=float, default=4.0, help="Simulated window")
    p.add_argument("--jobs-file", type=Path, default=None,
                   help="Load a job trace (trace_gen.py JSON shape) instead of generating one")
    p.add_argument("--rate-per-hour", type=float, default=20.0, help="Used only when --jobs-file is not given")
    p.add_argument("--min-nodes", type=int, default=1)
    p.add_argument("--max-nodes", type=int, default=16, help="Cap at scheduler.max_nodes_per_job in the system's YAML")
    p.add_argument("--gpu-fraction", type=float, default=0.2)
    p.add_argument("--min-runtime-s", type=int, default=300)
    p.add_argument("--max-runtime-s", type=int, default=14400)
    p.add_argument("--granularity-s", type=int, default=60, help="scheduler/system/cooling timeseries resolution")
    p.add_argument("--cooling", action=argparse.BooleanOptionalAction, default=True,
                   help="Run SimpleCoolingModel and export real per-CDU temperatures (default: on)")
    p.add_argument("--timeout-s", type=float, default=600.0, help="Max real-time seconds to wait for the sim to finish")
    p.add_argument("--poll-interval-s", type=float, default=2.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output-dir", type=Path, default=None, help="Default: ../exports/sim_export_<sim_id>")
    args = p.parse_args()

    if args.jobs_file:
        jobs_in = json.loads(args.jobs_file.read_text())
        print(f"Loaded {len(jobs_in)} job(s) from {args.jobs_file}", file=sys.stderr)
    else:
        jobs_in = generate_trace(
            duration_hours=args.duration_hours,
            rate_per_hour=args.rate_per_hour,
            min_nodes=args.min_nodes,
            max_nodes=args.max_nodes,
            gpu_fraction=args.gpu_fraction,
            min_runtime_s=args.min_runtime_s,
            max_runtime_s=args.max_runtime_s,
            seed=args.seed,
        )
        print(f"Generated {len(jobs_in)} job(s), {offered_node_hours(jobs_in):.0f} offered node-hours", file=sys.stderr)

    if not jobs_in:
        print("No jobs to submit, aborting.", file=sys.stderr)
        sys.exit(1)

    start = datetime.now(timezone.utc)
    end = start + timedelta(hours=args.duration_hours)

    print(f"Submitting sim: system={args.system} window={start.isoformat()}..{end.isoformat()} cooling={args.cooling}",
          file=sys.stderr)
    sim_id = submit_run(args.base_url, args.system, start, end, jobs_in, cooling=args.cooling)
    print(f"sim_id={sim_id}, polling until done...", file=sys.stderr)

    sim = poll_until_done(args.base_url, sim_id, args.timeout_s, args.poll_interval_s)
    if sim["state"] != "success":
        print(f"Sim did not succeed: state={sim['state']} error={sim.get('error_messages')}", file=sys.stderr)
        sys.exit(1)
    print("Sim completed successfully", file=sys.stderr)

    sim_summary = fetch_sim_summary(args.base_url, sim_id)
    jobs = fetch_all_jobs(args.base_url, sim_id)
    print(f"Fetched {len(jobs)} job record(s)", file=sys.stderr)
    system_ts = fetch_system_timeseries(args.base_url, sim_id, args.granularity_s)
    print(f"Fetched {len(system_ts)} system-timeseries point(s)", file=sys.stderr)
    job_power = fetch_job_power_histories(args.base_url, sim_id, jobs)
    print(f"Fetched power-history for {len(job_power)} job(s)", file=sys.stderr)

    cooling_cdu = None
    if args.cooling:
        cooling_cdu = fetch_cooling_cdu(args.base_url, sim_id, args.granularity_s)
        if cooling_cdu:
            print(f"Fetched {len(cooling_cdu)} cooling-CDU point(s)", file=sys.stderr)
        else:
            print("cooling=True but no cooling-CDU data was returned (empty series)", file=sys.stderr)

    output_dir = args.output_dir or (Path(__file__).resolve().parent.parent / "exports" / f"sim_export_{sim_id}")
    write_export(output_dir, sim_summary, jobs_in, jobs, system_ts, job_power, cooling_cdu)
    print(f"\nWrote export to {output_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
