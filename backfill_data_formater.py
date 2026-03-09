from venv import create
import subprocess
import pandas as pd
import os
import argparse
import sys
from argparse import ArgumentParser
import logging
import datetime
import csv
import pytz
import openpyxl
import re

dirname = os.path.dirname(os.path.abspath(__file__))

def get_api_key():
    """
    Get API key from .api_key file, or prompt user if it doesn't exist.
    The .api_key file is gitignored to prevent committing secrets.
    """
    api_key_path = os.path.join(dirname, '.api_key')

    if os.path.exists(api_key_path):
        # Read existing API key
        with open(api_key_path, 'r') as f:
            return f.read().strip()

    # First time setup - prompt for API key
    print("\n" + "=" * 60)
    print("FIRST TIME SETUP")
    print("=" * 60)
    print("Please provide your API key. This will be saved locally")
    print("and will NOT be committed to git (.api_key is gitignored).")
    print("=" * 60)

    api_key = input("\nEnter your API key: ").strip()

    # Save API key to file
    with open(api_key_path, 'w') as f:
        f.write(api_key)

    print(f"\n✓ API key saved to: {api_key_path}")
    print("=" * 60 + "\n")

    return api_key
 
# Standard point name → unit mappings
_STANDARD_UNITS = {
    'Current':               'amperes',
    'Current_A':             'amperes',
    'Current_B':             'amperes',
    'Current_C':             'amperes',
    'Frequency':             'hertz',
    'PF':                    'no-units',
    'PF_A':                  'no-units',
    'PF_B':                  'no-units',
    'PF_C':                  'no-units',
    'Volts_AB':              'volts',
    'Volts_AN':              'volts',
    'Volts_BC':              'volts',
    'Volts_BN':              'volts',
    'Volts_CA':              'volts',
    'Volts_CN':              'volts',
    'Volts_LL':              'volts',
    'Volts_LN':              'volts',
    'kVAR_Demand':           'kilovolt-amperes-reactive',
    'kVA_Demand':            'kilovolt-amperes',
    'kVAR':                  'kilovolt-amperes-reactive',
    'kVA':                   'kilovolt-amperes',
    'kW':                    'kilowatts',
    'kW_A':                  'kilowatts',
    'kW_B':                  'kilowatts',
    'kW_C':                  'kilowatts',
    'kWh_rec':               'kilowatts',
    'kWh':                   'kilowatt-hours',
    'Temperature':           'degrees-fahrenheit',
    'WaterFlowTotal':        'us-gallons',
    'WaterFlowRate':         'us-gallons-per-minute',
    'GasFlowRate_Unscaled':  'cubic-feet-per-hour',
    'GasFlowTotal_Unscaled': 'cubic-feet'
}

# Mango-formatted point name → unit mappings
_MANGO_UNITS = {
    'water_volume_accumulator': 'us-gallons',
    'flowrate_sensor': 'us-gallons-per-minute',
    'gas_flowrate_sensor': 'cubic-feet-per-hour',
    'gas_volume_accumulator': 'cubic-feet',
    'energy_accumulator': 'kilowatt-hours',
    'power_sensor': 'kilowatts'
}

unit_df = pd.DataFrame(
    [{'pointName': k, 'Units': v} for k, v in {**_STANDARD_UNITS, **_MANGO_UNITS}.items()]
)

# Custom exception for reset functionality
class ResetException(Exception):
    """Exception raised when user wants to reset to the beginning"""
    pass

def check_special_input(user_input):
    """
    Check if user input is a special command (quit or reset).
    Args:
        user_input: String input from user
    Returns:
        None if no special command, otherwise exits or raises ResetException
    """
    cleaned_input = user_input.strip().lower()
    if cleaned_input in ['quit', 'exit', 'q']:
        print("\nExiting program...")
        sys.exit(0)
    elif cleaned_input == 'reset':
        print("\nRestarting from the beginning...\n")
        raise ResetException()

