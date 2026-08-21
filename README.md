# iDT4GDC – Digital Twin Deployments

This repository contains the **reference digital twin deployments** used within the iDT4GDC project.
It brings together multiple complementary digital-twin platforms to support modelling, simulation, and integration experiments across the project work packages.

The focus is on **reproducible, containerised deployments** that can be instantiated locally or in shared environments for evaluation and integration.

---

## Contents

```text
idt4gdc-digital-twins/
├── bridge/       # DT Bridge – real-time ExaDigiT ↔ OpenDCIM integration service
├── exadigit/     # ExaDigiT Simulation Server deployment
├── opendcim/     # OpenDCIM infrastructure digital twin deployment
├── .env.example  # All environment variable defaults
└── README.md
```

---

## System overview

The three components work together as follows:

```
ExaDigiT Simulation Server  (port 8081)
         │
         │  REST API (submit jobs, poll power history)
         ▼
     DT Bridge               (port 8000)
         │
         │  REST API (POST /pdustats)
         ▼
       OpenDCIM               (port 443 via Nginx)
         │
         │  OIDC authentication
         ▼
       Keycloak               (port 8080)
```

- **ExaDigiT** models workload execution, energy consumption, and scheduling behaviour.
- **OpenDCIM** models the physical and logical infrastructure of a data centre.
- **DT Bridge** polls ExaDigiT for simulated power data and pushes it into OpenDCIM as live PDU statistics, making simulated power behaviour visible as if it were real telemetry.

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2
- Git (for cloning the upstream ExaDigiT repository)
- `openssl` (for generating TLS certificates for OpenDCIM)
- Python ≥ 3.11 (for running the DT Bridge locally)

---

## Environment setup

Copy `.env.example` to `.env` at the repository root and fill in any secrets before starting any component:

```bash
cp .env.example .env
```

All scripts and the bridge read configuration from environment variables. Source the file before running scripts:

```bash
source .env
```

---

## First-time setup order

The three components are independent but the bridge depends on both being reachable. Suggested order:

1. Start ExaDigiT — see [`exadigit/README.md`](exadigit/README.md)
2. Start and initialise OpenDCIM — see [`opendcim/README.md`](opendcim/README.md)
3. Start the DT Bridge — see [`bridge/README.md`](bridge/README.md)

---

## Digital twin platforms

### ExaDigiT

The `exadigit/` directory contains the deployment of the ExaDigiT Simulation Server, used to model workload execution, energy consumption, and scheduling behaviour.

See [`exadigit/README.md`](exadigit/README.md)

### OpenDCIM

The `opendcim/` directory contains the deployment of OpenDCIM, used to model the physical and logical infrastructure of a data centre.

See [`opendcim/README.md`](opendcim/README.md)

### DT Bridge

The `bridge/` directory contains the integration service that connects ExaDigiT and OpenDCIM in real time.

See [`bridge/README.md`](bridge/README.md)

---

## Demonstration sites

Four sibling data centre systems are modelled (`idt4gdc_dc1`–`idt4gdc_dc4`), each mapped to one of the project's four demonstration sites. Sites are matched to systems by scale, reflecting real-world siting constraints on land, power, and cooling for each location:

| System | Demonstration site | Scale | Rationale                                                                                                                                             |
|---|---|---|-------------------------------------------------------------------------------------------------------------------------------------------------------|
| `idt4gdc_dc3` | Reading | 1792 nodes / 56 racks (largest) | Thames Valley / M4 corridor - the UK's principal commercial data-centre cluster, with the power and land availability to support the large-scale site |
| `idt4gdc_dc1` | Edinburgh | 512 nodes / 16 racks | Established secondary hub with renewable power and a cooler climate, supporting a medium-large site                                                   |
| `idt4gdc_dc2` | London | 384 nodes / 12 racks | Land and grid capacity within the city constrain the footprint to small-medium site                                                                   |
| `idt4gdc_dc4` | Peterborough | 192 nodes / 6 racks (smallest) | Smaller regional/edge-scale site                                                                                                                      |
