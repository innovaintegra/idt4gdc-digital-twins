from __future__ import annotations

import uuid
from typing import Iterable, List
from datetime import datetime

import numpy as np
import pandas as pd

from ...models.sim import ServerSimConfig
from .. import SimException

from raps.job import job_dict, Job
from raps.utils import WorkloadData


# If True, reject jobs that would finish after sim end.
# If False, allow spillover (job may still be running at sim end).
REQUIRE_JOB_FINISH_WITHIN_SIM = False

# If True, set start_time/end_time based on submit_time/runtime (pre-scheduled-like).
# If False, leave start/end unset so the scheduler determines them.
PRESET_START_END_TIMES = True


def _build_workload(
    jobs_df: pd.DataFrame,
    *,
    sim_start: datetime,
    sim_end: datetime,
    config: dict,
) -> WorkloadData:
    """
    Build a WorkloadData object where submit_time_s is interpreted as seconds from sim_start.

    jobs_df columns:
      job_id, submit_time_s, runtime_s, nodes, gpus (optional)
    """
    trace_quanta = float(config.get("TRACE_QUANTA", 1.0))
    cpus_per_node = int(config.get("CPUS_PER_NODE", 1))
    gpus_per_node = int(config.get("GPUS_PER_NODE", 0))

    sim_len_s = int((sim_end - sim_start).total_seconds())
    if sim_len_s <= 0:
        raise SimException("Simulation end must be after start")

    telemetry_start = 0

    # Telemetry end should cover at least the jobs timeline (seconds-from-start)
    max_end = (jobs_df["submit_time_s"] + jobs_df["runtime_s"]).max()
    telemetry_end = int(np.ceil(max_end))

    # IMPORTANT: anchor workload timeline to simulation start so jobs fall within sim window
    telemetry_start_timestamp = sim_start

    jobs: List[Job] = []

    for _, row in jobs_df.iterrows():
        job_id = row["job_id"]
        submit_time_s = float(row["submit_time_s"])
        runtime_s = float(row["runtime_s"])
        nodes_required = int(row["nodes"])
        gpus_requested = int(row.get("gpus", 0))

        # Steps at trace_quanta resolution
        steps = max(int(np.ceil(runtime_s / trace_quanta)), 1)

        # Simple constant utilization traces
        cpu_trace = np.full(steps, cpus_per_node, dtype=float)
        if gpus_requested > 0 and gpus_per_node > 0:
            gpu_trace = np.full(steps, gpus_per_node, dtype=float)
        else:
            gpu_trace = np.zeros(steps, dtype=float)

        ntx_trace: Iterable[float] = []
        nrx_trace: Iterable[float] = []

        wall_time = int(runtime_s)

        # Optionally pre-set start/end (replay-like). For scheduler-driven sims, set to None.
        if PRESET_START_END_TIMES:
            start_time = int(submit_time_s)
            end_time = int(submit_time_s + runtime_s)
        else:
            start_time = None
            end_time = None

        job_info = job_dict(
            nodes_required=nodes_required,
            name=str(job_id) if job_id is not None else str(uuid.uuid4())[:6],
            account="default",
            cpu_trace=cpu_trace,
            gpu_trace=gpu_trace,
            nrx_trace=nrx_trace,
            ntx_trace=ntx_trace,
            end_state="C",
            scheduled_nodes=None,
            id=job_id,
            priority=0,
            partition=0,
            submit_time=int(submit_time_s),
            time_limit=wall_time,
            start_time=start_time,
            end_time=end_time,
            expected_run_time=wall_time,
            current_run_time=0,
            trace_time=steps * trace_quanta,
            trace_start_time=0,
            trace_end_time=steps * trace_quanta,
            trace_quanta=trace_quanta,
            trace_missing_values=False,
        )
        jobs.append(Job(job_info))

    return WorkloadData(
        jobs=jobs,
        telemetry_start=telemetry_start,
        telemetry_end=max(telemetry_end, sim_len_s),  # ensure covers sim duration too
        start_date=telemetry_start_timestamp,
    )


def load_data(_paths, **kwargs) -> WorkloadData:
    """
    Dataloader entrypoint.

    Ignores _paths; uses sim_config.jobs.
    """
    sim_config: ServerSimConfig = kwargs.get("sim_config")
    if sim_config is None:
        raise SimException("inline_jobs loader missing sim_config in kwargs")

    if not getattr(sim_config, "jobs", None):
        raise SimException("jobs is empty but inline_jobs loader was selected")

    if sim_config.start is None or sim_config.end is None:
        raise SimException("start/end must be provided when using inline_jobs")

    # Convert list[dict] -> DataFrame and validate columns
    df = pd.DataFrame(sim_config.jobs)

    required = {"job_id", "submit_time_s", "runtime_s", "nodes"}
    missing = required - set(df.columns)
    if missing:
        raise SimException(f"jobs missing keys: {sorted(missing)}")

    if "gpus" not in df.columns:
        df["gpus"] = 0

    # Coerce types
    df["submit_time_s"] = pd.to_numeric(df["submit_time_s"], errors="raise")
    df["runtime_s"] = pd.to_numeric(df["runtime_s"], errors="raise")
    df["nodes"] = pd.to_numeric(df["nodes"], errors="raise").astype(int)
    df["gpus"] = pd.to_numeric(df["gpus"], errors="coerce").fillna(0).astype(int)

    if (df["submit_time_s"] < 0).any() or (df["runtime_s"] <= 0).any() or (df["nodes"] <= 0).any():
        raise SimException("Invalid values in jobs (submit_time_s>=0, runtime_s>0, nodes>0 required)")

    # Ensure jobs occur within simulation window
    sim_len_s = int((sim_config.end - sim_config.start).total_seconds())
    if (df["submit_time_s"] >= sim_len_s).any():
        bad = df[df["submit_time_s"] >= sim_len_s][["job_id", "submit_time_s"]].to_dict(orient="records")
        raise SimException(f"Some jobs submit after sim end (submit_time_s >= {sim_len_s}): {bad}")

    if REQUIRE_JOB_FINISH_WITHIN_SIM:
        finish_s = df["submit_time_s"] + df["runtime_s"]
        if (finish_s > sim_len_s).any():
            bad = df[finish_s > sim_len_s][["job_id", "submit_time_s", "runtime_s"]].to_dict(orient="records")
            raise SimException(f"Some jobs finish after sim end (submit+runtime > {sim_len_s}): {bad}")

    # Use whatever config RAPS passes down; if not, default empty
    config = kwargs.get("config") or kwargs.get("system_config") or {}
    return _build_workload(
        df,
        sim_start=sim_config.start,
        sim_end=sim_config.end,
        config=config,
    )


# Optional helpers some parts of telemetry use
def node_index_to_name(index: int, config: dict | None = None) -> str:
    return f"node{index:04d}"


def cdu_index_to_name(index: int, config: dict | None = None) -> str:
    return f"cdu{index:02d}"


def cdu_pos(index: int, config: dict | None = None) -> tuple[int, int]:
    return (0, index)
