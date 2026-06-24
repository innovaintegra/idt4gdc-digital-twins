# ExaDigiT – Simulation Server Integration

This directory contains the **ExaDigiT Simulation Server integration** used within the iDT4GDC digital-twin platform.
The structure is intentionally split to keep **upstream code clean** while allowing **local customisation and reproducibility**.

---

## Prerequisites

- Docker and Docker Compose v2
- Git (the build script clones the upstream ExaDigiT repository)
- Environment variables from the root `.env` file — source it before running scripts:
  ```bash
  source ../.env
  ```

---

## Directory structure

```text
exadigit/
├── code/                              # Auto-cloned upstream ExaDigiT repo (do not edit)
│
├── exadigit/                          # iDT4GDC-specific overrides (bind-mounted at runtime)
│   ├── docker-compose.yml             # Replaces upstream compose; wires all bind mounts
│   ├── raps/
│   │   ├── config/
│   │   │   └── idt4gdc_dc1.yaml       # System topology, power params, scheduler config
│   │   └── raps/
│   │       ├── dataloaders/
│   │       │   └── idt4gdc_dc1.py     # RAPS-level data loader
│   │       ├── engine.py              # Power/scheduler engine overrides
│   │       └── sim_config.py          # Configuration customisations
│   └── simulation_server/
│       ├── models/
│       │   └── sim.py                 # Custom simulation model
│       └── simulation/
│           ├── simulation.py          # Core simulation orchestration
│           └── dataloaders/
│               ├── idt4gdc_dc1.py     # Simulation-level data loader
│               └── inline_jobs.py     # Job submission logic
│
└── scripts/
    ├── exadigit_simserver_build.sh    # Clone/update upstream repo at SIMSERVER_REF
    ├── exadigit_simserver_run.sh      # Install custom compose and start stack
    └── exadigit_simserver_down.sh     # Stop the stack cleanly
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SIMSERVER_REF` | `main` | Git branch, tag, or commit SHA to check out from the upstream repo |
| `SIMSERVER_REPO_URL` | `https://code.ornl.gov/exadigit/simulationserver.git` | Upstream repository URL |
| `SIMSERVER_HOST_PORT` | `8090` | Host port for the Simulation Dashboard UI |

---

## Usage

### Step 1 – Build (clone upstream)

```bash
./scripts/exadigit_simserver_build.sh
```

This script:
- Clones the upstream ExaDigiT repository into `code/` if it does not already exist.
- Fetches all tags and branches from upstream.
- Checks out the ref specified by `SIMSERVER_REF`.
- Runs `git submodule update --init --recursive` to pull the dashboard and RAPS submodules.

Re-running this script is safe — it will fast-forward `code/` to the latest state of `SIMSERVER_REF`.

### Step 2 – Run

```bash
./scripts/exadigit_simserver_run.sh
```

This script:
- Copies `exadigit/docker-compose.yml` over the upstream `code/docker-compose.yml`, replacing it with the iDT4GDC-customised version.
- Runs `docker compose up -d --build` from inside `code/`.

### Step 3 – Stop

```bash
./scripts/exadigit_simserver_down.sh
```

---

## Service ports

| Port | Service | Description |
|---|---|---|
| `8090` | Simulation Dashboard (UI) | Web-based interface for submitting, managing, and tracking simulations |
| `8081` | Simulation Server API | REST API for controlling simulations and retrieving results |
| `8081/docs` | API documentation | Interactive OpenAPI/Swagger docs for the Simulation Server |

---

## Customisation approach

Upstream code in `code/` is **never edited directly**. All iDT4GDC-specific changes live in `exadigit/` and are injected at container startup via Docker **bind mounts** defined in `exadigit/docker-compose.yml`.

This means:
- The upstream repo can be updated simply by changing `SIMSERVER_REF` and re-running the build script.
- All iDT4GDC-specific behaviour is confined to the `exadigit/` subdirectory and is easy to diff against upstream.

### Override files and their purpose

