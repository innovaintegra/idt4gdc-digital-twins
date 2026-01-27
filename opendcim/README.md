# OpenDCIM – iDT4GDC

This directory contains the **OpenDCIM deployment** used within the iDT4GDC digital-twin platform.  
It provides an asset-centric digital twin of data-centre infrastructure and integrates with Keycloak for authentication.

The deployment is fully containerised using **Docker Compose** and includes:
- OpenDCIM (web application)
- MariaDB (configuration database)
- Nginx (HTTPS reverse proxy)
- Keycloak (OIDC identity provider)

---

## Directory structure

```text
opendcim/
├── code/                    
│   ├── docker-compose.yml
│   ├── nginx/
│   │   ├── certs/
│   │   └── conf.d/
│   │       └── opendcim.conf
│   └── keycloak/
│       └── opendcim.json
│
├── data/
│   ├── README.md
│   └── *.csv
│
├── scripts/
│   ├── opendcim_generate_nginx_certs.sh
│   ├── opendcim_up.sh
│   ├── opendcim_down.sh
│   ├── opendcim_init_oidc_params.sh
│   └── opendcim_promote_site_admin.sh
│
└── README.md
```

## Step 1 – Generate TLS certificates

OpenDCIM is exposed via HTTPS using Nginx.
A self-signed certificate is generated for local development.

From the opendcim/ directory:

```./scripts/opendcim_generate_nginx_certs.sh```


This generates:

```
code/nginx/certs/localhost.crt
code/nginx/certs/localhost.key
```

## Step 2 – Start OpenDCIM

Start the full OpenDCIM stack:

``./scripts/opendcim_run.sh``


This will start the necessary containers:

- MariaDB
- OpenDCIM
- Nginx (HTTPS)
- Keycloak

You can verify they are running with:

``docker ps``

## Step 4 – Initialise OIDC configuration and admin user

Once the Keycloak container is running, initialise OpenDCIM’s database configuration.

```./scripts/opendcim_init_oidc_params.sh```

This script configures OpenDCIM OIDC parameters (fac_Config) to redirect users to keycloak for authentication.

## Step 5 - Log-in to OpenDCIM

Navigate to ```https://localhost``` and log-in as the test user:
```
username: test
password: password
```
This registers the user in OpenDCIM.
From here run:
```./scripts/opendcim_promote_site_admin.sh```

To elevate the newly registered user to Site Administrator and optionally sets an API key for automation.


## Step 5 – Bulk import reference data

Reference infrastructure data is provided in ```opendcim/data/```.

Follow the instructions in:

```opendcim/data/README.md```

This establishes a minimal but realistic OpenDCIM model for iDT4GDC experiments.

## Stopping OpenDCIM

To stop the OpenDCIM stack:

```./scripts/opendcim_stop.sh```

This cleanly shuts down all containers without deleting data volumes.