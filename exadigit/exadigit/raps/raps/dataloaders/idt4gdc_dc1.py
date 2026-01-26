import uuid
from pathlib import Path
from typing import List, Union, Iterable

import numpy as np
import pandas as pd
from datetime import datetime, timezone

from ..job import job_dict, Job
from ..utils import WorkloadData


def _build_jobs_from_df(jobs_df: pd.DataFrame, **kwargs) -> WorkloadData:
    """
    Convert a simple jobs dataframe into a WorkloadData object for RAPS.

    Expected columns in jobs_df:
        job_id, submit_time_s, runtime_s, nodes, gpus (gpus optional)
    """
    config = kwargs.get("config") or {}

    trace_quanta = float(config.get("TRACE_QUANTA", 1.0))      # seconds per time step
    cpus_per_node = int(config.get("CPUS_PER_NODE", 1))
    gpus_per_node = int(config.get("GPUS_PER_NODE", 0))

    priority_default = int(config.get("DEFAULT_JOB_PRIORITY", 0))
    partition_default = int(config.get("DEFAULT_PARTITION", 0))

    # Telemetry timeline: simple 0-based seconds
    telemetry_start = 0
    # end = max(submit + runtime)
    max_end = (jobs_df["submit_time_s"] + jobs_df["runtime_s"]).max()
    telemetry_end = int(np.ceil(max_end))

    # Fake "real" timestamp origin (Unix epoch)
    telemetry_start_timestamp = datetime.fromtimestamp(0, tz=timezone.utc)

    jobs: List[Job] = []

    for _, row in jobs_df.iterrows():
        job_id = row["job_id"]
        submit_time_s = float(row["submit_time_s"])
        runtime_s = float(row["runtime_s"])
        nodes_required = int(row["nodes"])
        gpus_requested = int(row.get("gpus", 0))

        # Number of trace steps
        steps = max(int(np.ceil(runtime_s / trace_quanta)), 1)

        # Simple constant utilization traces
        cpu_trace = np.full(steps, cpus_per_node, dtype=float)
        if gpus_requested > 0 and gpus_per_node > 0:
            gpu_trace = np.full(steps, gpus_per_node, dtype=float)
        else:
            gpu_trace = np.zeros(steps, dtype=float)

        ntx_trace: Iterable[float] = []
        nrx_trace: Iterable[float] = []

        # For this simple synthetic case, assume "expected" start=end based on submit_time
        start_time = int(submit_time_s)
        end_time = int(submit_time_s + runtime_s)
        wall_time = int(runtime_s)

        account = "default"
        end_state = "C"  # Completed
        scheduled_nodes = None  # let scheduler place nodes
        priority = int(row.get("priority", priority_default))
        partition = int(row.get("partition", partition_default))
        time_limit = wall_time

        trace_time = steps * trace_quanta
        trace_start_time = 0
        trace_end_time = trace_time
        trace_missing_values = False

        name = str(job_id) if job_id is not None else str(uuid.uuid4())[:6]

        job_info = job_dict(
            nodes_required=nodes_required,
            name=name,
            account=account,
            cpu_trace=cpu_trace,
            gpu_trace=gpu_trace,
            nrx_trace=nrx_trace,
            ntx_trace=ntx_trace,
            end_state=end_state,
            scheduled_nodes=scheduled_nodes,
            id=job_id,
            priority=priority,
            partition=partition,
            submit_time=int(submit_time_s),
            time_limit=time_limit,
            start_time=start_time,
            end_time=end_time,
            expected_run_time=wall_time,
            current_run_time=0,
            trace_time=trace_time,
            trace_start_time=trace_start_time,
            trace_end_time=trace_end_time,
            trace_quanta=trace_quanta,
            trace_missing_values=trace_missing_values,
        )

        job = Job(job_info)
        jobs.append(job)

    return WorkloadData(
        jobs=jobs,
        telemetry_start=telemetry_start,
        telemetry_end=telemetry_end,
        start_date=telemetry_start_timestamp,
    )


def load_data(jobs_path: Union[str, Iterable[str]], **kwargs) -> WorkloadData:
    """
    Entry point used by raps.telemetry.Telemetry.load_data.

    Accepts a single path or a list with one path; expects a Parquet file
    with job_id, submit_time_s, runtime_s, nodes, gpus.
    """
    if isinstance(jobs_path, (list, tuple)):
        if len(jobs_path) != 1:
            raise ValueError(f"idt4gdc_dc1 dataloader expects a single file, got: {jobs_path}")
        path = jobs_path[0]
    else:
        path = jobs_path

    p = Path(path)
    if p.suffix != ".parquet":
        raise ValueError(f"idt4gdc_dc1 expects a Parquet file, got: {p}")

    jobs_df = pd.read_parquet(p, engine="pyarrow")
    return _build_jobs_from_df(jobs_df, **kwargs)


def load_data_from_df(jobs_df: pd.DataFrame, **kwargs) -> WorkloadData:
    """Helper that mirrors marconi100 style for in-memory DataFrames."""
    return _build_jobs_from_df(jobs_df, **kwargs)


def node_index_to_name(index: int, config: dict):
    """ Converts an index value back to a name string based on system configuration. """
    return f"node{index:04d}"


def cdu_index_to_name(index: int, config: dict):
    return f"cdu{index:02d}"


def cdu_pos(index: int, config: dict) -> tuple[int, int]:
    """ Return (row, col) tuple for a cdu index """
    return (0, index)