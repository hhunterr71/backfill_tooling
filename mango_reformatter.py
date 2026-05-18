import pandas as pd
import os
import argparse
import sys
import pytz
import csv

def format_timestamps(df):
    """
    Unified timestamp formatting - handles both formatted and unformatted timestamps.
    - Parses timestamps using ISO8601 format
    - Localizes to America/Los_Angeles timezone (accounts for daylight savings)
    - Adds 15-minute offset (for BixBox aggregation compatibility)
    - Skips formatting if timestamps are already timezone-aware (prevents double-processing)
    - Raises error if timestamp parsing fails (no fallback to prevent bad data)

    Args:
        df: DataFrame with timestamp column
    Returns:
        DataFrame with formatted timestamps
    Raises:
        ValueError: If timestamps cannot be parsed in ISO8601 format
    """
    # Step 1: Convert to datetime and normalize to UTC
    # This handles: ISO8601 timestamps with explicit timezone offsets (e.g., 2025-10-01T00:00:00-07:00)
    # Using utc=True normalizes all timestamps to UTC first, avoiding "mixed timezone" issues
    # Store original count for validation
    original_count = len(df)
    original_timestamps = df.timestamp.copy()

    df.timestamp = pd.to_datetime(df.timestamp, utc=True)

    # Check for any parsing failures (NaT values)
    nat_count = df.timestamp.isna().sum()
    if nat_count > 0:
        # Show examples of failed timestamps for debugging
        failed_examples = original_timestamps[df.timestamp.isna()].head(5).tolist()
        raise ValueError(f"Failed to parse {nat_count} timestamp(s) out of {original_count}. Examples of failed timestamps: {failed_examples}")

    # Step 2: Convert from UTC to America/Los_Angeles timezone
    # This properly handles daylight saving time transitions
    df.timestamp = df.timestamp.dt.tz_convert('America/Los_Angeles')

    # Step 3: Add 15-minute offset for BixBox aggregation compatibility
    df.timestamp = df.timestamp + pd.Timedelta(minutes=15)

    print("  Timestamps converted to Pacific timezone with 15-minute offset")

    return df

def remove_rendered_columns(df):
    """
    Remove columns containing '_rendered' in their names.

    Args:
        df: DataFrame with potential _rendered columns
    Returns:
        DataFrame with _rendered columns removed
    """
    rendered_cols = [col for col in df.columns if '_rendered' in col.lower()]
    if rendered_cols:
        print(f"Removing {len(rendered_cols)} _rendered column(s): {', '.join(rendered_cols)}")
        df = df.drop(columns=rendered_cols)
    else:
        print("No _rendered columns found.")
    return df

def rename_point_columns(df):
    """
    Rename measurement columns by stripping the prefix before ' - '.
    For example: 'meter_name - kW' becomes 'kW'

    Args:
        df: DataFrame with columns formatted as 'meter_name - pointName'
    Returns:
        DataFrame with cleaned column names
    """
    new_columns = {}
    for col in df.columns:
        if col != 'timestamp' and ' - ' in col:
            # Extract pointName after ' - '
            point_name = col.split(' - ', 1)[1]
            new_columns[col] = point_name

    if new_columns:
        print(f"Renaming {len(new_columns)} measurement column(s)")
        df = df.rename(columns=new_columns)
    else:
        print("No columns to rename (no ' - ' separator found).")

    return df

def restructure_to_paired_columns(df):
    """
    Restructure measurement columns into paired pointName/value columns.
    Input: timestamp, kW, kWh, Temperature
    Output: timestamp, pointName1, value1, pointName2, value2, pointName3, value3

    Args:
        df: DataFrame with timestamp and measurement columns
    Returns:
        DataFrame with paired pointName/value columns
    """
    # Get measurement columns (everything except timestamp)
    measurement_cols = [col for col in df.columns if col != 'timestamp']

    if not measurement_cols:
        print("No measurement columns to restructure")
        return df

    # Create new DataFrame starting with timestamp
    new_df = pd.DataFrame()
    new_df['timestamp'] = df['timestamp']

    # Create paired columns for each measurement
    for idx, col_name in enumerate(measurement_cols, 1):
        new_df[f'pointName{idx}'] = col_name  # Point name (e.g., "kW")
        new_df[f'value{idx}'] = df[col_name]  # Actual values

    print(f"Restructured {len(measurement_cols)} measurement columns into {len(measurement_cols)} pointName/value pairs")
    return new_df

