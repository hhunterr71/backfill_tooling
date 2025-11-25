import pandas as pd
import os
import argparse
import sys
import pytz
import csv

def format_timestamps(df):
    """
    Format timestamps by:
    - Converting to ISO8601 datetime format
    - Localizing to America/Los_Angeles timezone (accounts for daylight savings)
    - Adding 15-minute offset (for BixBox aggregation compatibility)

    Args:
        df: DataFrame with timestamp column
    Returns:
        DataFrame with formatted timestamps
    """
    df.timestamp = pd.to_datetime(df.timestamp, format='ISO8601')
    df.timestamp = df.timestamp.dt.tz_localize('America/Los_Angeles', ambiguous='NaT')
    df.timestamp = df.timestamp + pd.Timedelta(minutes=15)
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

def add_metadata_columns(df, building, device, external_id):
    """
    Add metadata columns (building, device, externalID) after the timestamp column.

    Args:
        df: DataFrame with timestamp and measurement columns
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
    """
    try:
        print("\n" + "=" * 60)
        print("PROCESSING MANGO EXPORT")
        print("=" * 60)

        # Step 1: Read CSV
        print("\n[1/6] Reading CSV file...")
        df = pd.read_csv(input_path)
        print(f"  Loaded {len(df)} rows, {len(df.columns)} columns")

        # Step 2: Validate
        print("\n[2/6] Validating data...")
        if 'timestamp' not in df.columns:
            raise ValueError("CSV must contain a 'timestamp' column")
        print("  Validation passed")

        # Step 3: Remove _rendered columns
        print("\n[3/6] Removing _rendered columns...")
        df = remove_rendered_columns(df)

        # Step 4: Rename measurement columns
        print("\n[4/6] Renaming measurement columns...")
        df = rename_point_columns(df)

        # Step 5: Format timestamps
        print("\n[5/6] Formatting timestamps...")
        df = format_timestamps(df)
        print("  Timestamps converted to Pacific timezone with 15-minute offset")

        # Step 6: Add metadata columns
        print("\n[6/6] Adding metadata columns...")
        df = add_metadata_columns(df, building, device, external_id)

        # Save to output
        print("\n" + "=" * 60)
        print("SAVING OUTPUT")
        print("=" * 60)
        df.to_csv(output_path, index=False, quoting=csv.QUOTE_NONNUMERIC, float_format='%.10g')
        print(f"Output saved to: {output_path}")
        print(f"Final shape: {len(df)} rows, {len(df.columns)} columns")
        print("=" * 60)
        print("\nProcessing complete!")

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

    parser.add_argument(
        '-i', '--input',
        type=str,
        help='Path to input Mango CSV export file'
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
        '-e', '--externalid',
        type=str,
        help='External device ID (optional)'
    )

    parser.add_argument(
        '-o', '--output',
        type=str,
        help='Output file path (default: auto-generate from metadata)'
    )

    args = parser.parse_args()

    # If no input provided, return None to trigger interactive mode
    if args.input is None:
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
        # Get input file
        if args and args.input:
            input_file = args.input
        else:
            # Interactive mode
            print("Interactive Mode")
            print("(Use --help to see command-line options)")
            print()
            input_file = input("Enter path to Mango CSV export: ").strip()

        # Validate input file
        if not validate_mango_csv(input_file):
            print("\nExiting due to invalid input file.")
            sys.exit(1)

        # Get metadata
        building, device, external_id = get_user_metadata(args)

        # Determine output path
        if args and args.output:
            output_file = args.output
        else:
            # Auto-generate output filename
            output_file = f"{building}_{device}_mango.csv"

            # Place in same directory as input file
            input_dir = os.path.dirname(os.path.abspath(input_file))
            output_file = os.path.join(input_dir, output_file)

            # In interactive mode, confirm output path
            if not args:
                print(f"\nOutput will be saved to: {output_file}")
                confirm = input("Use this path? (Y/n): ").strip().lower()
                if confirm == 'n':
                    output_file = input("Enter output file path: ").strip()

        # Process the file
        process_mango_export(input_file, output_file, building, device, external_id)

    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\nFatal error: {str(e)}")
        sys.exit(1)
