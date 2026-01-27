from __future__ import annotations

import requests


class ExaDigiTClient:
    def __init__(self, base_url: str, *, session: requests.Session):
        self.base_url = base_url.rstrip("/")
        self.s = session

    def submit_simulation(self, payload: dict) -> dict:
        r = self.s.post(f"{self.base_url}/simulation/run", json=payload, timeout=60)
        r.raise_for_status()
        return r.json()

    def list_jobs(self, sim_id: str, limit: int = 10000, offset: int = 0) -> dict:
        r = self.s.get(
            f"{self.base_url}/simulation/{sim_id}/scheduler/jobs",
            params={"limit": limit, "offset": offset},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def job_power_history(self, sim_id: str, job_index_or_id: str) -> dict:
        r = self.s.get(
            f"{self.base_url}/simulation/{sim_id}/scheduler/jobs/{job_index_or_id}/power-history",
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
