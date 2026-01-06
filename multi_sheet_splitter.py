import pandas as pd
import os
import argparse
import sys
import csv
import openpyxl
from collections import defaultdict

def validate_sheet(df, sheet_name):
    """
    Validate a single sheet for required columns, data, and parseable timestamps.

    Args:
        df: DataFrame to validate
        sheet_name: Name of the sheet being validated
    Returns:
        Tuple of (is_valid, errors_list, warnings_list)
    """
    errors = []
    warnings = []

    # Check for required columns
    required_columns = ['building', 'device', 'timestamp', 'pointName', 'value']
    missing_columns = [col for col in required_columns if col not in df.columns]

    if missing_columns:
        errors.append(f"Missing required columns: {', '.join(missing_columns)}")
        return False, errors, warnings

    # Check if sheet has data rows
    if len(df) == 0:
        errors.append("Sheet is empty (no data rows)")
        return False, errors, warnings

    # Check for parseable timestamps (sample first 5 rows)
    sample_size = min(5, len(df))
    sample_timestamps = df['timestamp'].head(sample_size)

    try:
        # Try to parse timestamps
        pd.to_datetime(sample_timestamps, utc=True)
    except Exception as e:
        failed_examples = sample_timestamps.head(3).tolist()
        errors.append(f"Cannot parse timestamps. Examples: {failed_examples}")
        return False, errors, warnings

    # Check for extra columns (warning, not error)
    expected_columns = ['building', 'device', 'timestamp', 'pointName', 'value', 'externalID']
    extra_columns = [col for col in df.columns if col not in expected_columns]
    if extra_columns:
        warnings.append(f"Unrecognized columns (will be ignored): {', '.join(extra_columns)}")

    return True, errors, warnings

def extract_metadata(df, sheet_name):
    """
    Extract metadata for file naming and validate consistency.

    Args:
        df: DataFrame with validated data
        sheet_name: Name of the sheet
    Returns:
        Tuple of (building, device, start_date, end_date, warnings_list)
    """
    warnings = []

    # Extract building (check for multiple values)
    unique_buildings = df['building'].unique()
    if len(unique_buildings) > 1:
        warnings.append(f"Multiple buildings found: {list(unique_buildings)}. Using first: '{unique_buildings[0]}'")
    building = str(unique_buildings[0])

    # Extract device (check for multiple values)
    unique_devices = df['device'].unique()
    if len(unique_devices) > 1:
        warnings.append(f"Multiple devices found: {list(unique_devices)}. Using first: '{unique_devices[0]}'")
    device = str(unique_devices[0])

    # Parse timestamps and extract date range
    df['timestamp_parsed'] = pd.to_datetime(df['timestamp'], utc=True)
    start_date = df['timestamp_parsed'].min().date().strftime('%Y-%m-%d')
    end_date = df['timestamp_parsed'].max().date().strftime('%Y-%m-%d')

    # Clean up temporary column
    df.drop('timestamp_parsed', axis=1, inplace=True)

    return building, device, start_date, end_date, warnings

def export_sheet_to_csv(df, output_dir, building, device, start_date, end_date, sheet_name, filename_counter):
    """
    Export sheet to CSV with standard naming.

    Args:
        df: DataFrame to export
        output_dir: Output directory path
        building: Building identifier
        device: Device identifier
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        sheet_name: Original sheet name
        filename_counter: Dict tracking filename usage for collision handling
    Returns:
        Tuple of (output_filename, output_path)
    """
    # Generate base filename
    base_filename = f"{building}_{device}_{start_date}_{end_date}.csv"

    # Handle filename collisions
    if base_filename in filename_counter:
        filename_counter[base_filename] += 1
        # Sanitize sheet name for filename (remove special characters)
        safe_sheet_name = "".join(c for c in sheet_name if c.isalnum() or c in ('_', '-'))
        output_filename = f"{building}_{device}_{start_date}_{end_date}_{safe_sheet_name}.csv"
    else:
        filename_counter[base_filename] = 1
        output_filename = base_filename

    output_path = os.path.join(output_dir, output_filename)

    # Ensure proper column order for compatibility with backfill_data_formater.py
    has_external_id = 'externalID' in df.columns
    if has_external_id:
        column_order = ['building', 'device', 'externalID', 'timestamp', 'pointName', 'value']
    else:
        column_order = ['building', 'device', 'timestamp', 'pointName', 'value']

    # Add any extra columns at the end
    extra_cols = [col for col in df.columns if col not in column_order]
    column_order.extend(extra_cols)

    # Reorder columns
    df_export = df[column_order]

    # Export to CSV with proper formatting
    df_export.to_csv(output_path, index=False, quoting=csv.QUOTE_NONNUMERIC, float_format='%.10g')

    return output_filename, output_path

