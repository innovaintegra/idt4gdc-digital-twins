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