def add_metadata_columns(df, building, device, external_id):
    """
    Add metadata columns (building, device, externalID) after the timestamp column.

    Args:
        df: DataFrame with timestamp and paired pointName/value columns
        building: Building identifier
        device: Device identifier
        external_id: External device ID (can be empty string)
    Returns:
        DataFrame with metadata columns inserted
    """
    # Insert metadata columns after timestamp
    df.insert(1, 'building', building)
    df.insert(2, 'device', device)
    df.insert(3, 'externalID', external_id if external_id else '')

    print(f"Added metadata: building={building}, device={device}, externalID={external_id if external_id else '(none)'}")
    return df

def detect_meter_name(columns):
    """
    Detect meter name from column names formatted as 'meter_name - pointName'.
    Prepends 'power-meter-' for electrical meters or 'utility-' for gas/water meters
    based on the point names found in the columns. Falls back to prompting the user
    if the meter type cannot be determined automatically.

    Electrical indicators: kW, kWh, kVAR, kVA, PF, Amps, Volts, Hz, etc.
    Utility indicators: CCF, Therms, BTU, Gallons, GPM, MCF, etc.

    Args:
        columns: List of column names
    Returns:
        Detected meter name string with type prefix, or None if raw name not detectable
    """
    ELECTRICAL_KEYWORDS = {'kw', 'kwh', 'kvar', 'kva', 'pf', 'power factor', 'amp', 'amps',
                           'volt', 'volts', 'hz', 'current', 'voltage', 'demand',
                           'real power', 'reactive power', 'apparent power'}
    UTILITY_KEYWORDS = {'ccf', 'therm', 'therms', 'btu', 'gallon', 'gallons', 'gpm',
                        'mcf', 'cubic feet', 'gj', 'natural gas', 'water', 'gas'}

    raw_name = None
    point_names = []

    for col in columns:
        if col != 'timestamp' and ' - ' in col:
            prefix, point = col.split(' - ', 1)
            point_names.append(point.strip().lower())
            if raw_name is None:
                raw_name = prefix.strip()

    if raw_name is None:
        return None

    # Check for multiple distinct meter name prefixes
    all_prefixes = set()
    for col in columns:
        if col != 'timestamp' and ' - ' in col:
            all_prefixes.add(col.split(' - ', 1)[0].strip())
    if len(all_prefixes) > 1:
        from collections import Counter
        prefix_counts = Counter(
            col.split(' - ', 1)[0].strip()
            for col in columns
            if col != 'timestamp' and ' - ' in col
        )
        raw_name = prefix_counts.most_common(1)[0][0]
        print(f"  WARNING: Multiple meter name prefixes detected: {all_prefixes}. Using most common: '{raw_name}'")

    # Classify meter type from point names
    is_electrical = any(any(kw in pn for kw in ELECTRICAL_KEYWORDS) for pn in point_names)
    is_utility = any(any(kw in pn for kw in UTILITY_KEYWORDS) for pn in point_names)

    if is_electrical and not is_utility:
        type_prefix = 'power-meter-'
        print(f"  Detected meter type: electrical (prefix: '{type_prefix}')")
    elif is_utility and not is_electrical:
        type_prefix = 'utility-'
        print(f"  Detected meter type: gas/water (prefix: '{type_prefix}')")
    else:
        # Ambiguous or unrecognized - prompt user
        print(f"  Could not auto-detect meter type from point names: {[col.split(' - ', 1)[1].strip() for col in columns if col != 'timestamp' and ' - ' in col]}")
        print("  Select meter type:")
        print("    1. Electrical (power-meter-)")
        print("    2. Gas / Water (utility-)")
        choice = input("  Enter choice (1 or 2): ").strip()
        type_prefix = 'power-meter-' if choice == '1' else 'utility-'

    full_name = type_prefix + raw_name
    print(f"  Detected meter name: '{full_name}'")
    return full_name

def read_mapping_csv(mapping_path):
    """
    Read and validate a mapping CSV that drives batch-combine processing.

    Expected columns: building, meter_name, start_date, end_date
    Optional column: externalID (filled with empty string if absent)

    Args:
        mapping_path: Path to the mapping CSV file
    Returns:
        List of dicts, one per row
    Raises:
        ValueError: If required columns are missing or the file cannot be read
    """
    if not os.path.exists(mapping_path):
        raise ValueError(f"Mapping CSV not found: {mapping_path}")

    try:
        df = pd.read_csv(mapping_path, dtype=str).fillna('')
    except Exception as e:
        raise ValueError(f"Failed to read mapping CSV: {e}")

    required = {'building', 'meter_name', 'start_date', 'end_date'}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Mapping CSV is missing required columns: {missing}")

    if 'externalID' not in df.columns:
        df['externalID'] = ''

    rows = df[['building', 'meter_name', 'externalID', 'start_date', 'end_date']].to_dict('records')
    print(f"  Loaded {len(rows)} mapping row(s) from: {os.path.basename(mapping_path)}")
    return rows


