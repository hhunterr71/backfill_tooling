"""
Carson Backfill Tool
====================
Combines Mango export reformatting and backfill formatting into a single
job-oriented workflow.

Job workflow:
  S → Setup new job  (creates raw/ combined/ output/ + mapping template)
  1 → Flatten raw files          (rename files in raw/)
  2 → Batch combine from mapping (raw/ + mapping.csv → combined/)
  3 → Process combined files     (combined/ → output/ backfill folders)
  4 → Run backfill               (execute all output/ folders via blaze)
"""

import subprocess
import pandas as pd
import os
import sys
import csv
import json
import logging
import datetime
import re
import io
import contextlib
import shutil
from argparse import ArgumentParser
from collections import Counter

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

dirname = os.path.dirname(os.path.abspath(__file__))


# ============================================================
# SECTION 1 — SHARED UTILITIES
# ============================================================

class ResetException(Exception):
    """Raised when the user wants to restart the interactive loop."""
    pass


def check_special_input(user_input):
    """Handle 'quit'/'exit' and 'reset' commands from interactive prompts."""
    cleaned = user_input.strip().lower()
    if cleaned in ['quit', 'exit', 'q']:
        print("\nExiting program...")
        sys.exit(0)
    elif cleaned == 'reset':
        print("\nRestarting from the beginning...\n")
        raise ResetException()


def check_input(input_path):
    """Validate that input_path is an existing CSV or XLSX file."""
    if not os.path.exists(input_path):
        print(f"ERROR: File does not exist: {input_path}")
        return False
    if os.path.isdir(input_path):
        print("ERROR: Path is a directory. Please provide a file.")
        return False
    if not os.path.isfile(input_path):
        print(f"ERROR: Path is not a valid file: {input_path}")
        return False
    ext = os.path.splitext(input_path)[1].lower()
    if ext not in ['.csv', '.xlsx', '.xls']:
        print(f"ERROR: File must be .csv or .xlsx format. Got: {ext}")
        return False
    print(f"Valid {ext.upper()} file detected: {os.path.basename(input_path)}")
    return True


def format_timestamps(df):
    """
    Unified timestamp formatting.
    - Parses ISO8601 timestamps, normalises to UTC, then converts to America/Los_Angeles
    - Adds 15-minute offset for BixBox aggregation compatibility
    - Skips the 15-min offset if timestamps are already timezone-aware (prevents double-processing)
    - Raises ValueError if any timestamps cannot be parsed
    """
    original_count = len(df)
    original_timestamps = df.timestamp.copy()

    # Detect if input is already timezone-aware (already processed by this tool)
    already_tz_aware = False
    if len(df) > 0:
        first_ts = str(df.timestamp.iloc[0])
        tz_pattern = r'[+-]\d{2}:\d{2}$'
        already_tz_aware = (
            bool(re.search(tz_pattern, first_ts)) or
            (hasattr(df.timestamp.iloc[0], 'tz') and df.timestamp.iloc[0].tz is not None)
        )

    df.timestamp = pd.to_datetime(df.timestamp, utc=True)

    nat_count = df.timestamp.isna().sum()
    if nat_count > 0:
        failed_examples = original_timestamps[df.timestamp.isna()].head(5).tolist()
        raise ValueError(
            f"Failed to parse {nat_count} timestamp(s) out of {original_count}. "
            f"Examples: {failed_examples}"
        )

    df.timestamp = df.timestamp.dt.tz_convert('America/Los_Angeles')

    if already_tz_aware:
        print("  Timestamps already formatted (timezone-aware). Skipping 15-min offset.")
    else:
        df.timestamp = df.timestamp + pd.Timedelta(minutes=15)
        print("  Timestamps converted to Pacific timezone with 15-minute offset")

    return df


