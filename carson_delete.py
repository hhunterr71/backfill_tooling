import subprocess
import re
import os
import sys
import csv
import pandas as pd
import pytz
import datetime
from argparse import ArgumentParser

dirname = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------
# Custom exception for reset functionality
# ------------------------------------
class ResetException(Exception):
    """Exception raised when user wants to reset to the beginning"""
    pass

def check_special_input(user_input):
    """
    Check if user input is a special command (quit or reset).
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
    """
    if not os.path.exists(input_path):
        print(f"ERROR: File does not exist: {input_path}")
        return False
    if os.path.isdir(input_path):
        print("ERROR: Path is a directory. Please provide a single CSV or XLSX file.")
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

# ------------------------------------
# Batch file reading
# ------------------------------------
def read_batch_file(filepath):
    """
    Read a CSV or XLSX batch file containing one meter per row.
    Required columns: building_id, meter_name, external_id, start_date, end_date
    Returns:
        List of dicts, one per row
    Raises:
        ValueError: If required columns are missing
    """
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.csv':
        df = pd.read_csv(filepath, dtype=str)
    elif ext in ['.xlsx', '.xls']:
        df = pd.read_excel(filepath, dtype=str, engine='openpyxl')
    else:
        raise ValueError(f"Unsupported file format: {ext}")

    required = ['building_id', 'meter_name', 'external_id', 'start_date', 'end_date']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    records = []
    for _, row in df.iterrows():
        records.append({
            'building_id': str(row['building_id']).strip(),
            'meter_name': str(row['meter_name']).strip(),
            'external_id': str(row['external_id']).strip(),
            'start_date': str(row['start_date']).strip(),
            'end_date': str(row['end_date']).strip(),
        })
    return records

# ------------------------------------
# Timestamp formatting
# ------------------------------------
def format_delete_timestamp(date_str):
    """
    Parse a date or datetime string and return a timezone-aware timestamp string
    in America/Los_Angeles time, formatted as: YYYY-MM-DD HH:MM:SS±HH:MM

    Accepts:
        - "YYYY-MM-DD"
        - "YYYY-MM-DD HH:MM:SS"
        - "YYYY-MM-DD HH:MM"
    Default time is 00:00:00 if not provided.
    """
    la_tz = pytz.timezone('America/Los_Angeles')

    # Try parsing with time first, then date-only
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            naive_dt = datetime.datetime.strptime(date_str.strip(), fmt)
            break
        except ValueError:
            continue
    else:
        raise ValueError(
            f"Cannot parse date '{date_str}'. "
            "Expected format: YYYY-MM-DD or YYYY-MM-DD HH:MM:SS"
        )

    aware_dt = la_tz.localize(naive_dt, is_dst=False)

    # strftime('%z') gives e.g. '-0700'; insert colon to get '-07:00'
    raw_offset = aware_dt.strftime('%z')  # e.g. '-0700' or '+0530'
    offset_str = raw_offset[:3] + ':' + raw_offset[3:]

    return aware_dt.strftime('%Y-%m-%d %H:%M:%S') + offset_str

# ------------------------------------
# Data file generation
# ------------------------------------
def generate_data_file(building_id, meter_name, start_date, end_date, output_dir):
    """
    Generate the 2-row timestamp CSV required by the blaze delete command.
    File is named {building_id}_{meter_name}.csv

    Returns:
        Absolute path to the generated file
    """
    start_ts = format_delete_timestamp(start_date)
    end_ts = format_delete_timestamp(end_date)

    filename = f"{building_id}_{meter_name}.csv"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(['timestamp'])
        writer.writerow([start_ts])
        writer.writerow([end_ts])

    print(f"  Generated data file: {filepath}")
    return os.path.abspath(filepath)