def detect_meter_name_from_file(filepath):
    """
    Detect the meter name from a CSV file by reading only its headers.
    Uses nrows=0 to avoid loading the full file.

    Args:
        filepath: Path to CSV file
    Returns:
        Detected meter name string with type prefix, or None if not detectable
    """
    try:
        df_headers = pd.read_csv(filepath, nrows=0)
        return detect_meter_name(list(df_headers.columns))
    except Exception as e:
        print(f"  WARNING: Could not read headers from {os.path.basename(filepath)}: {e}")
        return None


def group_files_by_meter(csv_files):
    """
    Group CSV files by their detected meter name (read from column headers only).

    Args:
        csv_files: List of absolute paths to CSV files
    Returns:
        Dict mapping meter_name -> list of file paths
    """
    groups = {}
    skipped = []

    for filepath in csv_files:
        meter_name = detect_meter_name_from_file(filepath)
        if meter_name is None:
            print(f"  WARNING: Could not detect meter name for {os.path.basename(filepath)} — skipping")
            skipped.append(filepath)
            continue
        groups.setdefault(meter_name, []).append(filepath)

    print(f"\n  Detected {len(groups)} distinct meter group(s):")
    for name, files in groups.items():
        print(f"    '{name}': {len(files)} file(s)")
    if skipped:
        print(f"  Skipped {len(skipped)} file(s) with undetectable meter names")

    return groups


def trim_to_date_window(df, start_date, end_date):
    """
    Filter DataFrame rows to those whose timestamp falls within [start_date, end_date] inclusive,
    evaluated in America/Los_Angeles timezone.

    Operates on raw timestamps (before the format_timestamps pipeline step).

    Args:
        df: DataFrame with a 'timestamp' column (raw ISO8601 strings)
        start_date: Start date string in YYYY-MM-DD format (inclusive)
        end_date: End date string in YYYY-MM-DD format (inclusive)
    Returns:
        Filtered DataFrame (preserves original column format)
    """
    ts_utc = pd.to_datetime(df['timestamp'], utc=True, errors='coerce')
    ts_la = ts_utc.dt.tz_convert('America/Los_Angeles')

    start = pd.Timestamp(start_date, tz='America/Los_Angeles')
    end = pd.Timestamp(end_date, tz='America/Los_Angeles') + pd.Timedelta(days=1)

    mask = (ts_la >= start) & (ts_la < end)
    result = df[mask].reset_index(drop=True)

    if result.empty:
        print(f"  WARNING: No rows fall within date window {start_date} to {end_date}")
    else:
        print(f"  Trimmed to {len(result)} row(s) within {start_date} to {end_date}")

    return result


def get_csv_files_from_directory(directory_path):
    """
    Get all CSV files from a directory (non-recursive).

    Args:
        directory_path: Path to directory
    Returns:
        List of absolute paths to CSV files
    """
    if not os.path.exists(directory_path):
        print(f"ERROR: Directory does not exist: {directory_path}")
        return []

    if not os.path.isdir(directory_path):
        print(f"ERROR: Path is not a directory: {directory_path}")
        return []

    csv_files = []
    for filename in os.listdir(directory_path):
        filepath = os.path.join(directory_path, filename)

        # Skip directories
        if os.path.isdir(filepath):
            continue

        # Check if it's a CSV file
        if filename.lower().endswith('.csv'):
            csv_files.append(os.path.abspath(filepath))

    if csv_files:
        print(f"Found {len(csv_files)} CSV file(s) in directory")
    else:
        print(f"No CSV files found in directory: {directory_path}")

    return csv_files

def validate_mango_csv(filepath):
    """
    Validate that the input file is a valid Mango CSV export.

    Args:
        filepath: Path to CSV file
    Returns:
        True if valid, False otherwise
    """
    if not os.path.exists(filepath):
        print(f"ERROR: File does not exist: {filepath}")
        return False

    if not os.path.isfile(filepath):
        print(f"ERROR: Path is not a file: {filepath}")
        return False

    file_extension = os.path.splitext(filepath)[1].lower()
    if file_extension != '.csv':
        print(f"ERROR: File must be .csv format. Got: {file_extension}")
        return False

    # Try to read and check for timestamp column
    try:
        df = pd.read_csv(filepath, nrows=5)
        if 'timestamp' not in df.columns:
            print("ERROR: CSV must contain a 'timestamp' column")
            return False

        # Check if there are any measurement columns
        measurement_cols = [col for col in df.columns if col != 'timestamp']
        if not measurement_cols:
            print("WARNING: No measurement columns found besides timestamp")

        print(f"Valid CSV detected: {os.path.basename(filepath)}")
        print(f"  Columns found: {len(df.columns)} ({len(measurement_cols)} measurement columns)")
        return True

    except Exception as e:
        print(f"ERROR: Failed to read CSV: {str(e)}")
        return False

