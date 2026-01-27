from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from ..clients.exadigit import ExaDigiTClient
from ..clients.opendcim import OpenDCIMClient
from ..models import PduStat


def load_node_to_pduid(path: str) -> dict[str, int]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {str(k): int(v) for k, v in data.items()}


@dataclass
class ActiveSim:
    sim_id: str
    system: str
    node_to_pduid: Dict[str, int]


class SimPoller:
    def __init__(self, *, exa: ExaDigiTClient, dcim: OpenDCIMClient, poll_interval_s: int):
        self.exa = exa
        self.dcim = dcim
        self.poll_interval_s = int(poll_interval_s)
        self._active: Dict[str, ActiveSim] = {}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run_forever, daemon=True)
        self._stop = False

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop = True

    def register_sim(self, sim: ActiveSim) -> None:
        with self._lock:
            self._active[sim.sim_id] = sim

    def unregister_sim(self, sim_id: str) -> None:
        with self._lock:
            self._active.pop(sim_id, None)

    def _run_forever(self) -> None:
        while not self._stop:
            time.sleep(self.poll_interval_s)
            try:
                self._tick_all()
            except Exception as e:
                print(f"[poller] error: {e}", flush=True)

    def _tick_all(self) -> None:
        with self._lock:
            sims = list(self._active.values())
        for sim in sims:
            self._poll_once(sim)

    def _poll_once(self, sim: ActiveSim) -> None:
        jobs_resp = self.exa.list_jobs(sim.sim_id, limit=10000, offset=0)
        results = jobs_resp.get("results") or []
        if not isinstance(results, list):
            raise ValueError(f"Unexpected jobs response: {jobs_resp}")

        running_jobs = []
        for j in results:
            state_current = j.get("state_current")
            if not state_current:
                continue
            if str(state_current).lower() == "running":
                running_jobs.append(j)

        if not running_jobs:
            return

        pdu_watts: Dict[int, float] = {}
        lastread_iso: Optional[str] = None

        for j in running_jobs:
            job_id = j.get("job_id")
            assigned_nodes = j.get("nodes") or []
            if not assigned_nodes:
                continue

            ph = self.exa.job_power_history(sim.sim_id, str(job_id))
            data = ph.get("data") or []
            if not data:
                continue

            last = data[-1]
            power_w = float(last.get("power"))
            ts = str(last.get("timestamp"))
            lastread_iso = ts

            per_node = power_w / len(assigned_nodes)

            for node in assigned_nodes:
                pduid = sim.node_to_pduid.get(str(node))
                if pduid is None:
                    raise KeyError(f"No PDUID mapping for node '{node}'")
                pdu_watts[pduid] = pdu_watts.get(pduid, 0.0) + per_node

        if not pdu_watts or not lastread_iso:
            return

        for pduid, watts in pdu_watts.items():
            self.dcim.post_pdustat(PduStat(PDUID=pduid, Wattage=float(watts), LastRead=lastread_iso))
