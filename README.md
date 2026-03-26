# Backfill Tooling

A suite of Python tools for processing building telemetry data and running backfill operations.

## Tools

| Script | Purpose |
|--------|---------|
| `mango_reformatter.py` | Pre-processes raw Mango CSV exports into the flat format required by `backfill_data_formater.py` |
| `multi_sheet_splitter.py` | Splits multi-sheet Excel files (already in flat format) into individual CSVs. If you were to put 5 or 6 different meters in one sheet all in different tabs. |
| `backfill_data_formater.py` | Pivots flat telemetry data, formats timestamps, generates unit files, and produces backfill run commands. Bread and butter of the tool. |
| `carson_delete.py` | Runs backfill **delete** operations for one or more meters |

---
## Typical Workflow

### 1. backfill_data_formater.py  (direct)

```
backfill_data_formater.py
```

This tool has the ability to generate a template with each column that should be fill in. 


### 2. Mango data pathway (common)

```
mango_reformatter.py  →  backfill_data_formater.py
```

If your source data is a raw Mango CSV export, run `mango_reformatter.py` first. It:
1. Strips `_rendered` columns
2. Renames measurement columns (removes meter-name prefix, e.g. `meter - kW` → `kW`) You may need to adjust the pointNames to Mango format `power_sensor` for `kW`.
3. Converts timestamps to Pacific timezone and applies the 15-minute BixBox offset
4. Restructures into the flat `building / device / timestamp / pointName / value` format

The output CSV is then ready to pass directly into `backfill_data_formater.py`.

## backfill_data_formater.py

### Features

- **Multi-format Support**: Reads both CSV and XLSX/XLS files
- **Paired-column Detection**: Automatically converts `mango_reformatter.py` wide output (pointName1/value1...) to flat format
- **Data Pivoting**: Converts flat telemetry data into wide format by building and device
- **Timestamp Formatting**:
  - Localizes timestamps to America/Los_Angeles timezone
  - Handles daylight saving time
  - Applies 15-minute offset for BixBox aggregation compatibility (skipped if timestamps are already timezone-aware)
- **Unit Mapping**: Automatically generates unit files mapping field names to engineering units
- **Run Command Generation**: Writes a ready-to-use blaze backfill command for each output folder
- **Validation**: Warns about unrecognized field names
- **Comma-Free Output**: Ensures numeric values are formatted without thousands separators
- **Flexible Usage**: Command-line or interactive mode

### Requirements

- Python 3.7+
- pandas
- openpyxl
- pytz

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/backfill_tooling.git
cd backfill_tooling
```

2. Install dependencies:
```bash
pip install pandas openpyxl
```

## Input File Format

The input file must be a flat CSV or XLSX file with the following columns:

| Column | Required | Description |
|--------|----------|-------------|
| `building` | Yes | Building identifier (e.g., "US-MTV-1708") |
| `device` | Yes | Device identifier (e.g., "MAIN_device") |
| `timestamp` | Yes | ISO8601 formatted timestamp |
| `pointName` | Yes | Measurement point name (e.g., "kW", "Temperature") |
| `value` | Yes | Numeric measurement value |
| `externalID` | No | External device numeric ID (corresponds to device_num_id) |

### Example Input (without externalID):
```csv
building,device,timestamp,pointName,value
US-MTV-1708,MAIN_device,2025-01-15T00:00:00,kW,125.5
US-MTV-1708,MAIN_device,2025-01-15T00:00:00,kW,72.3
US-MTV-1708,MAIN_device,2025-01-15T00:15:00,kW,130.2
```

### Example Input (with externalID):
```csv
building,device,externalID,timestamp,pointName,value
US-MTV-1708,MAIN_device,12345,2025-01-15T00:00:00,kW,125.5
US-MTV-1708,MAIN_device,12345,2025-01-15T00:00:00,kW,72.3
US-MTV-1708,MAIN_device,67890,2025-02-01T00:00:00,kW,130.2
```

**Note:** When the `externalID` column is present, data will be automatically split and grouped by unique combinations of (building, device, externalID). The externalID value will be used to populate the `device_num_id` parameter in the generated run_command.txt files.

## Usage

### Command-Line Mode

**Basic usage (outputs to current directory):**
```bash
python backfill_data_formater.py -i path/to/data.xlsx
```

**Specify output directory:**
```bash
python backfill_data_formater.py -i path/to/data.xlsx -o path/to/output
```

**View help:**
```bash
python backfill_data_formater.py --help
```

### Interactive Mode

Run without arguments to enter interactive mode:
```bash
python backfill_data_formater.py
```

You will be prompted to choose from:
- **0** — Generate a blank template CSV with an example row
- **1** — Process a single file
- **2** — Process all files in a directory
- **3** — Process multiple individual files
- **4** — Run a backfill command for an already-processed output folder
- **5** — Run backfill commands for all folders in a directory

Type `quit` at any prompt to exit, or `reset` to start over.

## Output Structure

The tool creates a folder for each building/device combination with the date range:

```
{output_directory}/
└── {building}_{device}_{start_date}_{end_date}/
    ├── backfill_log.log
    ├── {building}_{device}.csv
    ├── {building}_{device}_units.csv
    └── run_command.txt
