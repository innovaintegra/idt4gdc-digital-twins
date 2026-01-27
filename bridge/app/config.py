from __future__ import annotations

import os
from pydantic import BaseModel, Field


class ExaDigiTSettings(BaseModel):
    base_url: str = Field(default="http://localhost:8081")
    system: str = Field(default="idt4gdc_dc1")


class OpenDCIMSettings(BaseModel):
    base_url: str = Field(default="https://dcim.local/api/v1")
    user_id: str = Field(default="test")
    api_key: str = Field(default="0ff38b1e9b8052611d418c5cd6fe5ff0")
    verify_ssl: bool = Field(default=False)


class AppSettings(BaseModel):
    exadigit: ExaDigiTSettings = Field(default_factory=ExaDigiTSettings)
    opendcim: OpenDCIMSettings = Field(default_factory=OpenDCIMSettings)
    node_pdu_map_path: str = Field(default="app/mappings/node_to_pduid.json")
    poll_interval_s: int = Field(default=60)


def load_settings() -> AppSettings:
    return AppSettings(
        exadigit=ExaDigiTSettings(
            base_url=os.getenv("EXADIGIT_BASE_URL", "http://localhost:8081"),
            system=os.getenv("EXADIGIT_SYSTEM", "idt4gdc_dc1"),
        ),
        opendcim=OpenDCIMSettings(
            base_url=os.getenv("OPENDCIM_BASE_URL", "https://localhost:8080/api/v1"),
            user_id=os.getenv("OPENDCIM_USERID", "test"),
            api_key=os.getenv("OPENDCIM_APIKEY", "0ff38b1e9b8052611d418c5cd6fe5ff0"),
            verify_ssl=os.getenv("OPENDCIM_VERIFY_SSL", "false").lower() in ("1", "true", "yes"),
        ),
        node_pdu_map_path=os.getenv("NODE_PDUID_MAP", "app/mappings/node_to_pduid.json"),
        poll_interval_s=int(os.getenv("POLL_INTERVAL_S", "60")),
    )