# ------------------------------------
# Run file (pre-execution reference)
# ------------------------------------
def write_run_file(building_id, meter_name, external_id,
                   start_date, end_date, data_file, command, output_dir):
    """
    Write a reference txt file during Phase 1 (before execution) containing
    all entry details and the command to be run. Named {building_id}_{meter_name}_run_command.txt
    """
    filename = f"{building_id}_{meter_name}_run_command.txt"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write("=" * 60 + "\n")
        f.write(f"Building ID  : {building_id}\n")
        f.write(f"Meter Name   : {meter_name}\n")
        f.write(f"External ID  : {external_id}\n")
        f.write(f"Start        : {format_delete_timestamp(start_date)}\n")
        f.write(f"End          : {format_delete_timestamp(end_date)}\n")
        f.write(f"Data file    : {data_file}\n")
        f.write("-" * 60 + "\n")
        f.write("Run Command:\n")
        f.write(command + "\n")
        f.write("=" * 60 + "\n")

    print(f"  Run file written: {filepath}")

# ------------------------------------
# Prerequisites
# ------------------------------------
def run_prerequisites():
    """
    Run environment setup for the backfill client using the script-safe form:
      bash -c 'cd "$(p4 g4d backfill)" && g4 sync && pwd'

    p4 g4d <client> outputs the client root path (script-safe alternative to the
    g4d shell function). We cd into it so g4 sync finds the .g4config file for
    the right client. pwd captures the root so blaze can be run from there.

    Returns the client root directory string on success, or None on failure.
    """
    print("  Running: cd $(p4 g4d backfill) && g4 sync")
    result = subprocess.run(
        ["bash", "-c", 'cd "$(p4 g4d backfill)" && g4 sync && pwd'],
        capture_output=True, text=True
    )

    # Last line of stdout is pwd (the client root); print everything before it
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

# ------------------------------------
# Output parsing
# ------------------------------------
def extract_bt_delete_lines(stdout, stderr):
    """
    Extract 'INFO: bt delete' lines from blaze output.
    Returns a list of matching lines, or an empty list if none found.
    Searches both stdout and stderr since blaze may write to either.
    """
    combined = (stdout or "") + "\n" + (stderr or "")
    return [line.strip() for line in combined.splitlines()
            if line.strip().startswith("INFO: bt delete")]

# ------------------------------------
# Command building and execution
# ------------------------------------
def build_delete_command(data_file_path, external_id):
    """
    Build the full blaze delete command string.
    """
    return (
        f"blaze run java/com/google/corp/bizapps/rews/datalake/tools/backfill:backfill_tool -- "
        f"--data_file=\"{data_file_path}\" "
        f"--unit_file=none "
        f"--device_num_id={external_id} "
        f"--mode=delete"
    )

def run_delete_command(data_file_path, external_id, cwd=None):
    """
    Run the blaze delete command via subprocess.
    cwd: working directory to run the command in (set by run_prerequisites).
    Returns:
        (returncode, stdout, stderr)
    """
    cmd = [
        "blaze", "run",
        "java/com/google/corp/bizapps/rews/datalake/tools/backfill:backfill_tool",
        "--",
        f"--data_file={data_file_path}",
        "--unit_file=none",
        f"--device_num_id={external_id}",
        "--mode=delete"
    ]
    print("  Running blaze delete command...")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result.returncode, result.stdout, result.stderr

# ------------------------------------
# Summary file writing
# ------------------------------------
def write_summary_file(building_id, meter_name, external_id,
                       start_date, end_date, command,
                       returncode, stdout, stderr, output_dir):
    """
    Write a readable summary txt file for a single delete operation.
    File is named {building_id}_{meter_name}_delete_summary.txt
    """
    filename = f"{building_id}_{meter_name}_delete_summary.txt"
    filepath = os.path.join(output_dir, filename)

    status = "SUCCESS" if returncode == 0 else f"FAILED (exit code {returncode})"
    combined_output = (stdout or "").strip()
    if stderr and stderr.strip():
        combined_output += ("\n\n--- stderr ---\n" + stderr.strip()) if combined_output else stderr.strip()

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write("=" * 60 + "\n")
        f.write(f"Building ID  : {building_id}\n")
        f.write(f"Meter Name   : {meter_name}\n")
        f.write(f"External ID  : {external_id}\n")
        f.write(f"Start Date   : {format_delete_timestamp(start_date)}\n")
        f.write(f"End Date     : {format_delete_timestamp(end_date)}\n")
        f.write(f"Status       : {status}\n")
        f.write("-" * 60 + "\n")
        f.write("Run Command:\n")
        f.write(command + "\n")
        f.write("-" * 60 + "\n")
        f.write("Command Output:\n")
        f.write(combined_output if combined_output else "(no output captured)")
        f.write("\n" + "=" * 60 + "\n")

    print(f"  Summary written: {filepath}")
    return filepath

