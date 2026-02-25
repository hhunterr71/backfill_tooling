# Backfill Data Formatter

A Python tool for processing and formatting building telemetry data from CSV and Excel files. This tool pivots flat telemetry data, formats timestamps, and generates unit mapping files for backfill operations.

## Features

- **Multi-format Support**: Reads both CSV and XLSX/XLS files
- **Data Pivoting**: Converts flat telemetry data into wide format by building and device
- **Timestamp Formatting**:
  - Localizes timestamps to America/Los_Angeles timezone
  - Handles daylight saving time
  - Applies 15-minute offset for BixBox aggregation compatibility
- **Unit Mapping**: Automatically generates unit files mapping field names to engineering units
- **Validation**: Warns about unrecognized field names
- **Comma-Free Output**: Ensures numeric values are formatted without thousands separators
- **Flexible Usage**: Command-line or interactive mode

## Requirements

- Python 3.7+
- pandas
- openpyxl

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
US-MTV-1708,MAIN_device,2025-01-15T00:00:00,Temperature,72.3
US-MTV-1708,MAIN_device,2025-01-15T00:15:00,kW,130.2
```

### Example Input (with externalID):
```csv
building,device,externalID,timestamp,pointName,value
US-MTV-1708,MAIN_device,12345,2025-01-15T00:00:00,kW,125.5
US-MTV-1708,MAIN_device,12345,2025-01-15T00:00:00,Temperature,72.3
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

The script will prompt you for:
1. Input file path (CSV or XLSX)

Output will be saved to the current working directory.

## Output Structure

The tool creates a folder for each building/device combination with the date range:

```
{output_directory}/
└── {building}_{device}_{start_date}_{end_date}/
    ├── backfill_log.log
    ├── {building}_{device}.csv
    └── {building}_{device}_units.csv
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

**3. Log File** (`backfill_log.log`)
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

## Examples

### Example 1: Process a single CSV file
```bash
python backfill_data_formater.py -i building_data.csv
```

**Output:**
```
============================================================
Backfill Data Formatter - Single File Processor
Supports: CSV and XLSX files
============================================================

Valid CSV file detected: building_data.csv
Output directory: C:\Users\YourName\Documents

Processing file: building_data.csv
This may take a moment...

============================================================
Processing complete!
Output saved to: C:\Users\YourName\Documents/
Folder structure: {building}_{device}_{start-date}_{end-date}/
============================================================
```

### Example 2: Process Excel file with custom output
```bash
python backfill_data_formater.py -i telemetry.xlsx -o C:/backfill_outputs
```

### Example 3: Interactive mode
```bash
python backfill_data_formater.py

============================================================
Backfill Data Formatter - Single File Processor
Supports: CSV and XLSX files
============================================================

Interactive Mode
(Use --help to see command-line options)

Enter path to CSV or XLSX file: data.xlsx
Valid XLSX file detected: data.xlsx
Output directory: C:\current\directory
...
```

## Troubleshooting

### Error: File must be .csv or .xlsx format
- Ensure your file has the correct extension (.csv, .xlsx, or .xls)
- The file must exist at the specified path

### Error: Path is a directory
- This tool processes single files only
- Provide a path to a specific file, not a folder

### Warning: Unrecognized field names
- The tool will still process the data
- Review the warning to ensure field names are spelled correctly
- Add custom unit mappings by editing the `unit_df` DataFrame in the script (line 15)

### Empty or incorrect output
- Verify your input file has required columns: `building`, `device`, `timestamp`, `pointName`, `value`
- Optional column: `externalID` (if present, data will be grouped by it)
- Check that timestamps are in ISO8601 format
- Ensure numeric values don't have unexpected formatting

### Multiple output folders for same building/device
- This is expected if your data includes the `externalID` column with different values
- Each unique combination of (building, device, externalID) will create a separate output folder
- The `device_num_id` parameter in run_command.txt will be populated with the externalID value

---

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

1. `g4d -f backfill` — switches into the backfill client
2. `g4 sync` — syncs the client

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

## Troubleshooting

### Error: Missing required columns
- Ensure the batch file has all five columns: `building_id`, `meter_name`, `external_id`, `start_date`, `end_date`

### Error: Cannot parse date
- Use `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` format for dates

### Command exits non-zero
- Check the `_delete_summary.txt` file in the entry's subfolder for full blaze output

---

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

[Add your license here]

## Contact

[Add contact information here]
