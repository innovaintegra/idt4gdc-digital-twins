from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from ..clients.exadigit import ExaDigiTClient
from ..clients.opendcim import OpenDCIMClient
from ..models import PduStat


def load_node_to_pduid(path: str) -> dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    mapping = {str(k): str(v) for k, v in data.items()}
    print(f"[poller] loaded {len(mapping)} node→PDU mappings from {path}", flush=True)
    return mapping


@dataclass
class ActiveSim:
    sim_id: str
    system: str
    node_to_pduid: Dict[str, str]


class SimPoller:
    def __init__(self, *, exa: ExaDigiTClient, dcim: OpenDCIMClient, poll_interval_s: int):
        self.exa = exa
        self.dcim = dcim
        self.poll_interval_s = int(poll_interval_s)
        self._active: Dict[str, ActiveSim] = {}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run_forever, daemon=True)
        self._stop = False
        print(f"[poller] initialized (poll_interval_s={poll_interval_s})", flush=True)

    def start(self) -> None:
        print("[poller] background thread starting", flush=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True

    def register_sim(self, sim: ActiveSim) -> None:
        with self._lock:
            self._active[sim.sim_id] = sim
        print(f"[poller] registered sim_id={sim.sim_id} system={sim.system}", flush=True)

    def unregister_sim(self, sim_id: str) -> None:
        with self._lock:
            removed = self._active.pop(sim_id, None)
        if removed:
            print(f"[poller] unregistered sim_id={sim_id}", flush=True)
        else:
            print(f"[poller] unregister: sim_id={sim_id} not found (already removed?)", flush=True)

    def _run_forever(self) -> None:
        while not self._stop:
            time.sleep(self.poll_interval_s)
            with self._lock:
                active_count = len(self._active)
            print(f"[poller] tick — {active_count} active sim(s)", flush=True)
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
        print(f"[poller] polling sim_id={sim.sim_id}", flush=True)

        jobs_resp = self.exa.list_jobs(sim.sim_id, limit=10000, offset=0)
        results = jobs_resp.get("results") or []
        if not isinstance(results, list):
            raise ValueError(f"Unexpected jobs response: {jobs_resp}")

        print(f"[poller] sim_id={sim.sim_id} — {len(results)} total job(s)", flush=True)

        running_jobs = []
        for j in results:
            state_current = j.get("state_current")
            if not state_current:
                continue
            if str(state_current).lower() == "running":
                running_jobs.append(j)

        print(f"[poller] sim_id={sim.sim_id} — {len(running_jobs)} running job(s)", flush=True)

        if not running_jobs:
            return

        pdu_watts: Dict[str, float] = {}
        lastread_iso: Optional[str] = None

        for j in running_jobs:
            job_id = j.get("job_id")
            assigned_nodes = j.get("nodes") or []
            print(f"[poller]   job_id={job_id} state=running nodes={assigned_nodes}", flush=True)

            if not assigned_nodes:
                print(f"[poller]   job_id={job_id} has no assigned nodes, skipping", flush=True)
                continue

            ph = self.exa.job_power_history(sim.sim_id, str(job_id))
            data = ph.get("data") or []
            print(f"[poller]   job_id={job_id} power history entries={len(data)}", flush=True)

            if not data:
                print(f"[poller]   job_id={job_id} no power history yet, skipping", flush=True)
                continue

            last = data[-1]
            power_w = float(last.get("power"))
            ts = str(last.get("timestamp"))
            lastread_iso = ts
            per_node = power_w / len(assigned_nodes)
            print(f"[poller]   job_id={job_id} latest power={power_w:.1f}W ts={ts} per_node={per_node:.1f}W", flush=True)

            for node in assigned_nodes:
                pduid = sim.node_to_pduid.get(str(node))
                if pduid is None:
                    raise KeyError(f"No PDUID mapping for node '{node}'")
                pdu_watts[pduid] = pdu_watts.get(pduid, 0.0) + per_node

        if not pdu_watts or not lastread_iso:
            print(f"[poller] sim_id={sim.sim_id} — no PDU data to post", flush=True)
            return

        print(f"[poller] sim_id={sim.sim_id} — posting to OpenDCIM: {dict(pdu_watts)}", flush=True)
        for pduid, watts in pdu_watts.items():
            stat = PduStat(PDUID=pduid, Wattage=float(watts), LastRead=lastread_iso)
            print(f"[poller]   POST pdustat PDUID={pduid} Wattage={watts:.1f}W LastRead={lastread_iso}", flush=True)
            self.dcim.post_pdustat(stat)
            print(f"[poller]   posted PDUID={pduid} OK", flush=True)
