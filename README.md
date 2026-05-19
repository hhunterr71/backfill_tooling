# Backfill Tooling

Two Python tools for processing building telemetry data and running backfill operations.

| Script | Purpose |
|--------|---------|
| `carson_backfill.py` | End-to-end job workflow: flatten raw Mango exports, combine and reformat data, generate backfill output folders, and run blaze backfill commands |
| `carson_delete.py` | Runs backfill **delete** operations for one or more meters |

---

## Requirements

- Python 3.7+
- pandas
- openpyxl
- pyyaml

```bash
pip install pandas openpyxl pyyaml
```

`standard_field_map.yaml` is auto-detected from a sibling `meter_onboard_tool/` repo at:
```
../meter_onboard_tool/mappings/standard_field_map.yaml
```
If the file is not found the tool continues without field renaming — a warning is printed.

---

# Carson Backfill Tool (`carson_backfill.py`)

A job-oriented tool that replaces the old `mango_reformatter.py` + `backfill_data_formater.py` two-step workflow.

## Job Workflow

```
S  →  1  →  2  →  3  →  4
Setup  Flatten  Combine  Process  Run
```

| Step | Menu | Action |
|------|------|--------|
| Setup | `S` | Creates job folder structure + mapping template CSV |
| Flatten | `1` | Renames raw Mango exports in `raw/` to a consistent naming scheme |
| Batch Combine | `2` | Reads mapping CSV, merges raw files per meter, applies field renaming, writes combined CSVs to `combined/` |
| Process | `3` | Pivots combined CSVs into per-device backfill output folders in `output/` |
| Run Backfill | `4` | Executes `run_command.txt` for every folder in `output/` via blaze |

### Job Folder Structure

Running `S` creates:

```
{job_name}/
├── raw/                              ← drop raw Mango CSV exports here
├── combined/                         ← step 2 output (combined CSVs + .meta sidecars)
├── output/                           ← step 3 output (per-device backfill folders)
└── {job_name}_mapping_template.csv   ← fill this in before running step 2
```

## Mapping CSV

The mapping CSV controls how raw files are combined and what metadata is attached.

| Column | Required | Description |
|--------|----------|-------------|
| `building_id` | Yes | Building identifier (e.g. `US-MTV-1708`) |
| `meter_name` | Yes | Meter device name — must match the prefix detected in raw file column headers |
| `external_id` | No | Device numeric ID (`device_num_id` in the blaze command) |
| `type` | No | Meter type: `EM`, `WM`, or `GM` — enables automatic field renaming and unit lookup via YAML |
| `bug_number` | No | Bug number used in the `admin_session --reason="b/{bug_number}"` command wrapper |
| `start_date` | Yes | Start of date window: `YYYY-MM-DD` |
| `end_date` | Yes | End of date window: `YYYY-MM-DD` |

### Example mapping CSV:

```csv
building_id,meter_name,external_id,type,bug_number,start_date,end_date
US-MTV-1708,power-meter-MAIN,1743694964149,EM,123456789,2025-01-01,2025-12-31
US-MTV-1708,utility-WM_01,1743694964150,WM,123456789,2025-01-01,2025-12-31
```

## Field Mapping (YAML integration)

When `type` is set and `standard_field_map.yaml` is found, step 2 automatically:

1. **Renames** raw Mango point names to standard field names (e.g. `kW` → `power_sensor`, `WaterFlowRate` → `water_flowrate_sensor`)
2. **Drops** noise columns listed under `IGNORE` (e.g. `Ping`, `Run_Time`, `Data_Stale`)
3. **Populates units** in the units CSV from the YAML `standard_unit` values

Unrecognized columns that are not in IGNORE are kept as-is with a warning.

## Output Structure

Step 3 writes one folder per device into `output/`:

```
output/
└── {building}_{device}_{start_date}_{end_date}/
    ├── {building}_{device}.csv        ← pivoted telemetry data
    ├── {building}_{device}_units.csv  ← field name → engineering unit mapping
    ├── run_command.txt                ← ready-to-run blaze command
    └── backfill_log.log               ← processing log with warnings
```

### run_command.txt format

```
admin_session --reason="b/{bug_number}" -- \
blaze run \
java/com/google/corp/bizapps/rews/datalake/tools/backfill:backfill_tool -- \
--mode="populate" --data_file="{data_file_path}" \
--unit_file="{unit_file_path}" --device_num_id={device_num_id} \
--environment=prod
```