def get_combine_metadata(args):
    """
    Get building and meter_name for combine operation from CLI args or interactively.

    Args:
        args: Parsed command-line arguments (or None for interactive mode)
    Returns:
        Tuple of (building, meter_name)
    """
    if args and args.building and hasattr(args, 'meter') and args.meter:
        return args.building, args.meter

    print("\n" + "=" * 60)
    print("COMBINE METADATA INPUT")
    print("=" * 60)

    if not (args and args.building):
        building = input("Enter building identifier (required): ").strip()
        while not building:
            print("Building is required.")
            building = input("Enter building identifier (required): ").strip()
    else:
        building = args.building

    meter_name = input("Enter meter name (press Enter to auto-detect from columns): ").strip()
    # Return None to signal auto-detection if user skips
    return building, meter_name if meter_name else None


def combine_mango_csv_files(directory, building, meter_name=None, output_dir=None):
    """
    Combine all mango CSV files in a directory into a single deduped, sorted CSV.

    - Validates all files share identical columns before combining
    - Removes duplicate rows identified by the same timestamp
    - Sorts output by ascending timestamp
    - Saves as: {building}_{meter_name}_{min_date}_to_{max_date}.csv

    Args:
        directory: Path to directory containing mango CSV files
        building: Building identifier for the output filename
        meter_name: Meter name for the output filename
        output_dir: Directory to write output (defaults to input directory)
    """
    print("\n" + "=" * 60)
    print("COMBINING MANGO CSV FILES")
    print("=" * 60)

    # Step 1: Get CSV files
    print("\n[1/6] Scanning directory for CSV files...")
    csv_files = get_csv_files_from_directory(directory)
    if not csv_files:
        raise ValueError(f"No CSV files found in directory: {directory}")

    # Step 2: Validate each file
    print("\n[2/6] Validating CSV files...")
    valid_files = [f for f in csv_files if validate_mango_csv(f)]
    if not valid_files:
        raise ValueError("No valid mango CSV files found after validation.")
    print(f"  {len(valid_files)}/{len(csv_files)} file(s) passed validation")

    # Step 3: Read all CSVs and check column consistency
    print("\n[3/6] Reading files and checking column consistency...")
    dfs = []
    reference_columns = None
    reference_filename = None
    mismatched = []

    for filepath in valid_files:
        df = pd.read_csv(filepath)
        cols = list(df.columns)

        if reference_columns is None:
            reference_columns = cols
            reference_filename = os.path.basename(filepath)
        elif set(cols) != set(reference_columns):
            mismatched.append(os.path.basename(filepath))
        else:
            pass  # columns match

        dfs.append(df)

    if mismatched:
        raise ValueError(
            f"Column mismatch detected. Reference file: '{reference_filename}' "
            f"with columns {reference_columns}. "
            f"Mismatched file(s): {mismatched}"
        )
    print(f"  All {len(valid_files)} file(s) have consistent columns: {reference_columns}")

    # Auto-detect meter name from column names if not provided
    if meter_name is None:
        print("\n  Auto-detecting meter name from column names...")
        meter_name = detect_meter_name(reference_columns)
        if meter_name is None:
            raise ValueError(
                "Could not detect meter name from column names (no 'meter_name - pointName' pattern found). "
                "Provide a meter name with --meter or interactively."
            )

    # Step 4: Concatenate
    print("\n[4/6] Concatenating files...")
    combined = pd.concat(dfs, ignore_index=True)
    print(f"  Combined row count: {len(combined)}")

    # Step 5: Remove duplicate timestamps, sort ascending
    print("\n[5/6] Removing duplicate timestamps and sorting...")
    before_dedup = len(combined)
    combined = combined.drop_duplicates(subset=['timestamp'], keep='first')
    removed = before_dedup - len(combined)
    print(f"  Removed {removed} duplicate row(s), {len(combined)} rows remaining")
    combined = combined.sort_values('timestamp').reset_index(drop=True)
    print("  Sorted by ascending timestamp")

    # Step 6: Determine date range and save
    print("\n[6/6] Saving output...")
    ts = pd.to_datetime(combined['timestamp'], utc=True, errors='coerce')
    min_date = ts.min().date().strftime('%Y-%m-%d')
    max_date = ts.max().date().strftime('%Y-%m-%d')

    out_dir = output_dir if output_dir else os.path.dirname(os.path.abspath(valid_files[0]))
    output_filename = f"{building}_{meter_name}_{min_date}_to_{max_date}.csv"
    output_path = os.path.join(out_dir, output_filename)

    combined.to_csv(output_path, index=False, quoting=csv.QUOTE_NONNUMERIC, float_format='%.10g')

    print(f"  Output saved to: {output_path}")
    print(f"  Final shape: {len(combined)} rows, {len(combined.columns)} columns")
    print(f"  Date range: {min_date} to {max_date}")
    print("=" * 60)
    print("\nCombine complete!")