| File | Purpose |
|---|---|
| `raps/config/idt4gdc_dc1.yaml` | Defines the simulated system: 1 CDU, 2 racks, 512 nodes (256 per rack), per-component power parameters, cooling model, and scheduler behaviour |
| `raps/raps/dataloaders/idt4gdc_dc1.py` | Loads iDT4GDC-specific workload and system data into the RAPS engine |
| `raps/raps/engine.py` | Overrides the upstream power and scheduling engine logic |
| `raps/raps/sim_config.py` | Extends or overrides upstream simulation configuration classes |
| `simulation_server/models/sim.py` | Custom simulation data model exposed via the API |
| `simulation_server/simulation/simulation.py` | Core simulation orchestration logic |
| `simulation_server/simulation/dataloaders/idt4gdc_dc1.py` | Simulation-level data loading for the iDT4GDC system |
| `simulation_server/simulation/dataloaders/inline_jobs.py` | Handles job submission from the bridge's inline job format |

### Key system parameters (`idt4gdc_dc1.yaml`)

| Parameter | Value |
|---|---|
| CDUs | 1 |
| Racks per CDU | 4 |
| Nodes per rack | 128 (512 total) |
| CPU idle power | 90 W |
| CPU max power | 280 W |
| Memory power | 74.26 W |
| NIC power | 20 W |
| Switch power | 250 W |
| PUE (idle / full load) | 1.25 / 1.10 |
| Carbon intensity | 0.233 kgCO₂/kWh |
| Polling quanta | 15 s |
| Max nodes per job | 512 |

---

## Running an inline-job simulation

The simulation server supports submitting an explicit list of jobs directly via the REST API, bypassing any workload replay files. This is the mechanism used by the DT Bridge.

### Request

```bash
curl -s -X POST http://localhost:8081/simulation/run \
  -H "Content-Type: application/json" \
  -d '{
    "system":   "idt4gdc_dc1",
    "start":    "2026-06-24T12:00:00Z",
    "end":      "2026-06-24T12:05:00Z",
    "replay":   true,
    "cooling":  true,
    "realtime": true,
    "jobs": [
      {
        "job_id":       "job-1",
        "submit_time_s": 0,
        "runtime_s":    120,
        "nodes":        4,
        "gpus":         0
      },
      {
        "job_id":       "job-2",
        "submit_time_s": 0,
        "runtime_s":    180,
        "nodes":        8,
        "gpus":         0
      }
    ]
  }'
```

Field reference:

| Field | Required | Description |
|---|---|---|
| `system` | Yes | Must match a config name in `raps/config/` — use `idt4gdc_dc1` |
| `start` / `end` | Yes | ISO 8601 UTC timestamps defining the simulation window |
| `replay` | Yes | Must be `true` to activate the inline-jobs dataloader |
| `cooling` | No | Enable the `SimpleCoolingModel` (PUE, CDU heat balance) |
| `realtime` | No | Run at 1× wall-clock speed; omit or set `false` for max-speed |
| `jobs[].job_id` | Yes | Unique identifier string for this job |
| `jobs[].submit_time_s` | Yes | Seconds from `start` at which the job enters the queue (≥ 0) |
| `jobs[].runtime_s` | Yes | Expected job duration in seconds (> 0) |
| `jobs[].nodes` | Yes | Number of nodes required (1 – 512) |
| `jobs[].gpus` | No | GPU count per node; leave 0 for this CPU-only system |

> **Note on `submit_time_s`:** jobs with `submit_time_s = 0` are pre-placed before the simulation clock starts and are visible immediately on the first API poll. Jobs with a non-zero `submit_time_s` only appear in results after that many real seconds have elapsed.

### Response

```json
{
  "sim_id": "abc123xyz"
}
```

### Polling job state

```bash
# List all jobs and their current state
curl -s http://localhost:8081/simulation/abc123xyz/scheduler/jobs | python -m json.tool

# Get power history for a specific job
curl -s http://localhost:8081/simulation/abc123xyz/scheduler/jobs/job-1/power-history | python -m json.tool
```

Job states returned: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, `TIMEOUT`, `NODE_FAIL`.

Power history entries are recorded every `power_update_freq` seconds (15 s by default) and report watts per job at that instant.
