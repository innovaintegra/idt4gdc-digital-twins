# simulation_server/simulation/dataloaders/_idt4gdc_common.py
"""
Shared implementation behind the idt4gdc_dc1/dc2/dc3 simulation-server dataloaders.

Logic is identical across all iDT4GDC data centres; only the system name embedded in
error messages differs. simulation.py resolves
`simulation_server.simulation.dataloaders.<system>` by exact module name, so each DC
still needs its own module -- but that module can just be a one-line call into
make_loaders() below instead of a full copy of this file.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Iterable, List, Union, Optional

import numpy as np
import pandas as pd
from datetime import datetime, timezone

from raps.job import job_dict, Job
from raps.utils import WorkloadData


def _coerce_required_cols(df: pd.DataFrame, system_name: str) -> pd.DataFrame:
    """
    Ensure the dataframe has the minimal columns and sensible dtypes.
    Required: job_id, submit_time_s, runtime_s, nodes
    Optional: gpus
    """
    required = ["job_id", "submit_time_s", "runtime_s", "nodes"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{system_name} jobs parquet missing columns: {missing}")

    if "gpus" not in df.columns:
        df = df.copy()
        df["gpus"] = 0

    # Basic dtype coercion
    df["job_id"] = df["job_id"].astype(object)  # allow int/str
    df["submit_time_s"] = pd.to_numeric(df["submit_time_s"], errors="raise")
    df["runtime_s"] = pd.to_numeric(df["runtime_s"], errors="raise")
    df["nodes"] = pd.to_numeric(df["nodes"], errors="raise").astype(int)
    df["gpus"] = pd.to_numeric(df["gpus"], errors="coerce").fillna(0).astype(int)

    # Sanity checks
    if (df["submit_time_s"] < 0).any():
        raise ValueError("submit_time_s must be >= 0")
    if (df["runtime_s"] <= 0).any():
        raise ValueError("runtime_s must be > 0")
    if (df["nodes"] <= 0).any():
        raise ValueError("nodes must be > 0")
    if (df["gpus"] < 0).any():
        raise ValueError("gpus must be >= 0")

    return df


def _build_jobs_from_df(jobs_df: pd.DataFrame, system_name: str, **kwargs) -> WorkloadData:
    """
    Convert a simple jobs dataframe into a WorkloadData object for RAPS.

    Expected columns:
        job_id, submit_time_s, runtime_s, nodes, gpus (gpus optional)
    """
    jobs_df = _coerce_required_cols(jobs_df, system_name)

    # RAPS passes config in kwargs for some loaders; support both "config" and "system_config"
    config = kwargs.get("config") or kwargs.get("system_config") or {}

    trace_quanta = float(config.get("TRACE_QUANTA", 1.0))      # seconds per time step
    cpus_per_node = int(config.get("CPUS_PER_NODE", 1))
    gpus_per_node = int(config.get("GPUS_PER_NODE", 0))

    priority_default = int(config.get("DEFAULT_JOB_PRIORITY", 0))
    partition_default = int(config.get("DEFAULT_PARTITION", 0))

    telemetry_start = 0
    max_end = (jobs_df["submit_time_s"] + jobs_df["runtime_s"]).max()
    telemetry_end = int(np.ceil(max_end))

    telemetry_start_timestamp = datetime.fromtimestamp(0, tz=timezone.utc)

    jobs: List[Job] = []

    for _, row in jobs_df.iterrows():
        job_id = row["job_id"]
        submit_time_s = float(row["submit_time_s"])
        runtime_s = float(row["runtime_s"])
        nodes_required = int(row["nodes"])
        gpus_requested = int(row.get("gpus", 0))

        steps = max(int(np.ceil(runtime_s / trace_quanta)), 1)

        cpu_trace = np.full(steps, cpus_per_node, dtype=float)
        if gpus_requested > 0 and gpus_per_node > 0:
            gpu_trace = np.full(steps, gpus_per_node, dtype=float)
        else:
            gpu_trace = np.zeros(steps, dtype=float)

        ntx_trace: Iterable[float] = []
        nrx_trace: Iterable[float] = []

        start_time = int(submit_time_s)
        end_time = int(submit_time_s + runtime_s)
        wall_time = int(runtime_s)

        account = "default"
        end_state = "C"  # Completed
        scheduled_nodes = None  # let scheduler place nodes
        priority = int(row.get("priority", priority_default)) if "priority" in jobs_df.columns else priority_default
        partition = int(row.get("partition", partition_default)) if "partition" in jobs_df.columns else partition_default
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
            gpu_units_required=gpus_requested,
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

        jobs.append(Job(job_info))

    return WorkloadData(
        jobs=jobs,
        telemetry_start=telemetry_start,
        telemetry_end=telemetry_end,
        start_date=telemetry_start_timestamp,
    )


def make_loaders(system_name: str):
    """
    Build the module-level functions simulation.py expects (load_data,
    load_data_from_df, node_index_to_name, cdu_index_to_name, cdu_pos), with
    `system_name` baked into error messages so each per-DC module still reports its
    own name.
    """

    def load_data(jobs_path: Union[str, Iterable[str]], **kwargs) -> WorkloadData:
        """
        Entry point used by raps.telemetry.Telemetry.load_data.

        Accepts either:
          - a single string path
          - an iterable with exactly one path

        Expects a Parquet file with: job_id, submit_time_s, runtime_s, nodes, gpus.
        """
        if isinstance(jobs_path, (list, tuple)):
            if len(jobs_path) != 1:
                raise ValueError(f"{system_name} dataloader expects a single file, got: {jobs_path}")
            path = jobs_path[0]
        else:
            path = jobs_path

        p = Path(path)
        if p.suffix.lower() != ".parquet":
            raise ValueError(f"{system_name} expects a Parquet file, got: {p}")

        jobs_df = pd.read_parquet(p, engine="pyarrow")
        return _build_jobs_from_df(jobs_df, system_name, **kwargs)

    def load_data_from_df(jobs_df: pd.DataFrame, **kwargs) -> WorkloadData:
        """Helper for in-memory DataFrames."""
        return _build_jobs_from_df(jobs_df, system_name, **kwargs)

    # --- Minimal name/pos helpers expected by some parts of the stack ---

    def node_index_to_name(index: int, config: Optional[dict] = None) -> str:
        return f"node{index:04d}"

    def cdu_index_to_name(index: int, config: Optional[dict] = None) -> str:
        return f"cdu{index:02d}"

    def cdu_pos(index: int, config: Optional[dict] = None) -> tuple[int, int]:
        return (0, index)

    return load_data, load_data_from_df, node_index_to_name, cdu_index_to_name, cdu_pos
