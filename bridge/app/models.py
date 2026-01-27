from __future__ import annotations

from typing import List
from pydantic import BaseModel, Field


class JobIn(BaseModel):
    job_id: str
    submit_time_s: int
    runtime_s: int
    nodes: int  # number of nodes required (ExaDigiT assigns actual node IDs)
    gpus: int = 0


class SimulationRequest(BaseModel):
    system: str = Field(default="idt4gdc_dc1")
    start_time: str
    end_time: str
    jobs: List[JobIn]


class SimulationResponse(BaseModel):
    sim_id: str
    poll_interval_s: int


class PduStat(BaseModel):
    PDUID: int
    Wattage: float
    LastRead: str