def run_prerequisites():
    """
    Run environment setup for the backfill client:
      bash -c 'cd "$(p4 g4d backfill)" && g4 sync && pwd'
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
        print(f"  Environment setup failed (exit code {result.returncode}). Aborting.")
        return None
    print(f"  Environment ready (client root: {client_root})")
    return client_root


def get_files_from_directory(directory_path, csv_only=False):
    """
    Get all valid files from a directory (non-recursive).
    csv_only=True: returns only .csv files.
    csv_only=False: returns .csv, .xlsx, and .xls files.
    """
    if not os.path.exists(directory_path):
        print(f"ERROR: Directory does not exist: {directory_path}")
        return []
    if not os.path.isdir(directory_path):
        print(f"ERROR: Path is not a directory: {directory_path}")
        return []

    valid_exts = ['.csv'] if csv_only else ['.csv', '.xlsx', '.xls']
    valid_files = []

    for filename in os.listdir(directory_path):
        filepath = os.path.join(directory_path, filename)
        if os.path.isdir(filepath):
            continue
        if os.path.splitext(filename)[1].lower() in valid_exts:
            valid_files.append(os.path.abspath(filepath))

    if valid_files:
        print(f"Found {len(valid_files)} file(s) in directory")
    else:
        ext_desc = 'CSV' if csv_only else 'CSV/XLSX'
        print(f"No {ext_desc} files found in: {directory_path}")

    return valid_files


# ============================================================
# SECTION 2 — MANGO PROCESSING FUNCTIONS
# ============================================================

def validate_mango_csv(filepath):
    """Validate that a CSV file exists and contains a timestamp column."""
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        return False
    if os.path.splitext(filepath)[1].lower() != '.csv':
        return False
    try:
        df = pd.read_csv(filepath, nrows=5)
        return 'timestamp' in df.columns
    except Exception:
        return False


def remove_rendered_columns(df):
    """Remove any columns whose name contains '_rendered'."""
    rendered_cols = [col for col in df.columns if '_rendered' in col.lower()]
    if rendered_cols:
        print(f"  Removing {len(rendered_cols)} _rendered column(s)")
        df = df.drop(columns=rendered_cols)
    return df


def rename_point_columns(df):
    """Rename 'meter_name - pointName' columns to just 'pointName'."""
    new_columns = {}
    for col in df.columns:
        if col != 'timestamp' and ' - ' in col:
            new_columns[col] = col.split(' - ', 1)[1]
    if new_columns:
        print(f"  Renaming {len(new_columns)} measurement column(s)")
        df = df.rename(columns=new_columns)
    return df


def restructure_to_paired_columns(df):
    """
    Convert wide measurement columns to paired pointName/value columns.
    Input:  timestamp, kW, kWh, Temperature
    Output: timestamp, pointName1, value1, pointName2, value2, ...
    """
    measurement_cols = [col for col in df.columns if col != 'timestamp']
    if not measurement_cols:
        return df
    new_df = pd.DataFrame()
    new_df['timestamp'] = df['timestamp']
    for idx, col_name in enumerate(measurement_cols, 1):
        new_df[f'pointName{idx}'] = col_name
        new_df[f'value{idx}'] = df[col_name]
    print(f"  Restructured {len(measurement_cols)} column(s) into pointName/value pairs")
    return new_df


def add_metadata_columns(df, building, device, external_id):
    """Insert building, device, externalID columns after timestamp."""
    df.insert(1, 'building', building)
    df.insert(2, 'device', device)
    df.insert(3, 'externalID', external_id if external_id else '')
    print(f"  Added metadata: building={building}, device={device}")
    return df


def detect_meter_name(columns, no_prompt=False):
    """
    Detect meter name from column names formatted as 'meter_name - pointName'.
    Returns 'power-meter-{name}' for electrical or 'utility-{name}' for gas/water meters.
    Returns None if detection fails and no_prompt=True.
    """
    ELECTRICAL_KEYWORDS = {
        'kw', 'kwh', 'kvar', 'kva', 'pf', 'power factor', 'amp', 'amps',
        'volt', 'volts', 'hz', 'current', 'voltage', 'demand',
        'real power', 'reactive power', 'apparent power',
    }
    UTILITY_KEYWORDS = {
        'ccf', 'therm', 'therms', 'btu', 'gallon', 'gallons', 'gpm',
        'mcf', 'cubic feet', 'gj', 'natural gas', 'water', 'gas',
    }

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

    # If multiple distinct prefixes exist, use the most common one
    all_prefixes = {
        col.split(' - ', 1)[0].strip()
        for col in columns
        if col != 'timestamp' and ' - ' in col
    }
    if len(all_prefixes) > 1:
        prefix_counts = Counter(
            col.split(' - ', 1)[0].strip()
            for col in columns
            if col != 'timestamp' and ' - ' in col
        )
        raw_name = prefix_counts.most_common(1)[0][0]
        print(f"  WARNING: Multiple meter prefixes detected: {all_prefixes}. Using: '{raw_name}'")

    is_electrical = any(any(kw in pn for kw in ELECTRICAL_KEYWORDS) for pn in point_names)
    is_utility = any(any(kw in pn for kw in UTILITY_KEYWORDS) for pn in point_names)

    if is_electrical and not is_utility:
        type_prefix = 'power-meter-'
    elif is_utility and not is_electrical:
        type_prefix = 'utility-'
    else:
        point_list = [
            col.split(' - ', 1)[1].strip()
            for col in columns
            if col != 'timestamp' and ' - ' in col
        ]
        if no_prompt:
            print(f"  WARNING: Could not auto-detect meter type for '{raw_name}'. Skipping.")
            return None
        print(f"  Could not auto-detect meter type from point names: {point_list}")
        print("  Select meter type:")
        print("    1. Electrical (power-meter-)")
        print("    2. Gas / Water (utility-)")
        choice = input("  Enter choice (1 or 2): ").strip()
        type_prefix = 'power-meter-' if choice == '1' else 'utility-'

    full_name = type_prefix + raw_name
    print(f"  Detected meter name: '{full_name}'")
    return full_name


def detect_meter_name_from_file(filepath, interactive=True):
    """Detect meter name from a CSV file by reading only its headers."""
    try:
        df_headers = pd.read_csv(filepath, nrows=0)
        return detect_meter_name(list(df_headers.columns), no_prompt=not interactive)
    except Exception as e:
        print(f"  WARNING: Could not read headers from {os.path.basename(filepath)}: {e}")
        return None


def group_files_by_meter(csv_files):
    """Group CSV files by their detected meter name (read from column headers only)."""
    groups = {}
    skipped = []
    for filepath in csv_files:
        meter_name = detect_meter_name_from_file(filepath, interactive=False)
        if meter_name is None:
            print(f"  WARNING: Could not detect meter for {os.path.basename(filepath)} — skipping")
            skipped.append(filepath)
            continue
        groups.setdefault(meter_name, []).append(filepath)
    print(f"\n  Detected {len(groups)} distinct meter group(s):")
    for name, files in groups.items():
        print(f"    '{name}': {len(files)} file(s)")
    if skipped:
        print(f"  Skipped {len(skipped)} file(s) with undetectable meter names")
    return groups


def read_mapping_csv(mapping_path):
    """
    Read and validate a mapping CSV.
    Required columns: building_id, meter_name, start_date, end_date
    Optional column:  external_id
    Returns a list of dicts, one per row.
    """
    if not os.path.exists(mapping_path):
        raise ValueError(f"Mapping CSV not found: {mapping_path}")
    try:
        df = pd.read_csv(mapping_path, dtype=str).fillna('')
    except Exception as e:
        raise ValueError(f"Failed to read mapping CSV: {e}")
    required = {'building_id', 'meter_name', 'start_date', 'end_date'}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Mapping CSV is missing required columns: {missing}")
    if 'technical_id' not in df.columns:
        df['technical_id'] = ''
    if 'external_id' not in df.columns:
        df['external_id'] = ''
    if 'type' not in df.columns:
        df['type'] = ''
    if 'bug_number' not in df.columns:
        df['bug_number'] = ''
    rows = df[['building_id', 'meter_name', 'technical_id', 'external_id', 'type',
               'bug_number', 'start_date', 'end_date']].to_dict('records')
    print(f"  Loaded {len(rows)} mapping row(s) from: {os.path.basename(mapping_path)}")
    return rows


def trim_to_date_window(df, start_date, end_date):
    """
    Filter DataFrame rows to those within [start_date, end_date] inclusive,
    evaluated in America/Los_Angeles timezone.
    """
    ts_utc = pd.to_datetime(df['timestamp'], utc=True, errors='coerce')
    ts_la = ts_utc.dt.tz_convert('America/Los_Angeles')
    start = pd.Timestamp(start_date, tz='America/Los_Angeles')
    end = pd.Timestamp(end_date, tz='America/Los_Angeles') + pd.Timedelta(days=1)
    mask = (ts_la >= start) & (ts_la < end)
    result = df[mask].reset_index(drop=True)
    if result.empty:
        print(f"  WARNING: No rows within date window {start_date} to {end_date}")
    else:
        print(f"  Trimmed to {len(result)} row(s) within {start_date} to {end_date}")
    return result


def _find_mapping_csv(directory):
    """
    Return the path to a single CSV file found directly inside directory
    (non-recursive), or None if zero or multiple exist.
    Used to auto-detect the mapping CSV when the user provides a project root.
    """
    try:
        root = os.path.abspath(directory)
        candidates = [
            os.path.join(root, f)
            for f in os.listdir(root)
            if f.lower().endswith('.csv') and os.path.isfile(os.path.join(root, f))
        ]
        return candidates[0] if len(candidates) == 1 else None
    except Exception:
        return None


def flatten_and_rename_directory(root_dir):
    """
    Flatten all CSV files within root_dir (or its raw/ subdir) by moving every
    CSV file to the raw/ level, renaming each to {meter_name}_{counter}_mango_export.csv.

    If root_dir contains a raw/ subdir, operates on raw/ automatically.
    If root_dir itself is named 'raw', operates directly there.
    Otherwise creates a raw/ subdir and moves files into it.
    """
    print("\n" + "=" * 60)
    print("FLATTEN AND RENAME DIRECTORY")
    print("=" * 60)

    root_dir = os.path.abspath(root_dir)

    # Auto-use raw/ subdir if the user gave the job root
    raw_subdir = os.path.join(root_dir, 'raw')
    if os.path.isdir(raw_subdir):
        root_dir = raw_subdir
        print(f"\n  raw/ subdir auto-detected: {root_dir}")

    if not os.path.exists(root_dir) or not os.path.isdir(root_dir):
        print(f"ERROR: Directory does not exist: {root_dir}")
        return

    # Destination: if already in a 'raw' dir, keep it; otherwise create raw/ inside
    if os.path.basename(root_dir).lower() == 'raw':
        raw_dir = root_dir
    else:
        raw_dir = os.path.join(root_dir, 'raw')
        os.makedirs(raw_dir, exist_ok=True)
        print(f"\n  Created destination: {raw_dir}")

    # Collect all CSV files (subdirectory files first, then root-level files)
    subdir_files = []
    root_files = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames.sort()
        for filename in sorted(filenames):
            if filename.lower().endswith('.csv'):
                full_path = os.path.join(dirpath, filename)
                if os.path.normpath(dirpath) == os.path.normpath(root_dir):
                    root_files.append(full_path)
                else:
                    subdir_files.append(full_path)

    all_files = subdir_files + root_files
    total = len(all_files)

    if total == 0:
        print("No CSV files found in directory tree.")
        return

    print(f"\nFound {total} CSV file(s) total")
    print(f"  In subdirectories : {len(subdir_files)}")
    print(f"  At root level     : {len(root_files)}")

    # Detect meter name for every file (non-interactively)
    print(f"\nDetecting meter names from file headers...")
    meter_for_file = {}
    for source in all_files:
        name = detect_meter_name_from_file(source, interactive=False)
        meter_for_file[source] = name if name else "unknown"

    meter_counts = Counter(meter_for_file.values())
    for meter, count in sorted(meter_counts.items()):
        print(f"  {meter}: {count} file(s)")

    meter_widths = {m: max(3, len(str(c))) for m, c in meter_counts.items()}
    meter_counter = {m: 1 for m in meter_counts}

    # Move and rename
    print(f"\nMoving and renaming files...")
    moved = 0
    for source in all_files:
        meter = meter_for_file[source]
        w = meter_widths[meter]
        c = meter_counter[meter]
        new_name = f"{meter}_{c:0{w}d}_mango_export.csv"
        dest = os.path.join(raw_dir, new_name)
        while os.path.exists(dest) and os.path.abspath(dest) != os.path.abspath(source):
            meter_counter[meter] += 1
            c = meter_counter[meter]
            new_name = f"{meter}_{c:0{w}d}_mango_export.csv"
            dest = os.path.join(raw_dir, new_name)
        original_display = os.path.relpath(source, os.path.dirname(raw_dir))
        shutil.move(source, dest)
        print(f"  Moved: {original_display}  ->  {os.path.relpath(dest, os.path.dirname(raw_dir))}")
        moved += 1
        meter_counter[meter] += 1

    # Remove empty subdirectories
    print(f"\nRemoving empty subdirectories...")
    removed_dirs = 0
    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
        norm = os.path.normpath(dirpath)
        if norm in (os.path.normpath(root_dir), os.path.normpath(raw_dir)):
            continue
        try:
            os.rmdir(dirpath)
            removed_dirs += 1
        except OSError:
            remaining = os.listdir(dirpath)
            print(f"  WARNING: Could not remove {os.path.relpath(dirpath, root_dir)}/ "
                  f"({len(remaining)} non-CSV file(s) remain)")

    print("\n" + "=" * 60)
    print("FLATTEN SUMMARY")
    print("=" * 60)
    print(f"  Files moved/renamed : {moved}")
    print(f"  Directories removed : {removed_dirs}")
    print("=" * 60)
    print("\nFlatten complete!")


def _get_field_map_yaml():
    """
    Return the path to standard_field_map.yaml.
    First run: prompts the user for the mappings folder and saves the path to .field_map_path.
    Subsequent runs: reads the saved path from .field_map_path.
    Returns None if the user skips setup or the file cannot be found.
    """
    config_path = os.path.join(dirname, '.field_map_path')

    # Use saved path if available and still valid
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            saved_folder = f.read().strip()
        yaml_path = os.path.join(saved_folder, 'standard_field_map.yaml')
        if os.path.exists(yaml_path):
            return yaml_path
        print(f"  WARNING: Saved field map path no longer valid: {saved_folder}")
        print("  Re-running field map setup...")

    # First-time setup — prompt the user
    print("\n" + "=" * 60)
    print("FIELD MAP SETUP")
    print("=" * 60)
    print("Enter the path to the meter_onboard_tool mappings folder.")
    print("This will be saved locally (.field_map_path is gitignored).")
    print("Leave blank to skip field mapping for this run.")
    print("=" * 60)

    while True:
        folder = input("\nEnter mappings folder path: ").strip().strip('"').strip("'")
        if not folder:
            print("  Skipping field map setup.")
            return None
        yaml_path = os.path.join(folder, 'standard_field_map.yaml')
        if os.path.exists(yaml_path):
            with open(config_path, 'w') as f:
                f.write(folder)
            print(f"  Path saved to: {config_path}")
            print("=" * 60 + "\n")
            return yaml_path
        print(f"  standard_field_map.yaml not found in: {folder}")
        print("  Please check the path and try again, or leave blank to skip.")


def load_field_map_yaml(yaml_path):
    """Load and parse standard_field_map.yaml. Returns the raw dict."""
    if not _YAML_AVAILABLE:
        raise RuntimeError("PyYAML is required. Install with: pip install pyyaml")
    with open(yaml_path, 'r') as f:
        return _yaml.safe_load(f)


def build_field_lookup(field_map, meter_type):
    """
    Build lookup tables from a loaded field map for a given meter type.
    Returns:
        raw_to_standard : dict {raw_name_lower -> standard_field_name}
        standard_to_unit: dict {standard_field_name -> standard_unit}
        ignore_names    : set  {raw_name_lower}  (fields to drop)
    """
    type_data = field_map.get(meter_type, {})
    raw_to_standard, standard_to_unit, ignore_names = {}, {}, set()
    for field_name, info in type_data.items():
        if field_name == 'IGNORE':
            for raw in info.get('names', []):
                ignore_names.add(str(raw).lower())
        else:
            standard_to_unit[field_name] = info.get('standard_unit', '')
            for raw in info.get('names', []):
                raw_to_standard[str(raw).lower()] = field_name
    return raw_to_standard, standard_to_unit, ignore_names


def apply_field_mapping(df, meter_type, field_map):
    """
    Rename measurement columns from raw names to standard field names.
    - Drops columns in the IGNORE list
    - Warns about columns with no match (keeps them as-is)
    Called after rename_point_columns(), before restructure_to_paired_columns().
    Returns (processed_df, unmatched_col_names)
    """
    raw_to_standard, _, ignore_names = build_field_lookup(field_map, meter_type)
    measurement_cols = [col for col in df.columns if col != 'timestamp']
    rename_map, drop_cols, unmatched = {}, [], []
    for col in measurement_cols:
        col_lower = col.lower()
        if col_lower in ignore_names:
            drop_cols.append(col)
        elif col_lower in raw_to_standard:
            rename_map[col] = raw_to_standard[col_lower]
        else:
            unmatched.append(col)
    if drop_cols:
        print(f"  Dropping {len(drop_cols)} IGNORE field(s): {', '.join(drop_cols)}")
        df = df.drop(columns=drop_cols)
    if rename_map:
        print(f"  Mapped {len(rename_map)} field(s) to standard names")
        df = df.rename(columns=rename_map)
    if unmatched:
        print(f"  WARNING: {len(unmatched)} unmatched field(s) kept as-is: {', '.join(unmatched)}")
    return df, unmatched


def _build_unit_df_from_field_map(field_map, meter_type):
    """Build a pointName->Units DataFrame from the YAML for use in pivot_flat_file."""
    _, standard_to_unit, _ = build_field_lookup(field_map, meter_type)
    return pd.DataFrame(
        [{'pointName': k, 'Units': v} for k, v in standard_to_unit.items()]
    )


def batch_combine_from_mapping(directory, mapping_path, output_dir=None):
    """
    Batch-combine workflow: detect meter names from a flat directory of raw CSV files,
    match against a mapping CSV, and for each row combine + trim + reformat the data.

    Auto-detects raw/ subdir if the user passes the job root.
    Writes output to a sibling combined/ directory by default.
    """
    print("\n" + "=" * 60)
    print("BATCH COMBINE FROM MAPPING CSV")
    print("=" * 60)

    # Auto-detect raw/ subdir if user passed the project root
    raw_subdir = os.path.join(os.path.abspath(directory), 'raw')
    if os.path.isdir(raw_subdir):
        directory = raw_subdir
        print(f"\n  Raw files dir   : {directory} (raw/ auto-detected)")
    else:
        directory = os.path.abspath(directory)
        print(f"\n  Raw files dir   : {directory}")

    with contextlib.redirect_stdout(io.StringIO()):
        mapping_rows = read_mapping_csv(mapping_path)
    print(f"\n  Mapping CSV     : {len(mapping_rows)} row(s) loaded")

    with contextlib.redirect_stdout(io.StringIO()):
        csv_files = get_files_from_directory(directory, csv_only=True)
    if not csv_files:
        raise ValueError(f"No CSV files found in directory: {directory}")
    print(f"  Raw files       : {len(csv_files)} CSV file(s) found")

    with contextlib.redirect_stdout(io.StringIO()):
        meter_groups = group_files_by_meter(csv_files)
    print(f"  Meter groups    : {len(meter_groups)} detected")
    for name, files in meter_groups.items():
        print(f"    '{name}': {len(files)} file(s)")

    # Combine files once per unique meter referenced in the mapping
    unique_meters = list(dict.fromkeys(row['meter_name'] for row in mapping_rows))
    combined_by_meter = {}
    combine_errors = {}

    print(f"  Combining       : ", end='', flush=True)
    for meter_name in unique_meters:
        files = meter_groups.get(meter_name)
        if not files:
            combine_errors[meter_name] = "no matching files detected in directory"
            continue
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                dfs = [pd.read_csv(f) for f in files if validate_mango_csv(f)]
            if not dfs:
                combine_errors[meter_name] = "no files passed validation"
                continue
            combined = pd.concat(dfs, ignore_index=True)
            combined = combined.drop_duplicates(subset=['timestamp'], keep='first')
            combined = combined.sort_values('timestamp').reset_index(drop=True)
            combined_by_meter[meter_name] = combined
        except Exception as e:
            combine_errors[meter_name] = str(e)
    print(f"{len(combined_by_meter)}/{len(unique_meters)} meter(s) ready")
    for meter_name, err in combine_errors.items():
        print(f"    WARNING '{meter_name}': {err}")

    # Auto-load field map YAML once before per-row processing
    yaml_path = _get_field_map_yaml()
    field_map = None
    if yaml_path:
        try:
            field_map = load_field_map_yaml(yaml_path)
            print(f"  Field map       : {os.path.basename(yaml_path)}")
        except Exception as e:
            print(f"  WARNING: Could not load field map: {e}")
    else:
        print("  Field map       : not found (skipping field renaming)")

    # Determine output directory (sibling combined/ if not specified)
    if output_dir:
        out_dir = output_dir
    else:
        parent_dir = os.path.dirname(os.path.abspath(directory))
        out_dir = os.path.join(parent_dir, 'combined')
    os.makedirs(out_dir, exist_ok=True)
    print(f"  Output dir      : {out_dir}")

    pad = len(str(len(mapping_rows)))
    print(f"\n  Processing {len(mapping_rows)} mapping row(s):")

    created = 0
    skipped = 0

    for idx, row in enumerate(mapping_rows, 1):
        meter_name = row['meter_name']
        building = row['building_id']
        external_id = row.get('external_id', '').strip()
        technical_id = row.get('technical_id', '').strip()
        meter_type = row.get('type', '').strip().upper() or None  # 'EM', 'WM', 'GM', or None
        bug_number = row.get('bug_number', '').strip()
        start_date = row['start_date'].strip()
        end_date = row['end_date'].strip()

        ext_display = f"  [{external_id}]" if external_id else ""
        label = (
            f"    [{idx:>{pad}}/{len(mapping_rows)}] "
            f"{meter_name}  {start_date} -> {end_date}{ext_display}"
        )

        try:
            if meter_name not in combined_by_meter:
                raise ValueError(combine_errors.get(meter_name, "no combined data available"))

            with contextlib.redirect_stdout(io.StringIO()):
                df = trim_to_date_window(
                    combined_by_meter[meter_name].copy(), start_date, end_date
                )

            if df.empty:
                raise ValueError(f"no data in date window {start_date} to {end_date}")

            with contextlib.redirect_stdout(io.StringIO()):
                df = remove_rendered_columns(df)
                df = rename_point_columns(df)
                if field_map and meter_type:
                    df, _ = apply_field_mapping(df, meter_type, field_map)
                df = format_timestamps(df)
                df = restructure_to_paired_columns(df)
                df = add_metadata_columns(df, building, device=meter_name, external_id=external_id)

            ext_segment = f"_{external_id}" if external_id else ""
            output_filename = (
                f"{building}_{meter_name}{ext_segment}_{start_date}_{end_date}_mango.csv"
            )
            output_path = os.path.join(out_dir, output_filename)
            df.to_csv(output_path, index=False, quoting=csv.QUOTE_NONNUMERIC, float_format='%.10g')

            meta_path = output_path.replace('.csv', '.meta')
            with open(meta_path, 'w') as mf:
                json.dump({'type': meter_type or '', 'bug_number': bug_number, 'technical_id': technical_id}, mf)

            print(f"{label}  ->  SUCCESS  ({len(df)} rows)")
            created += 1

        except Exception as e:
            print(f"{label}  ->  FAIL: {e}")
            skipped += 1

    print("\n" + "=" * 60)
    print("BATCH COMBINE SUMMARY")
    print("=" * 60)
    print(f"  Mapping rows processed : {len(mapping_rows)}")
    print(f"  Output files created   : {created}")
    print(f"  Rows skipped           : {skipped}")
    print("=" * 60)
    print("\nBatch combine complete!")


# ============================================================
# SECTION 3 — BACKFILL FORMATTING FUNCTIONS
# ============================================================

def read_data_file(filepath):
    """
    Read a CSV or XLSX file into a pandas DataFrame.
    Strips commas from numeric columns that were read as strings.
    """
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.csv':
        df = pd.read_csv(filepath)
    elif ext in ['.xlsx', '.xls']:
        df = pd.read_excel(filepath, engine='openpyxl')
    else:
        raise ValueError(f"Unsupported file format: {ext}")

    for col in df.columns:
        if df[col].dtype == 'object':
            try:
                cleaned = df[col].str.replace(',', '', regex=False)
                try:
                    df[col] = pd.to_numeric(cleaned)
                except (ValueError, TypeError):
                    pass
            except (AttributeError, TypeError):
                pass

    return df


def detect_paired_format(df):
    """Return True if the DataFrame uses paired pointName/value columns."""
    return 'pointName1' in df.columns and 'value1' in df.columns


def convert_paired_to_flat(df):
    """
    Convert paired column format to flat format.
    Input:  timestamp, building, device, externalID, pointName1, value1, ...
    Output: building, device, timestamp, pointName, value[, externalID]
    """
    print("  Converting paired format to flat format...")
    has_external_id = 'externalID' in df.columns

    point_pairs = []
    i = 1
    while f'pointName{i}' in df.columns and f'value{i}' in df.columns:
        point_pairs.append(i)
        i += 1

    if not point_pairs:
        raise ValueError("No valid pointName/value pairs found")

    print(f"  Found {len(point_pairs)} pointName/value pair(s)")

    flat_rows = []
    for _, row in df.iterrows():
        for i in point_pairs:
            if pd.isna(row[f'pointName{i}']) or pd.isna(row[f'value{i}']):
                continue
            flat_row = {
                'building': row['building'],
                'device': row['device'],
                'timestamp': row['timestamp'],
                'pointName': row[f'pointName{i}'],
                'value': row[f'value{i}'],
            }
            if has_external_id:
                flat_row['externalID'] = row['externalID']
            flat_rows.append(flat_row)

    flat_df = pd.DataFrame(flat_rows)
    print(f"  Converted to flat format: {len(flat_df)} rows")
    return flat_df


def pivot_flat_file(input_path, outputdirname, meter_type=None, field_map=None, bug_number='', technical_id=''):
    """
    Pivot a flat or paired CSV/XLSX file and write per-device output folders.
    Each output folder contains:
      - {building}_{device}.csv       (pivoted data)
      - {building}_{device}_units.csv (unit mappings)
      - run_command.txt               (blaze populate command)
      - backfill_log.log              (processing log)

    Args:
        input_path:    Absolute path to the input file
        outputdirname: Directory where per-device output folders are written
        meter_type:    'EM', 'WM', or 'GM' — used with field_map for unit lookup
        field_map:     Loaded standard_field_map.yaml dict (optional)
    """
    try:
        df = read_data_file(input_path)

        if detect_paired_format(df):
            df = convert_paired_to_flat(df)

        required_columns = ['building', 'device', 'timestamp', 'pointName', 'value']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")
        if df.empty:
            raise ValueError("Input file is empty or contains no valid data")

        has_external_id = 'externalID' in df.columns
        groupby_cols = (
            ['building', 'device', 'externalID'] if has_external_id
            else ['building', 'device']
        )

        for group_keys, group in df.groupby(groupby_cols):
            if has_external_id:
                building, device, external_id = group_keys
            else:
                building, device = group_keys
                external_id = None

            df_single = group
            table = pd.pivot_table(
                data=df_single, values='value',
                index=['timestamp'], columns='pointName'
            )
            table = table.rename_axis(None, axis=1).reset_index()
            table = format_timestamps(table)

            start_date = table.timestamp.min().date().strftime('%Y-%m-%d')
            end_date = table.timestamp.max().date().strftime('%Y-%m-%d')

            folder_name = f'{building}_{device}_{start_date}_{end_date}'
            newpath = os.path.join(outputdirname, folder_name)
            os.makedirs(newpath, exist_ok=True)
            file_label = technical_id if technical_id else device

            # Set up per-device logger
            logger_name = (
                f'{building}_{device}_{external_id}_{start_date}_{end_date}'
                if external_id is not None
                else f'{building}_{device}_{start_date}_{end_date}'
            )
            logger = logging.getLogger(logger_name)
            logger.setLevel(logging.DEBUG)
            logger.handlers.clear()

            log_file_path = os.path.join(newpath, 'backfill_log.log')
            file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(
                logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            )
            logger.addHandler(file_handler)

            logger.info('Input File Path: ' + input_path)
            logger.info('Action Performed: Pivoting and Timestamp Formatting')

            # Write pivoted data CSV
            output_path = os.path.join(newpath, f'{building}_{file_label}.csv')
            table.to_csv(output_path, index=False, quoting=csv.QUOTE_NONNUMERIC, float_format='%.10g')
            logger.info('Output File Path: ' + output_path)

            # Write unit CSV
            a_df = df_single.drop_duplicates(['device', 'pointName'])[['device', 'pointName']].copy()
            if technical_id:
                a_df['device'] = technical_id
            if field_map and meter_type:
                effective_unit_df = _build_unit_df_from_field_map(field_map, meter_type)
            else:
                print('  WARNING: No field map / meter type — units CSV will have no Units column.')
                effective_unit_df = pd.DataFrame(columns=['pointName', 'Units'])
            unit_table = a_df.merge(effective_unit_df, how='left', on='pointName')
            unit_table = unit_table.rename(
                {'device': 'Device Id', 'pointName': 'Field Name'}, axis='columns'
            )
            output_unit_path = os.path.join(newpath, f'{building}_{file_label}_units.csv')
            unit_table.to_csv(output_unit_path, index=False, quoting=csv.QUOTE_NONNUMERIC)
            logger.info('Output Unit File Path: ' + output_unit_path)

            device_num_id_value = str(external_id) if external_id is not None else ''
            command_template = (
                f'admin_session --reason="b/{bug_number}" -- \\\n'
                f'blaze run \\\n'
                f'java/com/google/corp/bizapps/rews/datalake/tools/backfill:backfill_tool -- \\\n'
                f'--mode="populate" --data_file="{output_path}" \\\n'
                f'--unit_file="{output_unit_path}" --device_num_id={device_num_id_value} \\\n'
                f'--data_field_name="points" --present_value_field_name="present_value" \\\n'
                f'--environment=prod'
            )

            run_command_path = os.path.join(newpath, 'run_command.txt')
            with open(run_command_path, 'w') as cmd_file:
                cmd_file.write(command_template)
            logger.info('Run Command File Path: ' + run_command_path)

            unmatched_units = unit_table[unit_table['Units'].isnull()]
            if not unmatched_units.empty:
                field_list = ', '.join(unmatched_units['Field Name'])
                logger.warning('Unrecognized field(s): ' + field_list)
                print(f'  WARNING: Unrecognized field(s): {field_list}. Review and add units if valid.')

            file_handler.close()
            logger.removeHandler(file_handler)

    except KeyError as e:
        raise ValueError(f"Column error during pivot: {str(e)}")
    except pd.errors.ParserError as e:
        raise ValueError(f"Error parsing file: {str(e)}")
    except Exception as e:
        raise Exception(f"Unexpected error processing file: {str(e)}")


def extract_publish_lines(stdout, stderr):
    """Extract 'INFO: Finished publishing messages' lines from blaze output."""
    combined = (stdout or "") + "\n" + (stderr or "")
    return [
        line.strip() for line in combined.splitlines()
        if "INFO: Finished publishing messages" in line
    ]


def write_backfill_run_summary(folder, command, returncode, stdout, stderr):
    """
    Write backfill_run_summary.txt to the given folder.
    SUCCESS:   exit 0 + publish lines found → writes publish lines.
    UNCERTAIN: exit 0 but no publish lines  → writes full output.
    FAILED:    non-zero exit                → writes full output.
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
        f.write(f"Status   : {status}\n")
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