def check_input(input_path):
    """
    Check if input is a valid CSV or XLSX file.
    Args:
        input_path: Absolute path to a file
    Returns:
        Boolean on whether input is a valid file with correct extension
    """
    if not os.path.exists(input_path):
        print(f"ERROR: File does not exist: {input_path}")
        return False

    if os.path.isdir(input_path):
        print(f"ERROR: Path is a directory. Please provide a single CSV or XLSX file.")
        return False

    if not os.path.isfile(input_path):
        print(f"ERROR: Path is not a valid file: {input_path}")
        return False

    file_extension = os.path.splitext(input_path)[1].lower()
    if file_extension not in ['.csv', '.xlsx', '.xls']:
        print(f"ERROR: File must be .csv or .xlsx format. Got: {file_extension}")
        return False

    print(f"Valid {file_extension.upper()} file detected: {os.path.basename(input_path)}")
    return True

def get_files_from_directory(directory_path):
    """
    Get all valid CSV and XLSX files from a directory.
    Args:
        directory_path: Path to directory
    Returns:
        List of absolute paths to valid CSV/XLSX files
    """
    if not os.path.exists(directory_path):
        print(f"ERROR: Directory does not exist: {directory_path}")
        return []

    if not os.path.isdir(directory_path):
        print(f"ERROR: Path is not a directory: {directory_path}")
        return []

    valid_files = []
    valid_extensions = ['.csv', '.xlsx', '.xls']

    for filename in os.listdir(directory_path):
        filepath = os.path.join(directory_path, filename)

        # Skip directories
        if os.path.isdir(filepath):
            continue

        # Check file extension
        file_extension = os.path.splitext(filename)[1].lower()
        if file_extension in valid_extensions:
            valid_files.append(os.path.abspath(filepath))

    if valid_files:
        print(f"Found {len(valid_files)} valid file(s) in directory")
    else:
        print(f"No valid CSV or XLSX files found in directory: {directory_path}")

    return valid_files

def collect_files_interactively():
    """
    Interactively collect multiple file paths from user input.
    Returns:
        List of valid file paths
    """
    print("\nEnter file paths one at a time.")
    print("Type 'done' or press Enter with no input when finished.")
    print("Type 'quit' to exit or 'reset' to start over.\n")

    files = []
    file_count = 0

    while True:
        prompt = f"Enter file path {file_count + 1} (or 'done' to finish): "
        user_input = input(prompt).strip()

        # Check for special commands (quit/reset)
        check_special_input(user_input)

        # Check if user wants to finish
        if user_input.lower() == 'done' or user_input == '':
            if files:
                print(f"\nCollected {len(files)} file(s)")
                break
            elif user_input == '':
                print("No files entered. Please enter at least one file.")
                continue
            else:
                break

        # Validate the file
        if check_input(user_input):
            abs_path = os.path.abspath(user_input)
            if abs_path not in files:
                files.append(abs_path)
                file_count += 1
                print(f"  ✓ Added ({len(files)} file(s) total)")
            else:
                print("  ! File already added")
        else:
            print("  Please try again.\n")

    return files

def read_data_file(filepath):
    """
    Read a CSV or XLSX file into a pandas DataFrame.
    Handles numeric columns to prevent comma formatting.
    Args:
        filepath: Path to CSV or XLSX file
    Returns:
        pandas DataFrame
    """
    file_extension = os.path.splitext(filepath)[1].lower()

    if file_extension == '.csv':
        df = pd.read_csv(filepath)
    elif file_extension in ['.xlsx', '.xls']:
        df = pd.read_excel(filepath, engine='openpyxl')
    else:
        raise ValueError(f"Unsupported file format: {file_extension}. Only .csv and .xlsx files are supported.")

    # Remove commas from numeric columns if they were read as strings
    for col in df.columns:
        if df[col].dtype == 'object':
            # Try to convert string numbers with commas to numeric
            try:
                # Only process non-null values, remove commas, then try to convert to numeric
                cleaned = df[col].str.replace(',', '', regex=False)
                try:
                    df[col] = pd.to_numeric(cleaned)
                except (ValueError, TypeError):
                    # If conversion fails, keep original values
                    pass
            except (AttributeError, TypeError):
                # Skip columns that don't support string operations
                pass

    return df
 