# ------------------------------------
# Template file generation
# ------------------------------------
def generate_template_file(folder, filename=None):
    """
    Generate a blank batch CSV template with the correct columns and one example row.
    Auto-names the file 'carson_delete_template.csv' if no filename is given.
    Returns the path to the written file.
    """
    if not filename:
        filename = "carson_delete_template.csv"
    elif not filename.lower().endswith('.csv'):
        filename += '.csv'

    if not os.path.exists(folder):
        print(f"Creating directory: {folder}")
        os.makedirs(folder)

    filepath = os.path.join(folder, filename)

    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(['building_id', 'meter_name', 'external_id', 'start_date', 'end_date'])
        writer.writerow([
            'US-MFA-BV100',
            'utility-WM_01_BLDGDCW',
            '1743694964149',
            '2025-06-30 00:00:00',
            '2026-02-05 00:00:00',
        ])

    print(f"  Template written: {filepath}")
    return filepath

# ------------------------------------
# Argument parsing
# ------------------------------------
def parse_arguments():
    """
    Parse command-line arguments.
    Returns parsed args, or None to trigger interactive mode.
    """
    parser = ArgumentParser(
        description='Carson Delete Tool - Run backfill delete for one or more meters',
        epilog='If no arguments are provided, interactive mode will be used.'
    )

    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        '-i', '--input',
        type=str,
        help='Path to CSV or XLSX batch file (columns: building_id, meter_name, external_id, start_date, end_date)'
    )
    input_group.add_argument(
        '--building_id',
        type=str,
        help='Building ID for a single one-off entry (e.g. US-MFA-BV100)'
    )

    parser.add_argument('--meter_name', type=str, help='Meter name (e.g. utility-WM_01_BLDGDCW)')
    parser.add_argument('--external_id', type=str, help='Device external/numeric ID')
    parser.add_argument('--start_date', type=str, help='Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)')
    parser.add_argument('--end_date', type=str, help='End date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)')
    parser.add_argument(
        '-o', '--output',
        type=str,
        default=None,
        help='Output directory for generated files (default: prompted interactively)'
    )

    args = parser.parse_args()

    # Nothing provided — interactive mode
    if args.input is None and args.building_id is None:
        return None

    # One-off mode: require all five fields
    if args.building_id:
        missing = [f for f in ['meter_name', 'external_id', 'start_date', 'end_date']
                   if getattr(args, f) is None]
        if missing:
            parser.error(f"When using --building_id, also provide: {', '.join('--' + m for m in missing)}")

    return args

# ------------------------------------
# Processing a single entry (two phases)
# ------------------------------------
def prepare_entry(building_id, meter_name, external_id, start_date, end_date, output_dir):
    """
    Phase 1: Create a per-entry subfolder, generate the data CSV, build the
    command string, and write the run file.
    Returns a dict with everything needed for execution and review.
    """
    # Use just the date portion (YYYY-MM-DD) for the folder name
    start_slug = start_date.strip().split(' ')[0].split('T')[0]
    end_slug = end_date.strip().split(' ')[0].split('T')[0]
    folder_name = f"{building_id}_{meter_name}_{external_id}_{start_slug}_{end_slug}"
    entry_dir = os.path.join(output_dir, folder_name)

    if not os.path.exists(entry_dir):
        os.makedirs(entry_dir)

    data_file = generate_data_file(building_id, meter_name, start_date, end_date, entry_dir)
    command = build_delete_command(data_file, external_id)
    write_run_file(building_id, meter_name, external_id,
                   start_date, end_date, data_file, command, entry_dir)
    return {
        'building_id': building_id,
        'meter_name': meter_name,
        'external_id': external_id,
        'start_date': start_date,
        'end_date': end_date,
        'data_file': data_file,
        'command': command,
        'output_dir': entry_dir,
    }

