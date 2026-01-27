# DT Bridge – ExaDigiT ↔ OpenDCIM

This project implements a **real-time bridge** between the **ExaDigiT Simulation Server** and **OpenDCIM**.

Its purpose is to:
- submit simulations to ExaDigiT,
- poll running simulations for power usage,
- translate simulated node-level power into **PDU-level measurements**, and
- push those measurements into OpenDCIM using its REST API.

The bridge enables the integration between digital twins within the iDT4GDC platform.

---

## What the bridge does

At a high level:

1. A client submits a simulation request to the bridge
2. The bridge forwards the request to ExaDigiT
3. While the simulation is running:
   - job power data is polled periodically
   - power is distributed across assigned compute nodes
   - nodes are mapped to PDUs
4. Aggregated PDU power readings are posted to OpenDCIM
5. OpenDCIM stores the data as live PDU statistics

This allows OpenDCIM to reflect simulated power behaviour as if it were real telemetry.

---

## Node → PDU mapping

The bridge requires an explicit mapping between ExaDigiT node IDs and OpenDCIM PDUIDs.

Example ```dt_bridge/mappings/node_to_pduid.json```.

---

## Running the bridge
Development mode
```shell
source .env
./scripts/run_dev.sh
```

Runs with hot reload using Uvicorn.

---

## API endpoints
POST /simulate

Submit a real-time simulation and start polling.

Returns:

simulation ID

polling interval