def format_timestamps(normalized_file):
    """
    Unified timestamp formatting - handles both formatted and unformatted timestamps.
    - Parses timestamps using ISO8601 format
    - Localizes to America/Los_Angeles timezone (accounts for daylight savings)
    - Adds 15-minute offset (for BixBox aggregation compatibility)
    - Skips formatting if timestamps are already timezone-aware (prevents double-processing)
    - Raises error if timestamp parsing fails (no fallback to prevent bad data)
    - TODO: determine if 15-minute offset is still needed

    Args:
        normalized_file: pivotted CSV file
    Returns:
        Pivotted CSV file with formatted timestamps
    Raises:
        ValueError: If timestamps cannot be parsed in ISO8601 format
    """
    df = normalized_file

    # Step 1: Check if timestamps are already timezone-aware (before parsing)
    # This helps us determine if we should add the 15-minute offset later
    original_count = len(df)
    original_timestamps = df.timestamp.copy()

    # Try to detect if input timestamps already have timezone info
    # This indicates they've been processed by mango_reformatter and already have the 15-min offset
    already_tz_aware = False
    if len(df) > 0:
        first_ts = str(df.timestamp.iloc[0])
        # Check for timezone indicators in various formats:
        # - ISO8601 with T: "2025-10-01T07:15:00-07:00"
        # - Pandas format: "2025-10-01 07:15:00-07:00" (space instead of T)
        # - Check last 6 characters for timezone pattern like "-07:00" or "+00:00"
        tz_pattern = r'[+-]\d{2}:\d{2}$'  # Matches "-07:00" or "+05:30" at end of string
        already_tz_aware = bool(re.search(tz_pattern, first_ts)) or \
                          (hasattr(df.timestamp.iloc[0], 'tz') and df.timestamp.iloc[0].tz is not None)

    # Step 2: Convert to datetime and normalize to UTC
    # Using utc=True normalizes all timestamps to UTC first, avoiding "mixed timezone" issues
    df.timestamp = pd.to_datetime(df.timestamp, utc=True)

    # Check for any parsing failures (NaT values)
    nat_count = df.timestamp.isna().sum()
    if nat_count > 0:
        # Show examples of failed timestamps for debugging
        failed_examples = original_timestamps[df.timestamp.isna()].head(5).tolist()
        raise ValueError(f"Failed to parse {nat_count} timestamp(s) out of {original_count}. Examples of failed timestamps: {failed_examples}")

    # Step 3: Convert from UTC to America/Los_Angeles timezone
    df.timestamp = df.timestamp.dt.tz_convert('America/Los_Angeles')

    # Step 4: Add 15-minute offset only if timestamps weren't already timezone-aware
    if already_tz_aware:
        print("  Timestamps already formatted (timezone-aware). Skipping offset.")
    else:
        df.timestamp = df.timestamp + pd.Timedelta(minutes=15)
        print("  Timestamps converted to Pacific timezone with 15-minute offset")

    return df

def detect_paired_format(df):
    """
    Detect if the DataFrame has paired pointName/value columns format.

    Args:
        df: DataFrame to check
    Returns:
        Boolean indicating if paired format is detected
    """
    # Check for pointName1, value1, pointName2, value2 pattern
    has_pointName1 = 'pointName1' in df.columns
    has_value1 = 'value1' in df.columns
    return has_pointName1 and has_value1

def convert_paired_to_flat(df):
    """
    Convert paired column format to flat format.
    Input: timestamp, building, device, externalID, pointName1, value1, pointName2, value2, ...
    Output: building, device, timestamp, pointName, value, externalID (if present)

    Args:
        df: DataFrame with paired column format
    Returns:
        DataFrame in flat format
    """
    print("Detected paired column format (pointName1, value1, ...). Converting to flat format...")

    # Extract metadata columns
    metadata_cols = ['timestamp', 'building', 'device']
    has_external_id = 'externalID' in df.columns
    if has_external_id:
        metadata_cols.append('externalID')

    # Find all pointName/value pairs
    point_pairs = []
    i = 1
    while f'pointName{i}' in df.columns and f'value{i}' in df.columns:
        point_pairs.append(i)
        i += 1

    if not point_pairs:
        raise ValueError("No valid pointName/value pairs found")

    print(f"Found {len(point_pairs)} pointName/value pair(s)")

    # Convert to flat format
    flat_rows = []
    for _, row in df.iterrows():
        for i in point_pairs:
            pointname_col = f'pointName{i}'
            value_col = f'value{i}'

            # Skip if pointName or value is NaN/empty
            if pd.isna(row[pointname_col]) or pd.isna(row[value_col]):
                continue

            flat_row = {
                'building': row['building'],
                'device': row['device'],
                'timestamp': row['timestamp'],
                'pointName': row[pointname_col],
                'value': row[value_col]
            }

            if has_external_id:
                flat_row['externalID'] = row['externalID']

            flat_rows.append(flat_row)

    flat_df = pd.DataFrame(flat_rows)
    print(f"Converted to flat format: {len(flat_df)} rows")
    return flat_df