def get_batch_combine_inputs(args):
    """
    Get the raw-files directory and mapping CSV path for batch-combine mode.

    Args:
        args: Parsed command-line arguments (or None for interactive mode)
    Returns:
        Tuple of (directory_path, mapping_csv_path)
    """
    if args and getattr(args, 'batch_combine', None) and getattr(args, 'mapping', None):
        return args.batch_combine, args.mapping

    print("\n" + "=" * 60)
    print("BATCH COMBINE INPUT")
    print("=" * 60)

    directory = input("Enter directory path containing raw CSV files: ").strip()
    while not directory:
        print("Directory path is required.")
        directory = input("Enter directory path containing raw CSV files: ").strip()

    mapping_path = input("Enter path to mapping CSV: ").strip()
    while not mapping_path:
        print("Mapping CSV path is required.")
        mapping_path = input("Enter path to mapping CSV: ").strip()

    return directory, mapping_path


def batch_combine_from_mapping(directory, mapping_path, output_dir=None):
    """
    Batch-combine workflow: detect meter names from a flat directory of raw CSV files,
    match against a mapping CSV, and for each mapping row combine + trim + reformat the data.

    Mapping CSV columns: building, meter_name, externalID (optional), start_date, end_date
    Output: one reformatted CSV per mapping row, named:
        {building}_{meter_name}_{externalID}_{start_date}_{end_date}_mango.csv
        (externalID segment omitted if empty)

    Args:
        directory: Path to flat directory of raw Mango CSV files
        mapping_path: Path to the mapping CSV file
        output_dir: Directory to write output files (defaults to input directory)
    """
    print("\n" + "=" * 60)
    print("BATCH COMBINE FROM MAPPING CSV")
    print("=" * 60)

    # Step 1: Read mapping CSV
    print("\n[1/5] Reading mapping CSV...")
    mapping_rows = read_mapping_csv(mapping_path)

    # Step 2: Scan directory for CSV files
    print("\n[2/5] Scanning directory for CSV files...")
    csv_files = get_csv_files_from_directory(directory)
    if not csv_files:
        raise ValueError(f"No CSV files found in directory: {directory}")

    # Step 3: Group files by detected meter name (reads headers only)
    print("\n[3/5] Detecting meter names and grouping files...")
    meter_groups = group_files_by_meter(csv_files)

    # Step 4: Combine files once per unique meter_name referenced in the mapping
    print("\n[4/5] Combining files per meter...")
    unique_meters = list(dict.fromkeys(row['meter_name'] for row in mapping_rows))
    combined_by_meter = {}

    for meter_name in unique_meters:
        files = meter_groups.get(meter_name)
        if not files:
            print(f"  WARNING: No files detected for meter '{meter_name}' — skipping all mapping rows for this meter")
            continue

        print(f"\n  Combining {len(files)} file(s) for '{meter_name}'...")
        dfs = []
        for filepath in files:
            if validate_mango_csv(filepath):
                dfs.append(pd.read_csv(filepath))

        if not dfs:
            print(f"  WARNING: No valid files for '{meter_name}' — skipping")
            continue

        combined = pd.concat(dfs, ignore_index=True)
        before = len(combined)
        combined = combined.drop_duplicates(subset=['timestamp'], keep='first')
        combined = combined.sort_values('timestamp').reset_index(drop=True)
        print(f"  Combined: {len(combined)} rows ({before - len(combined)} duplicates removed)")
        combined_by_meter[meter_name] = combined

    # Step 5: Process each mapping row
    print("\n[5/5] Processing mapping rows...")
    out_dir = output_dir if output_dir else os.path.dirname(os.path.abspath(csv_files[0]))

    created = 0
    skipped = 0

    for idx, row in enumerate(mapping_rows, 1):
        meter_name = row['meter_name']
        building = row['building']
        external_id = row.get('externalID', '').strip()
        start_date = row['start_date'].strip()
        end_date = row['end_date'].strip()

        print(f"\n  [{idx}/{len(mapping_rows)}] {meter_name} | {start_date} to {end_date}" +
              (f" | externalID={external_id}" if external_id else ""))

        if meter_name not in combined_by_meter:
            print(f"    Skipping (no combined data for this meter)")
            skipped += 1
            continue

        # Trim to date window
        df = trim_to_date_window(combined_by_meter[meter_name].copy(), start_date, end_date)
        if df.empty:
            print(f"    Skipping (no data in date window)")
            skipped += 1
            continue

        # Run the full reformatting pipeline
        df = remove_rendered_columns(df)
        df = rename_point_columns(df)
        df = format_timestamps(df)
        df = restructure_to_paired_columns(df)
        df = add_metadata_columns(df, building, device=meter_name, external_id=external_id)

        # Build output filename
        ext_segment = f"_{external_id}" if external_id else ""
        output_filename = f"{building}_{meter_name}{ext_segment}_{start_date}_{end_date}_mango.csv"
        output_path = os.path.join(out_dir, output_filename)

        df.to_csv(output_path, index=False, quoting=csv.QUOTE_NONNUMERIC, float_format='%.10g')
        print(f"    Saved: {output_filename}  ({len(df)} rows)")
        created += 1

    # Summary
    print("\n" + "=" * 60)
    print("BATCH COMBINE SUMMARY")
    print("=" * 60)
    print(f"  Mapping rows processed : {len(mapping_rows)}")
    print(f"  Output files created   : {created}")
    print(f"  Rows skipped           : {skipped}")
    print("=" * 60)
    print("\nBatch combine complete!")