# ============================================================
# SECTION 4 — JOB SETUP + PIPELINE ORCHESTRATION
# ============================================================

def setup_job(parent_dir, job_name):
    """
    Create a new job folder with the standard structure:
      {parent_dir}/{job_name}/raw/
      {parent_dir}/{job_name}/combined/
      {parent_dir}/{job_name}/output/
      {parent_dir}/{job_name}/{job_name}_mapping_template.csv
    """
    print("\n" + "=" * 60)
    print("SETUP NEW JOB")
    print("=" * 60)

    job_dir = os.path.join(os.path.abspath(parent_dir), job_name)

    for subdir in ['raw', 'combined', 'output']:
        d = os.path.join(job_dir, subdir)
        os.makedirs(d, exist_ok=True)
        print(f"  Created: {os.path.join(job_name, subdir)}/")

    mapping_template_path = os.path.join(job_dir, "meter_mapping.csv")
    with open(mapping_template_path, 'w', newline='') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(['building_id', 'meter_name', 'technical_id', 'external_id', 'type', 'bug_number', 'start_date', 'end_date'])
        writer.writerow([
            'US-MFA-BUILDING', 'power-meter-MAIN_METER', 'EM-1', '1743694964149',
            'EM', '123456789', '2025-01-01', '2025-12-31',
        ])
    print(f"  Created: {os.path.join(job_name, os.path.basename(mapping_template_path))}")

    print("\n" + "=" * 60)
    print(f"Job '{job_name}' created at: {job_dir}")
    print("=" * 60)
    print("\nNext steps:")
    print(f"  1. Drop raw Mango CSV exports into: {os.path.join(job_dir, 'raw')}/")
    print(f"  2. Fill in: {mapping_template_path}")
    print("  3. Run: 1 (Flatten) → 2 (Batch Combine) → 3 (Process) → 4 (Run Backfill)")
    print("=" * 60)
    return job_dir