def pivot_flat_file(input_path):
    """
    Pivot a single flat CSV or XLSX file and split into distinct files.
    Now supports both flat format and paired column format from mango_reformatter.py

    Args:
        input_path: Absolute path to a single file
    Returns:
        Single dataframe of pivotted telemetry data per building and device
    Raises:
        ValueError: If required columns are missing from the input file
        Exception: For other processing errors
    """
    try:
        # split the single CSV/XLSX into distinct files for each device
        df = read_data_file(input_path)

        # Check if input is in paired format and convert if needed
        if detect_paired_format(df):
            df = convert_paired_to_flat(df)

        # Validate required columns
        required_columns = ['building', 'device', 'timestamp', 'pointName', 'value']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")

        # Check if dataframe is empty
        if df.empty:
            raise ValueError("Input file is empty or contains no valid data")

        # Check if externalID column exists (optional)
        has_external_id = 'externalID' in df.columns

        # Determine grouping columns based on whether externalID exists
        if has_external_id:
            groupby_cols = ['building', 'device', 'externalID']
        else:
            groupby_cols = ['building', 'device']

        for group_keys, group in df.groupby(groupby_cols):
            # Unpack group keys based on number of grouping columns
            if has_external_id:
                building, device, external_id = group_keys
            else:
                building, device = group_keys
                external_id = None
            df_single = group
            table = pd.pivot_table(data=df_single, values='value', index=['timestamp'], columns='pointName')
            table = table.rename_axis(None, axis=1).reset_index()
            table = format_timestamps(table)

            # Calculate date range for folder naming
            start_date = table.timestamp.min().date().strftime('%Y-%m-%d')
            end_date = table.timestamp.max().date().strftime('%Y-%m-%d')

            # Create simplified folder structure: {building}_{device}_{start_date}_{end_date}
            folder_name = f'{building}_{device}_{start_date}_{end_date}'
            newpath = os.path.join(outputdirname, folder_name)

            if not os.path.exists(newpath):
                os.makedirs(newpath)

            # Create a unique logger for this building/device/externalID combination
            if external_id is not None:
                logger_name = f'{building}_{device}_{external_id}_{start_date}_{end_date}'
            else:
                logger_name = f'{building}_{device}_{start_date}_{end_date}'
            logger = logging.getLogger(logger_name)
            logger.setLevel(logging.DEBUG)

            # Remove any existing handlers to avoid duplicates
            logger.handlers.clear()

            # Create file handler for this specific log file
            log_file_path = os.path.join(newpath, 'backfill_log.log')
            file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)

            # Create formatter and add it to the handler
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(formatter)

            # Add handler to logger
            logger.addHandler(file_handler)

            # Log initial information
            logger.info('Input File Path: '+ os.path.join(dirname, input_path))
            logger.info('Action Performed: Pivoting and Timestamp Formatting')

            output_path = os.path.join(newpath, f'{building}_{device}.csv')
            table.to_csv(output_path, index=False, quoting=csv.QUOTE_NONNUMERIC, float_format='%.10g')
            logger.info('Output File Path: '+ output_path + ', Date Range: '+ start_date + ' to ' + end_date)

            # Create unit file
            a_df = df_single.drop_duplicates(['device','pointName'])[['device','pointName']]
            unit_table = a_df.merge(unit_df, how = "left", on = "pointName")
            unit_table = unit_table.rename({'device':'Device Id', 'pointName':'Field Name'}, axis="columns")
            output_unit_path = os.path.join(newpath, f'{building}_'+f'{device}'+'_units.csv')
            unit_table.to_csv(output_unit_path, index=False, quoting=csv.QUOTE_NONNUMERIC)
            logger.info('Output Unit File Path: '+ output_unit_path)

            # Create run_command.txt file with the command template
            api_key = get_api_key()

            # Set device_num_id based on whether externalID is available
            device_num_id_value = str(external_id) if external_id is not None else ''

            # Detect whether this device's data uses Mango-formatted point names
            device_points = set(df_single['pointName'].unique())
            is_mango = bool(device_points & set(_MANGO_UNITS.keys()))

            if is_mango:
                command_template = (
                    f'-- MANGO --\n\n'
                    f'blaze run java/com/google/corp/bizapps/rews/datalake/tools/backfill:backfill_tool -- '
                    f'--data_file="{output_path}" '
                    f'--unit_file="{output_unit_path}" '
                    f'--mode="populate" '
                    f'--api_key="{api_key}" '
                    f'--topic=projects/google.com:datalake/topics/replay '
                    f'--gcp_project_id=google.com:datalake '
                    f'--device_num_id={device_num_id_value} '
                    f'--robot_account=datalake-backfill@datalake.google.com.iam.gserviceaccount.com '
                    f'--data_field_name="points" '
                    f'--present_value_field_name="present_value"'
                )
            else:
                command_template = (
                    f'-- BITBOX --\n\n'
                    f'blaze run java/com/google/corp/bizapps/rews/datalake/tools/backfill:backfill_tool -- '
                    f'--data_file="{output_path}" '
                    f'--unit_file="{output_unit_path}" '
                    f'--mode="populate" '
                    f'--api_key="{api_key}" '
                    f'--topic=projects/google.com:datalake/topics/replay '
                    f'--gcp_project_id=google.com:datalake '
                    f'--device_num_id={device_num_id_value} '
                    f'--robot_account=datalake-backfill@datalake.google.com.iam.gserviceaccount.com'
                )
            run_command_path = os.path.join(newpath, 'run_command.txt')
            with open(run_command_path, 'w') as cmd_file:
                cmd_file.write(command_template)
            logger.info('Run Command File Path: '+ run_command_path)

            unmatched_units = unit_table[unit_table['Units'].isnull()]
            field_list = ', '.join(unmatched_units['Field Name'])
            if not unmatched_units.empty:
                logger.warning('The following field(s) is not recognized: '+ field_list + '. Please review and add units if the field(s) is valid.')
                print('WARNING: The following field(s) is not recognized: '+ field_list + '. Please review and add units if the field(s) is valid.')

            # Close the file handler to ensure logs are written properly
            file_handler.close()
            logger.removeHandler(file_handler)

    except KeyError as e:
        raise ValueError(f"Column error during pivot operation: {str(e)}")
    except pd.errors.ParserError as e:
        raise ValueError(f"Error parsing file: {str(e)}")
    except Exception as e:
        raise Exception(f"Unexpected error processing file: {str(e)}")
 