def get_user_metadata(args):
    """
    Get metadata from command-line args or prompt user interactively.

    Args:
        args: Parsed command-line arguments
    Returns:
        Tuple of (building, device, external_id)
    """
    # Check if we have CLI arguments
    if args and args.building and args.device:
        building = args.building
        device = args.device
        external_id = args.externalid if hasattr(args, 'externalid') and args.externalid else ''
    else:
        # Interactive mode
        print("\n" + "=" * 60)
        print("METADATA INPUT")
        print("=" * 60)
        building = input("Enter building identifier (required): ").strip()
        while not building:
            print("Building is required.")
            building = input("Enter building identifier (required): ").strip()

        device = input("Enter device identifier (required): ").strip()
        while not device:
            print("Device is required.")
            device = input("Enter device identifier (required): ").strip()

        external_id = input("Enter external device ID (optional, press Enter to skip): ").strip()

    return building, device, external_id

def process_mango_export(input_path, output_path, building, device, external_id):
    """
    Main processing function that orchestrates the entire transformation.

    Args:
        input_path: Path to input Mango CSV export
        output_path: Path for output CSV file
        building: Building identifier
        device: Device identifier
        external_id: External device ID (optional)
    Returns:
        Tuple of (start_date, end_date) strings in YYYY-MM-DD format
    """
    try:
        print("\n" + "=" * 60)
        print("PROCESSING MANGO EXPORT")
        print("=" * 60)

        # Step 1: Read CSV
        print("\n[1/7] Reading CSV file...")
        df = pd.read_csv(input_path)
        print(f"  Loaded {len(df)} rows, {len(df.columns)} columns")

        # Step 2: Validate
        print("\n[2/7] Validating data...")
        if 'timestamp' not in df.columns:
            raise ValueError("CSV must contain a 'timestamp' column")
        print("  Validation passed")

        # Step 3: Remove _rendered columns
        print("\n[3/7] Removing _rendered columns...")
        df = remove_rendered_columns(df)

        # Step 4: Rename measurement columns
        print("\n[4/7] Renaming measurement columns...")
        df = rename_point_columns(df)

        # Step 5: Format timestamps
        print("\n[5/7] Formatting timestamps...")
        df = format_timestamps(df)

        # Extract date range for filename
        start_date = df.timestamp.min().date().strftime('%Y-%m-%d')
        end_date = df.timestamp.max().date().strftime('%Y-%m-%d')

        # Step 6: Restructure to paired columns
        print("\n[6/7] Restructuring to pointName/value pairs...")
        df = restructure_to_paired_columns(df)

        # Step 7: Add metadata columns
        print("\n[7/7] Adding metadata columns...")
        df = add_metadata_columns(df, building, device, external_id)

        # Save to output
        print("\n" + "=" * 60)
        print("SAVING OUTPUT")
        print("=" * 60)
        df.to_csv(output_path, index=False, quoting=csv.QUOTE_NONNUMERIC, float_format='%.10g')
        print(f"Output saved to: {output_path}")
        print(f"Final shape: {len(df)} rows, {len(df.columns)} columns")
        print(f"Date range: {start_date} to {end_date}")
        print("=" * 60)
        print("\nProcessing complete!")

        return start_date, end_date

    except Exception as e:
        print("\n" + "=" * 60)
        print("ERROR DURING PROCESSING")
        print("=" * 60)
        print(f"Error: {str(e)}")
        print("=" * 60)
        raise