def _resolve_subdir(path, subdir_name):
    """
    If path contains a subdir named subdir_name, return that subdir's path.
    Otherwise return path as-is.
    Used so modes accept either the job root or the specific subdirectory.
    """
    candidate = os.path.join(path, subdir_name)
    if os.path.isdir(candidate):
        return candidate
    return path


def process_combined_dir(combined_dir, output_dir):
    """
    Process all CSV/XLSX files in combined_dir by pivoting them into backfill output folders.
    Writes per-device folders to output_dir.
    """
    print("\n" + "=" * 60)
    print("PROCESS COMBINED FILES")
    print("=" * 60)

    combined_dir = os.path.abspath(combined_dir)
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    files = get_files_from_directory(combined_dir)
    if not files:
        print("No files found to process.")
        return

    # Load field map once for the whole batch
    yaml_path = _get_field_map_yaml()
    field_map = None
    if yaml_path:
        try:
            field_map = load_field_map_yaml(yaml_path)
        except Exception as e:
            print(f"  WARNING: Could not load field map: {e}")

    print(f"\nInput  : {combined_dir}")
    print(f"Output : {output_dir}")
    print(f"\nProcessing {len(files)} file(s)...\n")

    successful = 0
    failed = 0
    failed_files = []

    for idx, filepath in enumerate(files, 1):
        print(f"[{idx}/{len(files)}] {os.path.basename(filepath)}")

        # Read .meta sidecar if present
        meta = {}
        if filepath.endswith('.csv'):
            meta_path = filepath[:-4] + '.meta'
            if os.path.exists(meta_path):
                try:
                    with open(meta_path) as mf:
                        meta = json.load(mf)
                except Exception:
                    pass

        meter_type = meta.get('type') or None
        bug_number = meta.get('bug_number') or ''
        technical_id = meta.get('technical_id') or ''
        effective_field_map = field_map if (field_map and meter_type) else None

        try:
            pivot_flat_file(filepath, output_dir, meter_type=meter_type,
                            field_map=effective_field_map, bug_number=bug_number, technical_id=technical_id)
            successful += 1
            print(f"  Done")
        except Exception as e:
            failed += 1
            failed_files.append((filepath, str(e)))
            print(f"  Failed: {e}")
        if idx < len(files):
            print()

    print("\n" + "=" * 60)
    print("PROCESS SUMMARY")
    print("=" * 60)
    print(f"  Total     : {len(files)}")
    print(f"  Successful: {successful}")
    print(f"  Failed    : {failed}")
    if failed_files:
        print("\n  Failed files:")
        for fp, err in failed_files:
            print(f"    - {os.path.basename(fp)}: {err}")
    print(f"\n  Output folders written to: {output_dir}/")
    print("=" * 60)