def run_prerequisites():
    """
    Run environment setup for the backfill client.
    Returns the client root directory string on success, or None on failure.
    """
    print("  Running: cd $(p4 g4d backfill) && g4 sync")
    result = subprocess.run(
        ["bash", "-c", 'cd "$(p4 g4d backfill)" && g4 sync && pwd'],
        capture_output=True, text=True
    )

    lines = result.stdout.strip().splitlines() if result.stdout.strip() else []
    for line in lines[:-1]:
        print(line)
    client_root = lines[-1].strip() if lines else None

    if result.stderr.strip():
        print(result.stderr.strip())

    if result.returncode != 0:
        print(f"  ✗ Environment setup failed (exit code {result.returncode}). Aborting.")
        return None

    print(f"  ✓ Environment ready (client root: {client_root})")
    return client_root


def find_output_files(folder):
    """
    Locate the data CSV and unit CSV inside a formatter output folder.
    Returns (data_file_path, unit_file_path).
    Raises ValueError if either is missing or ambiguous.
    """
    all_csvs = [f for f in os.listdir(folder) if f.lower().endswith('.csv')]
    unit_files = [f for f in all_csvs if f.endswith('_units.csv')]
    data_files = [
        f for f in all_csvs
        if not f.endswith('_units.csv') and f != 'backfill_template.csv'
    ]

    if not data_files:
        raise ValueError("No data CSV found in folder (expected a non-units CSV).")
    if len(data_files) > 1:
        raise ValueError(f"Multiple data CSVs found: {data_files}. Please ensure only one exists.")
    if not unit_files:
        raise ValueError("No unit CSV found in folder (expected a file ending in _units.csv).")
    if len(unit_files) > 1:
        raise ValueError(f"Multiple unit CSVs found: {unit_files}.")

    return (
        os.path.join(folder, data_files[0]),
        os.path.join(folder, unit_files[0]),
    )