## Usage

### Interactive mode

```bash
python carson_backfill.py
```

```
Choose mode:

  -- SETUP --
  S. Setup new job  (creates raw/ combined/ output/ + mapping template)

  -- JOB WORKFLOW --
  1. Flatten raw files           (rename files in raw/)
  2. Batch combine from mapping  (raw/ + mapping.csv -> combined/)
  3. Process combined files      (combined/ -> output/ backfill folders)
  4. Run backfill                (execute all output/ folders via blaze)
```

Type `quit` at any prompt to exit, `reset` to start over.

### Command-line mode

```bash
# Setup a new job
python carson_backfill.py --setup --job batch10

# Flatten raw files
python carson_backfill.py --flatten /path/to/job_root

# Batch combine (mapping CSV auto-detected from job root)
python carson_backfill.py --batch-combine /path/to/job_root

# Process combined files
python carson_backfill.py --process /path/to/job_root

# Run backfill
python carson_backfill.py --run /path/to/job_root
```

All step commands accept either the **job root** or the **specific subdirectory** (`raw/`, `combined/`, `output/`).

---

# Carson Delete Tool (`carson_delete.py`)

A tool for running backfill **delete** operations on one or more meters. Generates timestamp data files, builds the blaze delete command, shows a review before execution, and writes per-entry summary files.

## Features

- **Three input modes**: batch CSV/XLSX file, single one-off entry via CLI flags, or interactive prompts
- **Template generation**: generates a blank batch CSV with an example row (interactive mode, option 0)
- **Two-phase execution**: files are generated and reviewed before any commands are run
- **Timezone-aware timestamps**: all dates are localized to America/Los_Angeles
- **Per-entry output folders**: each entry gets its own subfolder with a data file, run command reference, and summary
- **Reset / quit support**: type `reset` or `quit` at any interactive prompt

## Batch File Format

| Column | Description |
|--------|-------------|
| `building_id` | Building identifier (e.g. `US-MFA-BV100`) |
| `meter_name` | Meter name (e.g. `utility-WM_01_BLDGDCW`) |
| `external_id` | Device numeric ID |
| `start_date` | Start date — `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` |
| `end_date` | End date — `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` |

### Example batch CSV:

```csv
building_id,meter_name,external_id,start_date,end_date
US-MFA-BV100,utility-WM_01_BLDGDCW,1743694964149,2025-06-30 00:00:00,2026-02-05 00:00:00
```

## Prerequisites

Before any delete commands run, the tool automatically executes:

1. `cd "$(p4 g4d backfill)"` — navigates into the backfill client root
2. `g4 sync` — syncs the client

These run once after you confirm execution. If either fails the tool aborts and no blaze commands are run.

## Usage

### Batch file mode
```bash
python carson_delete.py -i path/to/batch.csv
python carson_delete.py -i path/to/batch.xlsx -o path/to/output
```

### Single one-off entry
```bash
python carson_delete.py \
  --building_id US-MFA-BV100 \
  --meter_name utility-WM_01_BLDGDCW \
  --external_id 1743694964149 \
  --start_date "2025-06-30 00:00:00" \
  --end_date "2026-02-05 00:00:00"
```

### Interactive mode
```bash
python carson_delete.py
```

Options:
- **0** — Generate a blank template CSV
- **1** — Load a batch CSV/XLSX file
- **2** — Enter a single meter manually
- **3** — Run environment setup only (useful for testing prerequisites)
- **4** — Combine all `*_delete_summary.txt` files in a directory into one report

## Output Structure

```
{output_dir}/
└── {building_id}_{meter_name}_{start_date}_{end_date}/
    ├── {building_id}_{meter_name}.csv                   ← 2-row timestamp data file
    ├── {building_id}_{meter_name}_run_command.txt        ← pre-execution reference
    └── {building_id}_{meter_name}_delete_summary.txt    ← post-execution result
```

## Troubleshooting

**Missing required columns** — Ensure the batch file has all five columns: `building_id`, `meter_name`, `external_id`, `start_date`, `end_date`

**Cannot parse date** — Use `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` format

**Command exits non-zero** — Check `_delete_summary.txt` in the entry subfolder for full blaze output