def generate_validation_report(validation_results, total_sheets, output_dir, input_file, report_to_file=False):
    """
    Generate and print validation report.

    Args:
        validation_results: List of dicts with validation results for each sheet
        total_sheets: Total number of sheets processed
        output_dir: Output directory path
        input_file: Input file path
        report_to_file: Whether to save report to file
    """
    valid_count = sum(1 for r in validation_results if r['valid'])
    invalid_count = total_sheets - valid_count

    report_lines = []
    report_lines.append("=" * 60)
    report_lines.append("Multi-Sheet Excel Splitter - Validation Report")
    report_lines.append("=" * 60)
    report_lines.append(f"Input file: {input_file}")
    report_lines.append(f"Output directory: {output_dir}")
    report_lines.append(f"Total sheets: {total_sheets}")
    report_lines.append("")

    # Valid sheets
    valid_sheets = [r for r in validation_results if r['valid']]
    if valid_sheets:
        report_lines.append(f"Valid Sheets ({valid_count}):")
        for result in valid_sheets:
            output_file = result.get('output_filename', 'N/A')
            report_lines.append(f"  ✓ {result['sheet_name']} → {output_file}")
        report_lines.append("")

    # Invalid sheets
    invalid_sheets = [r for r in validation_results if not r['valid']]
    if invalid_sheets:
        report_lines.append(f"Invalid Sheets ({invalid_count}):")
        for result in invalid_sheets:
            errors = '; '.join(result['errors'])
            report_lines.append(f"  ✗ {result['sheet_name']}: {errors}")
        report_lines.append("")

    # Warnings
    sheets_with_warnings = [r for r in validation_results if r.get('warnings')]
    if sheets_with_warnings:
        report_lines.append("Warnings:")
        for result in sheets_with_warnings:
            for warning in result['warnings']:
                report_lines.append(f"  ! {result['sheet_name']}: {warning}")
        report_lines.append("")

    # Summary
    report_lines.append("Summary:")
    report_lines.append(f"  Processed: {valid_count}/{total_sheets} sheets")
    report_lines.append(f"  Failed: {invalid_count}/{total_sheets} sheets")
    report_lines.append("=" * 60)

    # Print to console
    report_text = "\n".join(report_lines)
    print("\n" + report_text)

    # Optionally save to file
    if report_to_file:
        report_path = os.path.join(output_dir, 'validation_report.txt')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f"\nValidation report saved to: {report_path}")