def detect_is_mango_from_folder(folder):
    """
    Read run_command.txt in the folder and check if it's a Mango command.
    Returns True if mango, False if bitbox or file not found.
    """
    run_cmd_path = os.path.join(folder, 'run_command.txt')
    if not os.path.exists(run_cmd_path):
        return False
    with open(run_cmd_path, 'r', encoding='utf-8') as f:
        contents = f.read()
    return '--data_field_name="points"' in contents


def run_backfill_command(data_file, unit_file, device_num_id, is_mango, api_key, cwd=None):
    """
    Run the blaze backfill populate command via subprocess.
    Returns (returncode, stdout, stderr).
    """
    cmd = [
        "blaze", "run",
        "java/com/google/corp/bizapps/rews/datalake/tools/backfill:backfill_tool",
        "--",
        f"--data_file={data_file}",
        f"--unit_file={unit_file}",
        "--mode=populate",
        f"--api_key={api_key}",
        "--topic=projects/google.com:datalake/topics/replay",
        "--gcp_project_id=google.com:datalake",
        f"--device_num_id={device_num_id}",
        "--robot_account=datalake-backfill@datalake.google.com.iam.gserviceaccount.com",
    ]
    if is_mango:
        cmd += ["--data_field_name=points", "--present_value_field_name=present_value"]

    print("  Running blaze backfill command...")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result.returncode, result.stdout, result.stderr


def extract_publish_lines(stdout, stderr):
    """
    Extract 'INFO: Finished publishing messages' lines from blaze output.
    Searches both stdout and stderr.
    Returns a list of matching lines, or empty list if none found.
    """
    combined = (stdout or "") + "\n" + (stderr or "")
    return [line.strip() for line in combined.splitlines()
            if "INFO: Finished publishing messages" in line]


def write_backfill_run_summary(folder, command, returncode, stdout, stderr):
    """
    Write backfill_run_summary.txt to the given folder.
    SUCCESS: exit 0 + publish lines found → writes publish lines.
    UNCERTAIN: exit 0 but no publish lines → writes full output.
    FAILED: non-zero exit → writes full output.
    """
    publish_lines = extract_publish_lines(stdout, stderr)

    if returncode == 0 and publish_lines:
        status = "SUCCESS"
        body = "\n".join(publish_lines)
    elif returncode == 0:
        status = "UNCERTAIN (no 'Finished publishing' line found)"
        combined = (stdout or "").strip()
        if stderr and stderr.strip():
            combined += ("\n\n--- stderr ---\n" + stderr.strip()) if combined else stderr.strip()
        body = combined if combined else "(no output captured)"
    else:
        status = f"FAILED (exit code {returncode})"
        combined = (stdout or "").strip()
        if stderr and stderr.strip():
            combined += ("\n\n--- stderr ---\n" + stderr.strip()) if combined else stderr.strip()
        body = combined if combined else "(no output captured)"

    filepath = os.path.join(folder, 'backfill_run_summary.txt')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write("=" * 60 + "\n")
        f.write(f"Status  : {status}\n")
        f.write(f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("-" * 60 + "\n")
        f.write("Command:\n")
        f.write(command + "\n")
        f.write("-" * 60 + "\n")
        f.write("Output:\n")
        f.write(body + "\n")
        f.write("=" * 60 + "\n")

    print(f"  Summary written: {filepath}")
    return filepath


def generate_template_file(folder, filename=None):
    """
    Generate a blank input CSV template with the correct columns and one example row.
    Auto-names the file 'backfill_template.csv' if no filename is given.
    Returns the path to the written file.
    """
    if not filename:
        filename = "backfill_template.csv"
    elif not filename.lower().endswith('.csv'):
        filename += '.csv'

    if not os.path.exists(folder):
        print(f"Creating directory: {folder}")
        os.makedirs(folder)

    filepath = os.path.join(folder, filename)

    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(['building', 'device', 'externalID', 'timestamp', 'pointName', 'value'])
        writer.writerow([
            'US-MFA-BV100',
            'utility-WM_01_BLDGDCW',
            '1743694964149'
            '2025-06-30 00:00:00',
            'kWh',
            '123.45',
        ])

    print(f"  Template written: {filepath}")
    return filepath


def parse_arguments():
    """
    Parse command-line arguments for the backfill data formatter.
    Returns:
        Parsed arguments or None if user wants interactive mode
    """
    parser = ArgumentParser(
        description='Backfill Data Formatter - Process CSV or XLSX telemetry files',
        epilog='If no arguments provided, interactive mode will be used.'
    )

    # Create mutually exclusive group for input sources
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        '-i', '--input',
        type=str,
        help='Path to a single input CSV or XLSX file'
    )
    input_group.add_argument(
        '-d', '--directory',
        type=str,
        help='Path to directory containing CSV or XLSX files (processes all files)'
    )

    parser.add_argument(
        '-o', '--output',
        type=str,
        default=None,
        help='Output directory path (default: current working directory)'
    )

    args = parser.parse_args()

    # If no arguments provided, return None to trigger interactive mode
    if args.input is None and args.directory is None:
        return None

    return args