def run_output_dir(output_dir, blaze_cwd):
    """
    Find all subfolders in output_dir that contain run_command.txt and run blaze on each.
    Writes backfill_run_summary.txt per folder and prints a batch summary at the end.
    """
    folders_to_run = []
    for entry in sorted(os.listdir(output_dir)):
        entry_path = os.path.join(output_dir, entry)
        if os.path.isdir(entry_path) and os.path.exists(
            os.path.join(entry_path, 'run_command.txt')
        ):
            folders_to_run.append(entry_path)

    if not folders_to_run:
        print(f"No subfolders with run_command.txt found in: {output_dir}")
        return

    print(f"\nFound {len(folders_to_run)} folder(s) to run:")
    for f in folders_to_run:
        print(f"  - {os.path.basename(f)}")

    run_results = []

    for idx, folder in enumerate(folders_to_run, 1):
        folder_name = os.path.basename(folder)
        run_cmd_path = os.path.join(folder, 'run_command.txt')
        with open(run_cmd_path, 'r', encoding='utf-8') as f:
            contents = f.read()

        cmd_str = contents.strip()
        if not cmd_str:
            print(f"\n[{idx}/{len(folders_to_run)}] SKIPPED '{folder_name}': run_command.txt is empty")
            run_results.append((folder_name, 'skipped', None))
            continue
        mode_label = "BACKFILL"

        print(f"\n{'=' * 60}")
        print(f"[{idx}/{len(folders_to_run)}] Running {mode_label}: {folder_name}")
        print(f"{'=' * 60}\n")

        process = subprocess.Popen(
            cmd_str, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=blaze_cwd, bufsize=1
        )
        output_lines = []
        if process.stdout:
            for line in process.stdout:
                print(line, end='', flush=True)
                output_lines.append(line)
        process.wait()
        returncode = process.returncode
        stdout = ''.join(output_lines)
        stderr = ''

        publish_lines = extract_publish_lines(stdout, stderr)
        if returncode == 0 and publish_lines:
            print(f"  Success: {publish_lines[0]}")
            run_results.append((folder_name, 'success', returncode))
        elif returncode == 0:
            print("  Command exited 0 but no 'Finished publishing' line found. Check summary.")
            run_results.append((folder_name, 'uncertain', returncode))
        else:
            print(f"  Failed (exit code {returncode}). Check summary.")
            run_results.append((folder_name, 'failed', returncode))

        write_backfill_run_summary(folder, cmd_str, returncode, stdout, stderr)

    print(f"\n{'=' * 60}")
    print("Batch Backfill Summary")
    print(f"{'=' * 60}")
    icons = {'success': '+', 'uncertain': '!', 'skipped': '~', 'failed': 'x'}
    for folder_name, status, _ in run_results:
        icon = icons.get(status, '?')
        print(f"  [{icon}] {folder_name}: {status.upper()}")
    print('=' * 60)