def validate_excel_file(filepath):
    """
    Validate that the input file exists and is an Excel file.

    Args:
        filepath: Path to input file
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
    if file_extension not in ['.xlsx', '.xls']:
        print(f"ERROR: File must be .xlsx or .xls format. Got: {file_extension}")
        return False

    print(f"Valid Excel file detected: {os.path.basename(filepath)}")
    return True

def process_multi_sheet_excel(input_file, output_dir, skip_invalid=False, report_to_file=False):
    """
    Main processing function for multi-sheet Excel files.

    Args:
        input_file: Path to input Excel file
        output_dir: Output directory for CSV files
        skip_invalid: Whether to skip invalid sheets or stop
        report_to_file: Whether to save validation report to file
    Returns:
        Tuple of (successful_count, failed_count)
    """
    print("\n" + "=" * 60)
    print("Processing Multi-Sheet Excel File")
    print("=" * 60)
    print(f"Input: {input_file}")
    print(f"Output: {output_dir}")
    print("=" * 60)

    # Load Excel file
    try:
        excel_file = pd.ExcelFile(input_file, engine='openpyxl')
        sheet_names = excel_file.sheet_names
        total_sheets = len(sheet_names)

        if total_sheets == 0:
            print("\nERROR: No sheets found in Excel file")
            return 0, 0

        print(f"\nFound {total_sheets} sheet(s)")
        print("")
    except Exception as e:
        print(f"\nERROR: Cannot read Excel file: {str(e)}")
        return 0, 0

    # Process each sheet
    validation_results = []
    filename_counter = defaultdict(int)
    successful = 0
    failed = 0

    for idx, sheet_name in enumerate(sheet_names, 1):
        print(f"[{idx}/{total_sheets}] Processing sheet: '{sheet_name}'")

        result = {
            'sheet_name': sheet_name,
            'valid': False,
            'errors': [],
            'warnings': []
        }

        try:
            # Read sheet
            df = pd.read_excel(excel_file, sheet_name=sheet_name)

            # Validate sheet
            is_valid, errors, warnings = validate_sheet(df, sheet_name)
            result['errors'] = errors
            result['warnings'] = warnings

            if not is_valid:
                result['valid'] = False
                failed += 1
                print(f"  ✗ Validation failed: {'; '.join(errors)}")

                if not skip_invalid:
                    print("\nStopping due to validation error. Use --skip-invalid to continue processing.")
                    validation_results.append(result)
                    break
            else:
                # Extract metadata
                building, device, start_date, end_date, metadata_warnings = extract_metadata(df, sheet_name)
                result['warnings'].extend(metadata_warnings)

                # Export to CSV
                output_filename, output_path = export_sheet_to_csv(
                    df, output_dir, building, device, start_date, end_date,
                    sheet_name, filename_counter
                )

                result['valid'] = True
                result['output_filename'] = output_filename
                result['output_path'] = output_path
                successful += 1

                print(f"  ✓ Exported to: {output_filename}")
                if result['warnings']:
                    for warning in result['warnings']:
                        print(f"  ! Warning: {warning}")

        except Exception as e:
            result['valid'] = False
            result['errors'].append(f"Unexpected error: {str(e)}")
            failed += 1
            print(f"  ✗ Error: {str(e)}")

            if not skip_invalid:
                print("\nStopping due to error. Use --skip-invalid to continue processing.")
                validation_results.append(result)
                break

        validation_results.append(result)

        if idx < total_sheets:
            print()

    # Generate validation report
    generate_validation_report(validation_results, total_sheets, output_dir, input_file, report_to_file)

    return successful, failed

def parse_arguments():
    """
    Parse command-line arguments.

    Returns:
        Parsed arguments or None if interactive mode
    """
    parser = argparse.ArgumentParser(
        description='Multi-Sheet Excel Splitter - Split multi-sheet Excel files into individual CSVs',
        epilog='If no arguments provided, interactive mode will be used.',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        '-i', '--input',
        type=str,
        help='Path to multi-sheet Excel file (.xlsx or .xls)'
    )

    parser.add_argument(
        '-o', '--output',
        type=str,
        default=None,
        help='Output directory for CSV files (default: current directory)'
    )

    parser.add_argument(
        '--skip-invalid',
        action='store_true',
        help='Skip invalid sheets instead of stopping on first error'
    )

    parser.add_argument(
        '--report',
        action='store_true',
        help='Generate validation_report.txt file in output directory'
    )

    args = parser.parse_args()

    # If no input provided, return None for interactive mode
    if args.input is None:
        return None

    return args

### MAIN
if __name__ == "__main__":
    print("=" * 60)
    print("Multi-Sheet Excel Splitter")
    print("Splits multi-sheet Excel files into individual CSV files")
    print("=" * 60)
    print()

    # Parse command-line arguments
    args = parse_arguments()

    try:
        # Determine input file
        if args and args.input:
            # Command-line mode
            input_file = args.input
            if not validate_excel_file(input_file):
                print("\nExiting due to invalid input file.")
                sys.exit(1)
        else:
            # Interactive mode
            print("Interactive Mode")
            print("(Use --help to see command-line options)")
            print()

            input_file = input("Enter path to multi-sheet Excel file: ").strip()
            if not validate_excel_file(input_file):
                print("\nExiting due to invalid input file.")
                sys.exit(1)

        input_file = os.path.abspath(input_file)

        # Determine output directory
        if args and args.output:
            output_dir = args.output
        else:
            # Default to directory of input file
            default_output = os.path.dirname(input_file)

            if not args:
                # Interactive mode - ask user
                print(f"\nDefault output directory: {default_output}")
                use_different = input("Use a different output directory? (y/N): ").strip().lower()

                if use_different in ['y', 'yes']:
                    output_dir = input("Enter output directory path: ").strip()
                else:
                    output_dir = default_output
            else:
                output_dir = default_output

        # Create output directory if it doesn't exist
        if not os.path.exists(output_dir):
            print(f"Creating output directory: {output_dir}")
            os.makedirs(output_dir)

        # Determine flags
        skip_invalid = args.skip_invalid if args else False
        report_to_file = args.report if args else False

        # Process the Excel file
        successful, failed = process_multi_sheet_excel(
            input_file, output_dir, skip_invalid, report_to_file
        )

        # Exit with appropriate status
        if failed > 0 and not skip_invalid:
            sys.exit(1)
        elif successful == 0:
            print("\nNo sheets were successfully processed.")
            sys.exit(1)
        else:
            print("\nProcessing complete!")
            sys.exit(0)

    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\nFatal error: {str(e)}")
        sys.exit(1)
