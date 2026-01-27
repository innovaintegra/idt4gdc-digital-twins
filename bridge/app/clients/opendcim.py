from __future__ import annotations

import requests
from ..models import PduStat


class OpenDCIMClient:
    def __init__(
        self,
        base_url: str,
        user: str,
        key: str,
        session: requests.Session,
        verify_ssl: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.s = session
        self.verify_ssl = verify_ssl
        self.headers = {
            "Content-Type": "application/json",
            "UserID": user,
            "APIKey": key,
        }

    def post_pdustat(self, stat: PduStat) -> None:
        r = self.s.post(
            f"{self.base_url}/pdustats",
            headers=self.headers,
            json=stat.model_dump(),
            timeout=30,
            verify=self.verify_ssl,
        )
        r.raise_for_status()