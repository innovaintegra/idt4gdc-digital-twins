# OpenDCIM Bulk Import – iDT4GDC Reference Data

This directory contains **bulk import CSV files** used to initialise a reference OpenDCIM model for the iDT4GDC project.

The import process must be performed in a **specific order** due to OpenDCIM’s internal dependencies (manufacturers, templates, locations, then devices).

---

## Prerequisites

Before importing any CSV files:

1. Log in to OpenDCIM as a **Site Administrator**
2. Ensure OpenDCIM is fully initialised and reachable via the web UI

---

## Step 1 – Create the `Generic` manufacturer

Device templates reference a manufacturer by name.  
OpenDCIM **does not auto-create manufacturers** during bulk import.

1. Navigate to:  
   **Management → Manufacturers**
2. Click **Create Manufacturer**
3. Create a manufacturer with:
   - **Name:** `Generic`
4. Save

> This manufacturer is used by all reference device templates in this directory.

---

## Step 2 – Import containers, data centres, and zones

These CSV files define the **top-level physical structure**.

1. Navigate to:  
   **Bulk Operations → Import Data Centers / Containers / Zones**
2. Upload `idt4gdc_container.csv`
3. Map fields as required
4. Import

This creates:
- Container: `idt4gdc`
- Data centres: `idt4gdc_dc1`, `idt4gdc_dc2`
- Zone: `Compute Room`

---

## Step 3 – Import device templates

Device templates must exist **before** importing devices.

1. Navigate to:  
   **Bulk Operations → Import Device Templates**
2. Upload `idt4gdc_device_templates.csv`
3. Ensure columns are mapped correctly
4. Import

Templates included (all under manufacturer `Generic`):

| Model | Height | Type | Nominal watts |
|---|---|---|---|
| 1U Switch | 1U | Switch | 150 W |
| 1U Server | 1U | Server | 300 W |
| 2U Server | 2U | Server | 600 W |
| 4U GPU Server | 4U | Server | 2000 W |

---

## Step 4 – Import cabinets

Cabinets depend on:
- Data centre existence
- Zone existence

1. Navigate to:  
   **Bulk Operations → Import Cabinets**
2. Upload `idt4gdc_dc1_cabinets.csv`
3. Ensure **Row is left unmapped** unless rows were created explicitly
4. Import

The file defines 42U cabinets named `DC1-R1-01`, `DC1-R1-02`, etc. in the `idt4gdc_dc1` data centre.

> Rows are optional. Leaving them blank avoids `(Data Center + Row)` uniqueness errors.

---

## Step 5 – Import devices

Devices reference:
- Existing cabinets
- Existing device templates (Manufacturer + Model)

1. Navigate to:  
   **Bulk Operations → Import New Devices**
2. Upload `idt4gdc_dc1_devices.csv`
3. Map fields carefully (cabinet, position, height, model, etc.)
4. Import

The file places switches, management servers, compute servers, and GPU servers into the cabinets defined in the previous step. Device types are tagged (`compute,cpu`, `compute,gpu`, `network,switch`, `mgmt`) for filtering in the UI.

---

## Step 6 – Create PDUs

PDUs are **not bulk-imported** in this reference setup and must be created manually.

1. Navigate to:  
   **Power → Power Distribution Units**
2. Create PDUs per cabinet or per row as required
3. Associate PDUs with:
   - Cabinets
   - Power paths
   - Voltage / breaker settings as appropriate

> PDUs are required for accurate power modelling and integration with ExaDigiT / RAPS workflows.

---

## Recommended import order (summary)

1. Manufacturer (`Generic`)
2. Containers / Data Centres / Zones
3. Device Templates
4. Cabinets
5. Devices
6. PDUs (manual)

---

## Purpose

These reference imports provide:
- A minimal but realistic data-centre model
- Consistent structure across iDT4GDC experiments
- A clean baseline for integration with ExaDigiT simulations

They are not intended to represent a full production facility.