# ============================================================
# SECTION 5 — CLI ARGUMENT PARSING + MAIN
# ============================================================

def parse_arguments():
    """Parse command-line arguments. Returns None to trigger interactive mode."""
    parser = ArgumentParser(
        description='Carson Backfill Tool — job-oriented Mango reformatting and backfill processing',
        epilog='If no arguments are provided, interactive mode will be used.'
    )

    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        '--setup', action='store_true',
        help='Create a new job folder structure (requires --job)'
    )
    input_group.add_argument(
        '-fl', '--flatten', type=str, metavar='DIR',
        help='Job root or raw/ dir: flatten and rename CSV files in raw/'
    )
    input_group.add_argument(
        '-bc', '--batch-combine', type=str, dest='batch_combine', metavar='DIR',
        help='Job root or raw/ dir: batch-combine using mapping CSV (auto-detected or --mapping)'
    )
    input_group.add_argument(
        '-p', '--process', type=str, metavar='DIR',
        help='Job root or combined/ dir: pivot files and write backfill output folders'
    )
    input_group.add_argument(
        '-r', '--run', type=str, metavar='DIR',
        help='Job root or output/ dir: execute blaze on all backfill output folders'
    )

    parser.add_argument('--job', type=str, help='Job name (used with --setup)')
    parser.add_argument('--mapping', type=str, help='Path to mapping CSV (optional, auto-detected otherwise)')
    parser.add_argument('-o', '--output', type=str, default=None, help='Override output directory')

    args = parser.parse_args()

    if not any([args.setup, args.flatten, args.batch_combine, args.process, args.run]):
        return None

    return args


