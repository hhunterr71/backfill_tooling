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

| Column | Description |
|--------|-------------|
| `building` | Building identifier (e.g., "US-MTV-1708") |
| `device` | Device identifier (e.g., "MAIN_device") |
| `timestamp` | ISO8601 formatted timestamp |
| `pointName` | Measurement point name (e.g., "kW", "Temperature") |
| `value` | Numeric measurement value |

### Example Input:
```csv
building,device,timestamp,pointName,value
US-MTV-1708,MAIN_device,2025-01-15T00:00:00,kW,125.5
US-MTV-1708,MAIN_device,2025-01-15T00:00:00,Temperature,72.3
US-MTV-1708,MAIN_device,2025-01-15T00:15:00,kW,130.2
```

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
- Verify your input file has columns: `building`, `device`, `timestamp`, `pointName`, `value`
- Check that timestamps are in ISO8601 format
- Ensure numeric values don't have unexpected formatting

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

[Add your license here]

## Contact

[Add contact information here]
