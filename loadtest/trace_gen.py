"""
Synthetic job-trace generator for scheduler load testing.

RAPS' own Poisson job generator (job_arrival_time/mtbf) is only reachable via the
RAPS CLI, not the simulation-server HTTP API. This generates an equivalent Poisson
arrival trace client-side, in the {job_id, submit_time_s, runtime_s, nodes, gpus}
shape the "replay" / inline_jobs dataloader expects.
"""
from __future__ import annotations

import argparse
import json
import random


def generate_trace(
    *,
    duration_hours: float,
    rate_per_hour: float,
    min_nodes: int = 1,
    max_nodes: int = 16,
    gpu_fraction: float = 0.2,
    min_runtime_s: int = 300,
    max_runtime_s: int = 14400,
    seed: int | None = None,
) -> list[dict]:
    """
    Poisson-arrival job trace over [0, duration_hours*3600).

    rate_per_hour: mean number of job arrivals per hour.
    nodes: sampled uniformly in [min_nodes, max_nodes] (cap at your scheduler's
        max_nodes_per_job config, e.g. 16 for idt4gdc_dc1).
    gpus: set to `nodes` (i.e. 1 GPU/node) with probability gpu_fraction, else 0.
    """
    rng = random.Random(seed)
    duration_s = duration_hours * 3600.0
    mean_interarrival_s = 3600.0 / rate_per_hour

    jobs = []
    t = 0.0
    job_idx = 0
    while True:
        t += rng.expovariate(1.0 / mean_interarrival_s)
        if t >= duration_s:
            break
        nodes = rng.randint(min_nodes, max_nodes)
        gpus = nodes if rng.random() < gpu_fraction else 0
        runtime_s = rng.randint(min_runtime_s, max_runtime_s)
        jobs.append({
            "job_id": f"lt{job_idx:06d}",
            "submit_time_s": round(t, 1),
            "runtime_s": runtime_s,
            "nodes": nodes,
            "gpus": gpus,
        })
        job_idx += 1

    return jobs


def offered_node_hours(jobs: list[dict]) -> float:
    return sum(j["nodes"] * j["runtime_s"] / 3600.0 for j in jobs)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--duration-hours", type=float, default=24.0)
    p.add_argument("--rate-per-hour", type=float, required=True)
    p.add_argument("--min-nodes", type=int, default=1)
    p.add_argument("--max-nodes", type=int, default=16)
    p.add_argument("--gpu-fraction", type=float, default=0.2)
    p.add_argument("--min-runtime-s", type=int, default=300)
    p.add_argument("--max-runtime-s", type=int, default=14400)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("-o", "--output", type=str, default=None, help="Write JSON trace to this file (default: stdout)")
    args = p.parse_args()

    trace = generate_trace(
        duration_hours=args.duration_hours,
        rate_per_hour=args.rate_per_hour,
        min_nodes=args.min_nodes,
        max_nodes=args.max_nodes,
        gpu_fraction=args.gpu_fraction,
        min_runtime_s=args.min_runtime_s,
        max_runtime_s=args.max_runtime_s,
        seed=args.seed,
    )
    print(f"# {len(trace)} jobs, {offered_node_hours(trace):.0f} offered node-hours", flush=True)

    out = json.dumps(trace, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(out)
    else:
        print(out)
