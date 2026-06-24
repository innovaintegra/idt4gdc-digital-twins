from __future__ import annotations

from typing import Any

import requests
from fastapi import FastAPI, HTTPException

from app.config import load_settings
from app.models import SimulationRequest, SimulationResponse
from app.clients.exadigit import ExaDigiTClient
from app.clients.opendcim import OpenDCIMClient
from app.poller.sim_poller import SimPoller, ActiveSim, load_node_to_pduid


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    return s


settings = load_settings()
session = make_session()

exa = ExaDigiTClient(settings.exadigit.base_url, session=session)
dcim = OpenDCIMClient(
    settings.opendcim.base_url,
    user=settings.opendcim.user_id,
    key=settings.opendcim.api_key,
    session=session,
    verify_ssl=settings.opendcim.verify_ssl,
)

app = FastAPI(title="DT Bridge (Real-time ExaDigiT → OpenDCIM)")
poller = SimPoller(exa=exa, dcim=dcim, poll_interval_s=settings.poll_interval_s)


@app.on_event("startup")
def _startup() -> None:
    poller.start()


@app.post("/simulate", response_model=SimulationResponse)
def simulate(req: SimulationRequest) -> SimulationResponse:
    try:
        node_to_pduid = load_node_to_pduid(settings.node_pdu_map_path)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"NODE_PDUID_MAP not found: {settings.node_pdu_map_path}")

    if req.start_time >= req.end_time:
        raise HTTPException(status_code=400, detail="start_time must be before end_time")

    sim_payload: dict[str, Any] = {
        "system": req.system,
        "realtime": True,
        "start": req.start_time,
        "end": req.end_time,
        "replay": True,
        "cooling": True,
        "jobs": [
            {
                "job_id": j.job_id,
                "submit_time_s": j.submit_time_s,
                "runtime_s": j.runtime_s,
                "nodes": j.nodes,
                "gpus": j.gpus,
            }
            for j in req.jobs
        ],
    }

    try:
        sim = exa.submit_simulation(sim_payload)
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"ExaDigiT submit failed: {e}")

    sim_id = sim.get("id") or sim.get("sim_id") or sim.get("simulation_id")
    if not sim_id:
        raise HTTPException(status_code=502, detail=f"ExaDigiT response missing sim id: {sim}")

    poller.register_sim(ActiveSim(sim_id=str(sim_id), system=req.system, node_to_pduid=node_to_pduid))
    return SimulationResponse(sim_id=str(sim_id), poll_interval_s=settings.poll_interval_s)


@app.delete("/simulate/{sim_id}")
def stop_sim(sim_id: str) -> dict:
    poller.unregister_sim(sim_id)
    return {"ok": True, "sim_id": sim_id}


def run() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(__import__("os").environ.get("BRIDGE_PORT", "8000")))
