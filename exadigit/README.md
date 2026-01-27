# ExaDigiT – Simulation Server Integration

This directory contains the **ExaDigiT Simulation Server integration** used within the iDT4GDC digital-twin platform.  
The structure is intentionally split to keep **upstream code clean** while allowing **local customisation and reproducibility**.

---

## Directory structure

```text
exadigit/
├── code/
│
├── exadigit/
│   ├── raps/
│   ├── simulation_server/
│   └── docker-compose.yml
│
└── scripts/
    ├── exadigit_simserver_build.sh
    ├── exadigit_simserver_up.sh
    └── exadigit_simserver_down.sh
```

### code/

Contains clones of the official ExaDigiT repositories which is managed automatically by the supplied script.

###  exadigit/

Contains all iDT4GDC-specific changes, including: custom RAPS configurations and modified simulation server logic. These 
files are mounted into containers at runtime using Docker bind mounts.

### scripts/

Provides wrappers around the upstream stack:

- exadigit_simserver_build.sh – clone/update upstream repo
- exadigit_simserver_up.sh – install custom compose and start stack
- exadigit_simserver_down.sh – stop the stack cleanly

## Service ports

The ExaDigiT Simulation Server stack exposes the following services on the host system:

| Port      | Service                   | Description                                                                 |
|-----------|---------------------------|-----------------------------------------------------------------------------|
| 8090      | Simulation Dashboard (UI) | Web-based user interface used to submit, manage, and track simulation runs. |
| 8081      | Simulation Server API     | REST API used to control simulations and retrieve results.                  |
| 8081/docs | API documentation         | Interactive OpenAPI/Swagger documentation for the Simulation Server.        |