def execute_entry(prepared, cwd=None):
    """
    Phase 2: Run the blaze command and write the summary file.
    cwd: working directory passed from run_prerequisites (g4d client root).
    On success: summary contains only the extracted 'bt delete' lines.
    On failure: summary contains full stdout/stderr for debugging.
    Raises RuntimeError if the command exits non-zero.
    """
    returncode, stdout, stderr = run_delete_command(
        prepared['data_file'], prepared['external_id'], cwd=cwd
    )

    if returncode == 0:
        bt_lines = extract_bt_delete_lines(stdout, stderr)
        if bt_lines:
            summary_stdout = "\n".join(bt_lines)
            summary_stderr = ""
        else:
            summary_stdout = "(command succeeded but no 'bt delete' lines found in output)"
            summary_stderr = ""
    else:
        summary_stdout = stdout
        summary_stderr = stderr

    write_summary_file(
        prepared['building_id'], prepared['meter_name'], prepared['external_id'],
        prepared['start_date'], prepared['end_date'],
        prepared['command'], returncode, summary_stdout, summary_stderr,
        prepared['output_dir']
    )
    if returncode != 0:
        raise RuntimeError(f"blaze command exited with code {returncode}")

# ------------------------------------
# Interactive one-off entry collection
# ------------------------------------
def collect_one_off_entry():
    """
    Interactively prompt for all five required fields.
    Returns a dict with the entry.
    """
    print("\nEnter the meter details below.")
    print("(Type 'quit' to exit or 'reset' to start over)\n")

    fields = [
        ('building_id', 'Building ID (e.g. US-MFA-BV100)'),
        ('meter_name', 'Meter Name (e.g. utility-WM_01_BLDGDCW)'),
        ('external_id', 'External / Device Numeric ID'),
        ('start_date', 'Start Date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)'),
        ('end_date', 'End Date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)'),
    ]

    entry = {}
    for key, label in fields:
        while True:
            value = input(f"  {label}: ").strip()
            check_special_input(value)
            if not value:
                print("  Value cannot be empty. Please try again.")
                continue
            # Validate date fields
            if key in ('start_date', 'end_date'):
                try:
                    format_delete_timestamp(value)
                except ValueError as e:
                    print(f"  ERROR: {e}")
                    continue
            entry[key] = value
            break

    return entry