if __name__ == "__main__":
    print("=" * 60)
    print("Carson Backfill Tool")
    print("Job-oriented Mango reformatting and backfill processing")
    print("=" * 60)
    print()

    args = parse_arguments()

    while True:
        try:
            # ---- CLI modes ----
            if args and args.setup:
                if not args.job:
                    print("ERROR: --setup requires --job <name>")
                    sys.exit(1)
                parent_dir = args.output or os.getcwd()
                setup_job(parent_dir, args.job)
                sys.exit(0)

            elif args and args.flatten:
                flatten_and_rename_directory(args.flatten)
                sys.exit(0)

            elif args and args.batch_combine:
                mapping_path = args.mapping
                if not mapping_path:
                    mapping_path = _find_mapping_csv(args.batch_combine)
                    if mapping_path:
                        print(f"Mapping CSV auto-detected: {os.path.basename(mapping_path)}")
                    else:
                        print("ERROR: No mapping CSV found. Provide --mapping or place a single CSV in the job root.")
                        sys.exit(1)
                out_dir = args.output or None
                batch_combine_from_mapping(args.batch_combine, mapping_path, output_dir=out_dir)
                sys.exit(0)

            elif args and args.process:
                path = os.path.abspath(args.process)
                combined_dir = _resolve_subdir(path, 'combined')
                if args.output:
                    output_dir = args.output
                else:
                    job_root = path if os.path.isdir(os.path.join(path, 'combined')) else os.path.dirname(combined_dir)
                    output_dir = os.path.join(job_root, 'output')
                process_combined_dir(combined_dir, output_dir)
                sys.exit(0)

            elif args and args.run:
                path = os.path.abspath(args.run)
                output_dir = _resolve_subdir(path, 'output')
                print(f"\n{'=' * 60}")
                print("Setting up environment...")
                print(f"{'=' * 60}\n")
                blaze_cwd = run_prerequisites()
                if blaze_cwd is None:
                    print("\nEnvironment setup failed. No commands were run.")
                    sys.exit(1)
                run_output_dir(output_dir, blaze_cwd)
                sys.exit(0)

            else:
                # ---- Interactive mode ----
                print("Interactive Mode")
                print("(Use --help to see command-line options)")
                print("(Type 'quit' at any prompt to exit, 'reset' to start over)")
                print()
                print("Choose mode:")
                print()
                print("  -- SETUP --")
                print("  S. Setup new job  (creates raw/ combined/ output/ + mapping template)")
                print()
                print("  -- JOB WORKFLOW --")
                print("  1. Flatten raw files           (rename files in raw/)")
                print("  2. Batch combine from mapping  (raw/ + mapping.csv -> combined/)")
                print("  3. Process combined files      (combined/ -> output/ backfill folders)")
                print("  4. Run backfill                (execute all output/ folders via blaze)")
                print()

                valid_choice = False
                while not valid_choice:
                    choice = input("Enter choice (S, 1-4): ").strip().lower()
                    check_special_input(choice)

                    if choice == 's':
                        parent_dir = input(
                            "\nEnter parent directory for the new job "
                            "(leave blank for current directory): "
                        ).strip().strip('"').strip("'")
                        if parent_dir:
                            check_special_input(parent_dir)
                        else:
                            parent_dir = os.getcwd()
                        job_name = ''
                        while not job_name:
                            job_name = input("Enter job name (e.g. batch10): ").strip()
                            check_special_input(job_name)
                            if not job_name:
                                print("Job name is required.")
                        setup_job(parent_dir, job_name)
                        sys.exit(0)

                    elif choice == '1':
                        dir_path = input(
                            "\nEnter job root or raw/ directory path: "
                        ).strip().strip('"').strip("'")
                        check_special_input(dir_path)
                        if not os.path.isdir(dir_path):
                            print(f"ERROR: Not a valid directory: {dir_path}\n")
                            continue
                        flatten_and_rename_directory(dir_path)
                        sys.exit(0)

                    elif choice == '2':
                        dir_path = input(
                            "\nEnter job root directory path: "
                        ).strip().strip('"').strip("'")
                        check_special_input(dir_path)
                        if not os.path.isdir(dir_path):
                            print(f"ERROR: Not a valid directory: {dir_path}\n")
                            continue
                        mapping_path = _find_mapping_csv(dir_path)
                        if mapping_path:
                            print(f"  Mapping CSV auto-detected: {os.path.basename(mapping_path)}")
                        else:
                            mapping_path = input(
                                "  No mapping CSV auto-detected. Enter path to mapping CSV: "
                            ).strip().strip('"').strip("'")
                            check_special_input(mapping_path)
                            while not mapping_path or not os.path.isfile(mapping_path):
                                print("  A valid mapping CSV path is required.")
                                mapping_path = input(
                                    "  Enter path to mapping CSV: "
                                ).strip().strip('"').strip("'")
                                check_special_input(mapping_path)
                        batch_combine_from_mapping(dir_path, mapping_path)
                        sys.exit(0)

                    elif choice == '3':
                        dir_path = input(
                            "\nEnter job root or combined/ directory path: "
                        ).strip().strip('"').strip("'")
                        check_special_input(dir_path)
                        if not os.path.isdir(dir_path):
                            print(f"ERROR: Not a valid directory: {dir_path}\n")
                            continue
                        dir_path = os.path.abspath(dir_path)
                        combined_dir = _resolve_subdir(dir_path, 'combined')
                        job_root = (
                            dir_path
                            if os.path.isdir(os.path.join(dir_path, 'combined'))
                            else os.path.dirname(combined_dir)
                        )
                        output_dir = os.path.join(job_root, 'output')
                        process_combined_dir(combined_dir, output_dir)
                        sys.exit(0)

                    elif choice == '4':
                        dir_path = input(
                            "\nEnter job root or output/ directory path: "
                        ).strip().strip('"').strip("'")
                        check_special_input(dir_path)
                        if not os.path.isdir(dir_path):
                            print(f"ERROR: Not a valid directory: {dir_path}\n")
                            continue
                        dir_path = os.path.abspath(dir_path)
                        output_dir = _resolve_subdir(dir_path, 'output')
                        print(f"\n{'=' * 60}")
                        print("Setting up environment...")
                        print(f"{'=' * 60}\n")
                        blaze_cwd = run_prerequisites()
                        if blaze_cwd is None:
                            print("\nEnvironment setup failed. No commands were run.")
                            sys.exit(1)
                        run_output_dir(output_dir, blaze_cwd)
                        sys.exit(0)

                    else:
                        print("Invalid choice. Please enter S, 1, 2, 3, or 4.\n")

            break

        except ResetException:
            continue
        except KeyboardInterrupt:
            print("\n\nOperation cancelled by user.")
            sys.exit(0)
        except Exception as e:
            print(f"\nFatal error: {str(e)}")
            sys.exit(1)