def parse_arguments():
    """
    Parse command-line arguments.

    Returns:
        Parsed arguments or None if user wants interactive mode
    """
    parser = argparse.ArgumentParser(
        description='Mango CSV Reformatter - Process Mango CSV exports',
        epilog='If no arguments provided, interactive mode will be used.',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # Create mutually exclusive group for input sources
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        '-i', '--input',
        type=str,
        help='Path to input Mango CSV export file'
    )
    input_group.add_argument(
        '-dir', '--directory',
        type=str,
        help='Path to directory containing CSV files (processes all CSV files, non-recursive)'
    )
    input_group.add_argument(
        '-cd', '--combine-dir',
        type=str,
        dest='combine_dir',
        help='Path to directory containing mango CSV files to combine into a single output file'
    )
    input_group.add_argument(
        '-bc', '--batch-combine',
        type=str,
        dest='batch_combine',
        help='Path to flat directory of raw CSV files for batch-combine mode (requires --mapping)'
    )

    parser.add_argument(
        '-b', '--building',
        type=str,
        help='Building identifier (e.g., US-MTV-1708)'
    )

    parser.add_argument(
        '-d', '--device',
        type=str,
        help='Device identifier (e.g., MAIN_device)'
    )

    parser.add_argument(
        '-m', '--meter',
        type=str,
        help='Meter name for combined output filename (used with --combine-dir)'
    )

    parser.add_argument(
        '--mapping',
        type=str,
        help='Path to mapping CSV file (used with --batch-combine)'
    )

    parser.add_argument(
        '-e', '--externalid',
        type=str,
        help='External device ID (optional)'
    )

    parser.add_argument(
        '-o', '--output',
        type=str,
        help='Output directory path for batch processing (default: same as input location)'
    )

    args = parser.parse_args()

    # If no input provided, return None to trigger interactive mode
    if args.input is None and args.directory is None and args.combine_dir is None and args.batch_combine is None:
        return None

    return args