```

### Output Files

**1. Data CSV** (`{building}_{device}.csv`)
- Pivoted telemetry data with timestamps
- Columns: `timestamp`, followed by all point names (Current, kW, Temperature, etc.)
- Numeric values formatted without commas
- Timestamps in Pacific timezone

**2. Units CSV** (`{building}_{device}_units.csv`)
- Maps field names to engineering units
- Columns: `Device Id`, `Field Name`, `Units`

**3. Run Command** (`run_command.txt`)
- Ready-to-use blaze backfill populate command with all required flags pre-filled
- Labeled `-- MANGO --` or `-- BITBOX --` based on detected point names
- Can be run directly or via interactive option 4/5

**4. Log File** (`backfill_log.log`)
- Processing details
- Date ranges
- Warnings about unrecognized fields
- File paths

### Example Output:

```
./US-MTV-1708_MAIN_device_2025-01-15_2025-01-31/
├── backfill_log.log
├── US-MTV-1708_MAIN_device.csv
└── US-MTV-1708_MAIN_device_units.csv
```

## Supported Measurement Points

The tool recognizes and maps units for the following measurement types:

### Electrical Measurements
- Current (A, B, C phases): amperes
- Voltage (AB, AN, BC, BN, CA, CN, LL, LN): volts
- Power Factor (total, A, B, C): no-units
- Power (kW, kVA, kVAR): kilowatts, kilovolt-amperes, kilovolt-amperes-reactive
- Energy (kWh): kilowatt-hours
- Frequency: hertz

### HVAC & Utilities
- Temperature: degrees-fahrenheit
- Gas Flow: cubic-feet-per-hour, cubic-feet
- Water Flow: us-gallons-per-minute, us-gallons

**Note**: Unrecognized field names will trigger a warning but will still be processed.


# Carson Delete Tool (`carson_delete.py`)

A tool for running backfill **delete** operations on one or more meters. It generates the required timestamp data files, builds the blaze delete command, shows a review before execution, and writes per-entry summary files with the results.

## Features

- **Three input modes**: batch CSV/XLSX file, single one-off entry via CLI flags, or interactive prompts
- **Template generation**: generates a blank batch CSV with an example row (interactive mode, option 0)
- **Two-phase execution**: files are generated and reviewed before any commands are run
- **Timezone-aware timestamps**: all dates are localized to America/Los_Angeles
- **Per-entry output folders**: each entry gets its own subfolder with a data file, run command reference, and summary
- **Reset / quit support**: type `reset` or `quit` at any interactive prompt

## Requirements

- Python 3.7+
- pandas
- openpyxl
- pytz

## Batch File Format

The batch input file must be CSV or XLSX with the following columns:

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

Before any delete commands run, the tool automatically executes the following environment setup in order:

1. `cd "$(p4 g4d backfill)"` — navigates into the backfill client root (`p4 g4d <client>` is the script-safe form of the `g4d` shell function; it outputs the client path)
2. `g4 sync` — syncs the client from within that directory (picks up `.g4config` automatically)

These run once after you confirm execution. If either command fails, the tool aborts and no blaze commands are run. The generated data files and run command files are preserved in the output directory.

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

Prompts you to choose:
- **0** — Generate a blank template CSV
- **1** — Load a batch CSV/XLSX file
- **2** — Enter a single meter manually
- **3** — Run environment setup only (`p4 g4d -f backfill && g4 sync`) — useful for testing prerequisites without running any deletes
- **4** — Combine all `*_delete_summary.txt` files in a directory into one consolidated report

### View help
```bash
python carson_delete.py --help
```

## Output Structure

Each entry gets its own subfolder inside the output directory:

```
{output_dir}/
└── {building_id}_{meter_name}_{start_date}_{end_date}/
    ├── {building_id}_{meter_name}.csv                   # 2-row timestamp data file
    ├── {building_id}_{meter_name}_run_command.txt        # Pre-execution reference
    └── {building_id}_{meter_name}_delete_summary.txt    # Post-execution result
```

### Output Files

**Data CSV** (`{building_id}_{meter_name}.csv`)
- Two-row file with `timestamp` header, start timestamp, and end timestamp
- Timestamps formatted as `YYYY-MM-DD HH:MM:SS±HH:MM` (America/Los_Angeles)

**Run Command file** (`_run_command.txt`)
- Written before execution; contains all entry details and the full blaze command
- Useful for manual review or re-running outside the tool

**Summary file** (`_delete_summary.txt`)
- Written after execution; contains status (SUCCESS / FAILED), the command, and output
- On success: shows the extracted `bt delete` lines from blaze output
- On failure: shows full stdout/stderr for debugging

**Combined summary** (generated via interactive option 4)
- Recursively finds all `*_delete_summary.txt` files under a chosen directory
- Writes a single `combined_delete_summary.txt` (or custom filename) with a header showing total entry count and generation timestamp, followed by each individual summary in alphabetical order

## Troubleshooting

### Error: Missing required columns
- Ensure the batch file has all five columns: `building_id`, `meter_name`, `external_id`, `start_date`, `end_date`

### Error: Cannot parse date
- Use `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` format for dates

### Command exits non-zero
- Check the `_delete_summary.txt` file in the entry's subfolder for full blaze output


