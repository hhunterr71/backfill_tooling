from venv import create
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
 
dirname = os.path.dirname(os.path.abspath(__file__))
 
unit_df = pd.DataFrame({'pointName':['Current', 'Current_A', 'Current_B', 'Current_C', 'Frequency', 'PF', 'PF_A', 'PF_B', 'PF_C', 'Volts_AB', 'Volts_AN', 'Volts_BC', 'Volts_BN', 'Volts_CA', 'Volts_CN', 'Volts_LL', 'Volts_LN', 'kVAR_Demand', 'kVA_Demand', 'kVAR', 'kVA', 'kW', 'kW_A', 'kW_B', 'kW_C', 'kWh','Temperature','GasFlowRate_Unscaled','GasFlowTotal_Unscaled','WaterFlowTotal','WaterFlowRate','kWh_rec','water_volume_accumulator'], 'Units':['amperes', 'amperes', 'amperes', 'amperes', 'hertz', 'no-units', 'no-units', 'no-units', 'no-units', 'volts', 'volts', 'volts', 'volts', 'volts', 'volts', 'volts', 'volts', 'kilovolt-amperes-reactive', 'kilovolt-amperes', 'kilovolt-amperes-reactive', 'kilovolt-amperes', 'kilowatts', 'kilowatts', 'kilowatts', 'kilowatts', 'kilowatt-hours','degrees-fahrenheit','cubic-feet-per-hour','cubic-feet','us-gallons','us-gallons-per-minute','kilowatts','us-gallons']})

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
                df[col] = pd.to_numeric(cleaned, errors='ignore')
            except (AttributeError, TypeError):
                # Skip columns that don't support string operations
                pass

    return df
 
def format_timestamps(normalized_file):
    """
    - Format the timestamps by adding the required time zone offset (accounts for daylight savings)
    - Shift the timestamps ahead by 15-minutes (due to bug in BixBox aggregation)
    - TODO: determine if this is still a bug
    Args:
        normalized_file: pivotted CSV file
    Returns:
        Pivotted CSV file with formatted timestamps
    """
    df = normalized_file
    df.timestamp = pd.to_datetime(df.timestamp, format='ISO8601')
    #df.timestamp = df.timestamp.isoformat(timespec='milliseconds')
    df.timestamp = df.timestamp.dt.tz_localize('America/Los_Angeles', ambiguous='NaT')
    df.timestamp = df.timestamp + pd.Timedelta(minutes=15)
    return df

def pivot_flat_file(input_path):
    """
    Pivot a single flat CSV or XLSX file and split into distinct files
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

        # Validate required columns
        required_columns = ['building', 'device', 'timestamp', 'pointName', 'value']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")

        # Check if dataframe is empty
        if df.empty:
            raise ValueError("Input file is empty or contains no valid data")

        for (building, device), group in df.groupby(['building', 'device']):
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
                logging.basicConfig(filename = os.path.join(newpath, 'backfill_log.log'), encoding = 'utf-8', level = logging.DEBUG)
                logging.info('Input File Path: '+ os.path.join(dirname, input_path))
                logging.info('Action Performed: Pivoting and Timestamp Formatting')

            output_path = os.path.join(newpath, f'{building}_{device}.csv')
            table.to_csv(output_path, index=False, quoting=csv.QUOTE_NONNUMERIC, float_format='%.10g')
            logging.info('Output File Path: '+ output_path + ', Date Range: '+ start_date + ' to ' + end_date)

            # Create unit file
            a_df = df_single.drop_duplicates(['device','pointName'])[['device','pointName']]
            unit_table = a_df.merge(unit_df, how = "left", on = "pointName")
            unit_table = unit_table.rename({'device':'Device Id', 'pointName':'Field Name'}, axis="columns")
            output_unit_path = os.path.join(newpath, f'{building}_'+f'{device}'+'_units.csv')
            unit_table.to_csv(output_unit_path, index=False, quoting=csv.QUOTE_NONNUMERIC)
            logging.info('Output Unit File Path: '+ output_unit_path)
            unmatched_units = unit_table[unit_table['Units'].isnull()]
            field_list = ', '.join(unmatched_units['Field Name'])
            if not unmatched_units.empty:
                logging.warning('The following field(s) is not recognized: '+ field_list + '. Please review and add units if the field(s) is valid.')
                print('WARNING: The following field(s) is not recognized: '+ field_list + '. Please review and add units if the field(s) is valid.')

    except KeyError as e:
        raise ValueError(f"Column error during pivot operation: {str(e)}")
    except pd.errors.ParserError as e:
        raise ValueError(f"Error parsing file: {str(e)}")
    except Exception as e:
        raise Exception(f"Unexpected error processing file: {str(e)}")
 
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
                print("  1. Process a single file")
                print("  2. Process all files in a directory")
                print("  3. Process multiple individual files")
                print()

                valid_choice = False
                while not valid_choice:
                    choice = input("Enter choice (1-3): ").strip()
                    check_special_input(choice)

                    if choice == '1':
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

                    else:
                        print("Invalid choice. Please enter 1, 2, or 3.\n")

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