# ------------------------------------
# Combined summary file generation
# ------------------------------------
def _parse_summary_file(filepath):
    """
    Parse an individual *_delete_summary.txt file.
    Returns:
        metadata_lines : list of 'Key : Value' lines (Building ID through Status)
        output_lines   : command output lines with 'INFO: ' prefix stripped
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.read().strip().splitlines()

    metadata_lines = []
    output_lines = []
    in_run_command = False
    in_output = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('===') or stripped.startswith('---'):
            continue
        if stripped == 'Run Command:':
            in_run_command = True
            continue
        if stripped == 'Command Output:':
            in_run_command = False
            in_output = True
            continue
        if in_run_command:
            continue
        if in_output:
            cleaned = stripped[6:] if stripped.startswith('INFO: ') else stripped
            if cleaned:
                output_lines.append(cleaned)
        else:
            if stripped:
                metadata_lines.append(stripped)

    return metadata_lines, output_lines


def combine_summary_files(directory, output_path):
    """
    Recursively find all *_delete_summary.txt files under directory,
    sort them alphabetically, and write a single consolidated report
    to output_path.
    Returns the number of files combined.
    """
    summary_files = sorted([
        os.path.join(root, f)
        for root, _, files in os.walk(directory)
        for f in files
        if f.endswith('_delete_summary.txt')
    ])

    if not summary_files:
        return 0

    with open(output_path, 'w', encoding='utf-8') as out:
        out.write("=" * 60 + "\n")
        out.write("COMBINED DELETE SUMMARY\n")
        out.write(f"Generated : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        out.write(f"Entries   : {len(summary_files)}\n")
        out.write(f"Source    : {directory}\n")
        out.write("=" * 60 + "\n")

        for idx, filepath in enumerate(summary_files, 1):
            metadata_lines, output_lines = _parse_summary_file(filepath)
            out.write(f"\n[{idx}/{len(summary_files)}]\n")
            for line in metadata_lines:
                out.write(line + "\n")
            out.write("Command Output:\n")
            out.write("```\n")
            for line in output_lines:
                out.write(line + "\n")
            out.write("```\n")

    return len(summary_files)

# ------------------------------------
# Main
# ------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("Carson Delete Tool")
    print("Supports: Single entry (one-off) or batch CSV/XLSX file")
    print("=" * 60)
    print()

    args = parse_arguments()

    while True:
        try:
            entries = []
            input_file_path = None

            # ---- Collect entries ----
            if args and args.input:
                # Batch file mode (CLI)
                if not check_input(args.input):
                    print("\nExiting due to invalid input file.")
                    sys.exit(1)
                input_file_path = os.path.abspath(args.input)
                entries = read_batch_file(input_file_path)
                print(f"Loaded {len(entries)} entry/entries from file.\n")

            elif args and args.building_id:
                # One-off mode (CLI)
                entries = [{
                    'building_id': args.building_id,
                    'meter_name': args.meter_name,
                    'external_id': args.external_id,
                    'start_date': args.start_date,
                    'end_date': args.end_date,
                }]

            else:
                # Interactive mode
                print("Interactive Mode")
                print("(Use --help to see command-line options)")
                print("(Type 'quit' at any prompt to exit, 'reset' to start over)")
                print()
                print("Choose input mode:")
                print("  1. Generate batch CSV template")
                print("  2. Process batch CSV/XLSX file")
                print("  3. Enter a single meter manually")
                print()

                valid_choice = False
                while not valid_choice:
                    choice = input("Enter choice (1-3): ").strip()
                    check_special_input(choice)

                    if choice == '1':
                        folder = input("\nEnter folder to save template (leave blank for current directory): ").strip().strip('"').strip("'")
                        check_special_input(folder) if folder else None
                        if not folder:
                            folder = os.getcwd()
                        filename = input("Template filename (leave blank for 'carson_delete_template.csv'): ").strip().strip('"').strip("'")
                        check_special_input(filename) if filename else None
                        generate_template_file(folder, filename or None)
                        print("\nTemplate created. Fill it in and re-run with option 2.")
                        sys.exit(0)

                    elif choice == '2':
                        valid_file = False
                        while not valid_file:
                            path = input("\nEnter path to CSV or XLSX file: ").strip().strip('"').strip("'")
                            check_special_input(path)
                            valid_file = check_input(path)
                            if not valid_file:
                                print("Please try again.\n")
                            else:
                                input_file_path = os.path.abspath(path)
                                entries = read_batch_file(input_file_path)
                                print(f"Loaded {len(entries)} entry/entries from file.")
                        valid_choice = True

                    elif choice == '3':
                        entries = [collect_one_off_entry()]
                        valid_choice = True

                    else:
                        print("Invalid choice. Please enter 1, 2, or 3.\n")

            # ---- Determine output directory ----
            if args and args.output:
                output_dir = args.output
            else:
                default_dir = os.path.dirname(input_file_path) if input_file_path else os.getcwd()
                print(f"\nDefault output directory: {default_dir}")
                use_different = input("Use a different output directory? (y/N): ").strip().lower()
                check_special_input(use_different)

                if use_different in ['y', 'yes']:
                    output_dir = input("Enter output directory path: ").strip().strip('"').strip("'")
                    check_special_input(output_dir)
                else:
                    output_dir = default_dir

            if not os.path.exists(output_dir):
                print(f"Creating output directory: {output_dir}")
                os.makedirs(output_dir)

            print(f"Output directory: {output_dir}")

            # ---- Phase 1: Generate data files and commands ----
            print(f"\n{'=' * 60}")
            print(f"Generating files for {len(entries)} entry/entries...")
            print(f"{'=' * 60}\n")

            prepared_entries = []
            prep_failed = []

            for idx, entry in enumerate(entries, 1):
                label = f"{entry['building_id']} / {entry['meter_name']}"
                print(f"[{idx}/{len(entries)}] {label}")
                try:
                    prepared = prepare_entry(
                        entry['building_id'],
                        entry['meter_name'],
                        entry['external_id'],
                        entry['start_date'],
                        entry['end_date'],
                        output_dir
                    )
                    prepared_entries.append(prepared)
                    print(f"  ✓ Ready\n")
                except Exception as e:
                    prep_failed.append((label, str(e)))
                    print(f"  ✗ Failed to prepare: {e}\n")

            if prep_failed:
                print("The following entries could not be prepared and will be skipped:")
                for label, err in prep_failed:
                    print(f"  - {label}: {err}")
                print()

            if not prepared_entries:
                print("No entries ready to run. Exiting.")
                break

            # ---- Phase 2: Review ----
            print("=" * 60)
            print("Review — Commands to be run:")
            print("=" * 60)
            for idx, p in enumerate(prepared_entries, 1):
                print(f"\n[{idx}] {p['building_id']} / {p['meter_name']}")
                print(f"  External ID : {p['external_id']}")
                print(f"  Start       : {format_delete_timestamp(p['start_date'])}")
                print(f"  End         : {format_delete_timestamp(p['end_date'])}")
                print(f"  Data file   : {p['data_file']}")
                print(f"  Command     : {p['command']}")
            print()

            confirm = input("Proceed with running all commands? (y/N): ").strip().lower()
            check_special_input(confirm)
            if confirm not in ['y', 'yes']:
                print("\nAborted. Data files have been written but no commands were run.")
                print(f"Files are in: {output_dir}/")
                break

            # ---- Phase 3: Execute ----
            print(f"\n{'=' * 60}")
            print("Setting up environment...")
            print(f"{'=' * 60}\n")

            blaze_cwd = run_prerequisites()
            if blaze_cwd is None:
                print("\nEnvironment setup failed. No delete commands were run.")
                print(f"Files are in: {output_dir}/")
                break

            print(f"\n{'=' * 60}")
            print("Running commands...")
            print(f"{'=' * 60}\n")

            successful = 0
            failed = 0
            failed_entries = []

            for idx, prepared in enumerate(prepared_entries, 1):
                label = f"{prepared['building_id']} / {prepared['meter_name']}"
                print(f"[{idx}/{len(prepared_entries)}] {label}")
                try:
                    execute_entry(prepared, cwd=blaze_cwd)
                    successful += 1
                    print(f"  ✓ Done\n")
                except Exception as e:
                    failed += 1
                    failed_entries.append((label, str(e)))
                    print(f"  ✗ Failed: {e}\n")

            # ---- Summary ----
            print("=" * 60)
            print("Summary")
            print("=" * 60)
            print(f"Total   : {len(prepared_entries)}")
            print(f"Success : {successful}")
            print(f"Failed  : {failed}")

            if failed_entries:
                print("\nFailed entries:")
                for label, err in failed_entries:
                    print(f"  - {label}: {err}")

            print(f"\nOutput saved to: {output_dir}/")
            print("=" * 60)

            # ---- Auto-generate combined summary report ----
            combined_path = os.path.join(output_dir, "combined_delete_summary.txt")
            count = combine_summary_files(output_dir, combined_path)
            if count > 0:
                print(f"\nCombined summary ({count} entry/entries): {combined_path}")

            break

        except ResetException:
            continue
