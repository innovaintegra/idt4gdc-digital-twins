# DT Bridge – ExaDigiT ↔ OpenDCIM

This project implements a **real-time bridge** between the **ExaDigiT Simulation Server** and **OpenDCIM**.

Its purpose is to:
- submit simulations to ExaDigiT,
- poll running simulations for power usage,
- translate simulated node-level power into **PDU-level measurements**, and
- push those measurements into OpenDCIM using its REST API.

The bridge enables live integration between digital twins within the iDT4GDC platform: OpenDCIM reflects simulated power behaviour as if it were real telemetry.

---

## Prerequisites

- Python ≥ 3.11
- A running ExaDigiT Simulation Server (default: `http://localhost:8081`)
- A running OpenDCIM instance (default: `https://localhost:8080/api/v1`)

---

## Directory structure

```text
bridge/
├── app/
│   ├── api.py                        # FastAPI application and route definitions
│   ├── config.py                     # Settings loaded from environment variables
│   ├── models.py                     # Pydantic request/response schemas
│   ├── clients/
│   │   ├── exadigit.py               # HTTP client for ExaDigiT API
│   │   └── opendcim.py               # HTTP client for OpenDCIM REST API
│   ├── poller/
│   │   └── sim_poller.py             # Background polling thread
│   └── mappings/
│       └── node_to_pduid.json        # Node-to-PDU ID mapping
├── scripts/
│   └── run.sh                        # Development runner (uvicorn --reload)
└── pyproject.toml
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `EXADIGIT_BASE_URL` | `http://localhost:8081` | ExaDigiT Simulation Server base URL |
| `EXADIGIT_SYSTEM` | `idt4gdc_dc1` | System name passed when submitting simulations |
| `OPENDCIM_BASE_URL` | `https://localhost:8080/api/v1` | OpenDCIM REST API base URL |
| `OPENDCIM_USERID` | `test` | OpenDCIM user ID for API authentication |
| `OPENDCIM_APIKEY` | *(see `.env.example`)* | OpenDCIM API key for API authentication |
| `OPENDCIM_VERIFY_SSL` | `false` | Whether to verify TLS certificates when calling OpenDCIM |
| `BRIDGE_PORT` | `8000` | Port the bridge listens on |
| `POLL_INTERVAL_S` | `60` | Polling interval in seconds |
| `NODE_PDUID_MAP` | `app/mappings/node_to_pduid.json` | Path to the node-to-PDU mapping JSON file |

---

## Running the bridge

Install dependencies and run in development mode with hot reload:

```bash
cd bridge
pip install -e .
source ../.env
./scripts/run.sh
```

The bridge is also runnable directly:

```bash
cd bridge
export PYTHONPATH="$(pwd)"
uvicorn app.api:app --reload --host 0.0.0.0 --port "${BRIDGE_PORT:-8000}"
```

Or via the installed entry point:

```bash
dt-bridge
```

---

## How it works

1. A client submits a `POST /simulate` request to the bridge.
2. The bridge forwards the simulation payload to ExaDigiT and registers the returned simulation ID.
3. A background polling thread wakes every `POLL_INTERVAL_S` seconds and, for each registered simulation:
   - Fetches all running jobs from ExaDigiT (`GET /simulation/{id}/scheduler/jobs`).
   - Retrieves the power history for each running job (`GET /simulation/{id}/scheduler/jobs/{job_id}/power-history`).
   - Takes the most recent power reading and divides it equally across the job's assigned nodes.
   - Aggregates per-node watts to per-PDU watts using `node_to_pduid.json`.
   - Posts one `PduStat` record per PDU to OpenDCIM (`POST /pdustats`).
4. The client calls `DELETE /simulate/{sim_id}` to stop the simulation and cancel polling.

If a node has no entry in `node_to_pduid.json` the poller raises a `KeyError` and logs the error — the simulation remains registered and polling continues on the next tick.

---

## API reference

### `POST /simulate`

Submit a simulation and begin polling.

**Request body:**
```json
{
  "system": "idt4gdc_dc1",
  "start_time": "2024-01-01T00:00:00",
  "end_time": "2024-01-01T01:00:00",
  "jobs": [
    {
      "job_id": "job_001",
      "submit_time_s": 0,
      "runtime_s": 3600,
      "nodes": 16,
      "gpus": 0
    }
  ]
}
```

**Response:**
```json
{
  "sim_id": "abc123",
  "poll_interval_s": 60
}
```

### `DELETE /simulate/{sim_id}`

Stop polling for the given simulation.

**Response:**
```json
{ "ok": true, "sim_id": "abc123" }
```

---

## Node → PDU mapping

The bridge requires an explicit mapping from ExaDigiT node IDs to OpenDCIM PDU IDs. The mapping file is a flat JSON object:

```json
{
  "node0000": 161,
  "node0001": 161,
  "node0256": 162,
  "node0257": 162
}
```

The default mapping at `app/mappings/node_to_pduid.json` covers 512 nodes (`node0000`–`node0511`) across two PDUs (IDs 161 and 162), corresponding to the two-rack `idt4gdc_dc1` system configuration.

To use a different data centre topology, replace this file (or point `NODE_PDUID_MAP` to an alternative path) so the node IDs and PDU IDs match those configured in OpenDCIM.