### MAIN
if __name__ == "__main__":
    print("=" * 60)
    print("Backfill Data Formatter - Multi-File Processor")
    print("Supports: CSV and XLSX files")
    print("=" * 60)
    print()

    # Try to parse command-line arguments
    args = parse_arguments()

    # Main loop to support reset functionality
    while True:
        try:
            # Collect input files based on mode
            files_to_process = []

            if args and args.input:
                # Command-line mode: single file
                if not check_input(args.input):
                    print("\nExiting due to invalid input file.")
                    sys.exit(1)
                files_to_process = [os.path.abspath(args.input)]

            elif args and args.directory:
                # Command-line mode: directory
                files_to_process = get_files_from_directory(args.directory)
                if not files_to_process:
                    print("\nExiting: No valid files found in directory.")
                    sys.exit(1)

            else:
                # Interactive mode - offer three options
                print("Interactive Mode")
                print("(Use --help to see command-line options)")
                print("(Type 'quit' at any prompt to exit, 'reset' to start over)")
                print()
                print("Choose input mode:")
                print("  0. Generate a blank template CSV (with example row)")
                print("  1. Process a single file")
                print("  2. Process all files in a directory")
                print("  3. Process multiple individual files")
                print("  4. Run a backfill command for a processed folder")
                print()

                valid_choice = False
                while not valid_choice:
                    choice = input("Enter choice (0-4): ").strip()
                    check_special_input(choice)

                    if choice == '0':
                        folder = input("\nEnter folder to save template (leave blank for current directory): ").strip().strip('"').strip("'")
                        check_special_input(folder) if folder else None
                        if not folder:
                            folder = os.getcwd()
                        filename = input("Template filename (leave blank for 'backfill_template.csv'): ").strip().strip('"').strip("'")
                        check_special_input(filename) if filename else None
                        generate_template_file(folder, filename or None)
                        print("\nTemplate created. Fill it in and re-run with option 1.")
                        sys.exit(0)

                    elif choice == '1':
                        # Single file mode
                        valid_input_file = False
                        while not valid_input_file:
                            input_filepath = input('\nEnter path to CSV or XLSX file: ')
                            check_special_input(input_filepath)
                            valid_input_file = check_input(input_filepath)
                            if not valid_input_file:
                                print("Please try again.\n")
                            else:
                                files_to_process = [os.path.abspath(input_filepath)]
                        valid_choice = True

                    elif choice == '2':
                        # Directory mode
                        valid_directory = False
                        while not valid_directory:
                            dir_path = input('\nEnter directory path: ').strip()
                            check_special_input(dir_path)
                            files_to_process = get_files_from_directory(dir_path)
                            if files_to_process:
                                valid_directory = True
                                valid_choice = True
                            else:
                                print("Please try again.\n")

                    elif choice == '3':
                        # Multiple files mode
                        files_to_process = collect_files_interactively()
                        if files_to_process:
                            valid_choice = True
                        else:
                            print("No files collected. Please try again.\n")

                    elif choice == '4':
                        # Run backfill command for a processed folder
                        print()
                        folder = input("Enter path to processed output folder: ").strip().strip('"').strip("'")
                        check_special_input(folder)
                        if not os.path.isdir(folder):
                            print(f"ERROR: '{folder}' is not a valid directory.\n")
                            continue
                        folder = os.path.abspath(folder)

                        run_cmd_path = os.path.join(folder, 'run_command.txt')
                        if not os.path.exists(run_cmd_path):
                            print(f"ERROR: No run_command.txt found in '{folder}'.\n")
                            continue

                        with open(run_cmd_path, 'r', encoding='utf-8') as f:
                            contents = f.read()

                        # Extract the blaze command line (line starting with 'blaze')
                        cmd_lines = [l.strip() for l in contents.splitlines() if l.strip().startswith('blaze')]
                        if not cmd_lines:
                            print(f"ERROR: No blaze command found in run_command.txt.\n")
                            continue
                        cmd_str = cmd_lines[0]

                        is_mango = '--data_field_name="points"' in contents
                        mode_label = "MANGO" if is_mango else "BITBOX"
                        print(f"  Detected mode: {mode_label}")

                        print(f"\n{'=' * 60}")
                        print("Setting up environment...")
                        print(f"{'=' * 60}\n")
                        blaze_cwd = run_prerequisites()
                        if blaze_cwd is None:
                            print("\nEnvironment setup failed. No command was run.")
                            sys.exit(1)

                        print(f"\n{'=' * 60}")
                        print(f"Running {mode_label} backfill command...")
                        print(f"{'=' * 60}\n")
                        print("  Running blaze backfill command...\n")
                        process = subprocess.Popen(
                            cmd_str, shell=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, cwd=blaze_cwd, bufsize=1
                        )
                        output_lines = []
                        for line in process.stdout:
                            print(line, end='', flush=True)
                            output_lines.append(line)
                        process.wait()
                        returncode = process.returncode
                        stdout = ''.join(output_lines)
                        stderr = ''

                        publish_lines = extract_publish_lines(stdout, stderr)
                        if returncode == 0 and publish_lines:
                            print(f"  ✓ Success: {publish_lines[0]}")
                        elif returncode == 0:
                            print("  ! Command exited 0 but no 'Finished publishing' line found. Check summary.")
                        else:
                            print(f"  ✗ Command failed (exit code {returncode}). Check summary.")

                        write_backfill_run_summary(folder, cmd_str, returncode, stdout, stderr)
                        sys.exit(0)

                    else:
                        print("Invalid choice. Please enter 0, 1, 2, 3, or 4.\n")

            # Determine default output directory
            if args and args.output:
                outputdirname = args.output
                if not os.path.exists(outputdirname):
                    print(f"Creating output directory: {outputdirname}")
                    os.makedirs(outputdirname)
            else:
                # Default to directory of first input file
                default_output = os.path.dirname(os.path.abspath(files_to_process[0]))

                # In interactive mode, ask if user wants different output directory
                if not args:
                    print(f"\nDefault output directory: {default_output}")
                    use_different = input("Use a different output directory? (y/N): ").strip().lower()
                    check_special_input(use_different)

                    if use_different in ['y', 'yes']:
                        outputdirname = input("Enter output directory path: ").strip()
                        check_special_input(outputdirname)
                        if not os.path.exists(outputdirname):
                            print(f"Creating output directory: {outputdirname}")
                            os.makedirs(outputdirname)
                    else:
                        outputdirname = default_output
                else:
                    outputdirname = default_output

                print(f"Output directory: {outputdirname}")

            # Process all files
            print(f"\n{'=' * 60}")
            print(f"Processing {len(files_to_process)} file(s)...")
            print(f"{'=' * 60}\n")

            successful = 0
            failed = 0
            failed_files = []

            for idx, filepath in enumerate(files_to_process, 1):
                try:
                    print(f"[{idx}/{len(files_to_process)}] Processing: {os.path.basename(filepath)}")
                    output = pivot_flat_file(filepath)
                    successful += 1
                    print(f"  ✓ Success")
                except Exception as e:
                    failed += 1
                    failed_files.append((filepath, str(e)))
                    print(f"  ✗ Failed: {str(e)}")

                if idx < len(files_to_process):
                    print()

            # Print summary
            print("\n" + "=" * 60)
            print("Processing Summary")
            print("=" * 60)
            print(f"Total files: {len(files_to_process)}")
            print(f"Successful: {successful}")
            print(f"Failed: {failed}")

            if failed_files:
                print("\nFailed files:")
                for filepath, error in failed_files:
                    print(f"  - {os.path.basename(filepath)}: {error}")

            print(f"\nOutput saved to: {outputdirname}/")
            print("Folder structure: {{building}}_{{device}}_{{start-date}}_{{end-date}}/")
            print("=" * 60)

            # Break out of the loop after successful completion (no reset)
            break

        except ResetException:
            # User requested reset, continue the while loop
            continue