### MAIN
if __name__ == "__main__":
    print("=" * 60)
    print("Mango CSV Reformatter")
    print("Processes Mango exports with timestamp formatting")
    print("=" * 60)
    print()

    # Parse command-line arguments
    args = parse_arguments()

    try:
        # Collect input files
        files_to_process = []

        if args and args.input:
            # Single file mode (CLI)
            if not validate_mango_csv(args.input):
                print("\nExiting due to invalid input file.")
                sys.exit(1)
            files_to_process = [os.path.abspath(args.input)]

        elif args and args.directory:
            # Directory mode (CLI)
            files_to_process = get_csv_files_from_directory(args.directory)
            if not files_to_process:
                print("\nExiting: No CSV files found in directory.")
                sys.exit(1)
            # Validate all files
            valid_files = []
            for filepath in files_to_process:
                if validate_mango_csv(filepath):
                    valid_files.append(filepath)
            files_to_process = valid_files
            if not files_to_process:
                print("\nExiting: No valid CSV files found.")
                sys.exit(1)

        elif args and args.combine_dir:
            # Combine mode (CLI)
            out_dir = args.output if args.output else None
            if out_dir and not os.path.exists(out_dir):
                os.makedirs(out_dir)
            building, meter_name = get_combine_metadata(args)
            combine_mango_csv_files(args.combine_dir, building, meter_name, output_dir=out_dir)
            sys.exit(0)

        elif args and args.batch_combine:
            # Batch-combine mode (CLI)
            if not args.mapping:
                print("ERROR: --batch-combine requires --mapping <path-to-mapping-csv>")
                sys.exit(1)
            out_dir = args.output if args.output else None
            if out_dir and not os.path.exists(out_dir):
                os.makedirs(out_dir)
            batch_combine_from_mapping(args.batch_combine, args.mapping, output_dir=out_dir)
            sys.exit(0)

        else:
            # Interactive mode
            print("Interactive Mode")
            print("(Use --help to see command-line options)")
            print()
            print("Choose input mode:")
            print("  1. Process a single file")
            print("  2. Process all CSV files in a directory")
            print("  3. Combine multiple mango CSV files from a directory into one")
            print("  4. Batch combine from mapping CSV (auto-detect meters, trim to date windows)")
            print()

            choice = input("Enter choice (1-4): ").strip()

            if choice == '1':
                # Single file mode
                input_file = input("\nEnter path to Mango CSV export: ").strip()
                if not validate_mango_csv(input_file):
                    print("\nExiting due to invalid input file.")
                    sys.exit(1)
                files_to_process = [os.path.abspath(input_file)]

            elif choice == '2':
                # Directory mode
                input_dir = input("\nEnter directory path: ").strip()
                files_to_process = get_csv_files_from_directory(input_dir)
                if not files_to_process:
                    print("\nExiting: No CSV files found in directory.")
                    sys.exit(1)
                # Validate all files
                valid_files = []
                for filepath in files_to_process:
                    if validate_mango_csv(filepath):
                        valid_files.append(filepath)
                files_to_process = valid_files
                if not files_to_process:
                    print("\nExiting: No valid CSV files found.")
                    sys.exit(1)

            elif choice == '3':
                # Combine mode
                input_dir = input("\nEnter directory path containing mango CSV files: ").strip()
                building, meter_name = get_combine_metadata(None)
                out_dir = None
                combine_mango_csv_files(input_dir, building, meter_name, output_dir=out_dir)
                sys.exit(0)

            elif choice == '4':
                # Batch-combine from mapping CSV
                directory, mapping_path = get_batch_combine_inputs(None)
                batch_combine_from_mapping(directory, mapping_path)
                sys.exit(0)

            else:
                print("\nInvalid choice. Exiting.")
                sys.exit(1)

        # Determine output directory
        if args and args.output:
            output_dir = args.output
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
        else:
            # Use directory of first input file
            output_dir = os.path.dirname(os.path.abspath(files_to_process[0]))

        print(f"\nOutput directory: {output_dir}")

        # Process all files
        if len(files_to_process) > 1:
            print("\n" + "=" * 60)
            print(f"Processing {len(files_to_process)} file(s)...")
            print("=" * 60)

        successful = 0
        failed = 0
        failed_files = []

        for idx, input_file in enumerate(files_to_process, 1):
            try:
                if len(files_to_process) > 1:
                    print(f"\n[{idx}/{len(files_to_process)}] Processing: {os.path.basename(input_file)}")

                    # In batch mode, prompt for metadata for EACH file
                    # (Unless using CLI with -b and -d flags provided for all files)
                    if args and args.building and args.device:
                        # CLI mode with metadata provided - use same for all files
                        building, device, external_id = get_user_metadata(args)
                    else:
                        # Interactive batch mode - prompt for each file
                        print(f"\nEnter metadata for: {os.path.basename(input_file)}")
                        building, device, external_id = get_user_metadata(None)
                else:
                    # Single file mode - get metadata once
                    building, device, external_id = get_user_metadata(args)

                # First, we need to process the file to get the date range
                # Create a temporary output path, then rename after we get dates
                temp_output = os.path.join(output_dir, f"temp_{os.path.basename(input_file)}")

                # Process the file and get date range
                start_date, end_date = process_mango_export(input_file, temp_output, building, device, external_id)

                # Generate final output filename with date range
                output_file = f"{building}_{device}_{start_date}_{end_date}_mango.csv"
                final_output_path = os.path.join(output_dir, output_file)

                # Rename temp file to final filename
                if os.path.exists(final_output_path):
                    os.remove(final_output_path)
                os.rename(temp_output, final_output_path)

                print(f"Final output: {output_file}")
                successful += 1

            except Exception as e:
                failed += 1
                failed_files.append((input_file, str(e)))
                if len(files_to_process) > 1:
                    print(f"  ✗ Failed: {str(e)}")
                else:
                    raise

        # Print summary if multiple files
        if len(files_to_process) > 1:
            print("\n" + "=" * 60)
            print("PROCESSING SUMMARY")
            print("=" * 60)
            print(f"Total files: {len(files_to_process)}")
            print(f"Successful: {successful}")
            print(f"Failed: {failed}")

            if failed_files:
                print("\nFailed files:")
                for filepath, error in failed_files:
                    print(f"  - {os.path.basename(filepath)}: {error}")

            print("=" * 60)

    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\nFatal error: {str(e)}")
        sys.exit(1)
