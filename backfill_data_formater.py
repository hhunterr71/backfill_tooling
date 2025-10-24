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
 
unit_df = pd.DataFrame({'pointName':['Current', 'Current_A', 'Current_B', 'Current_C', 'Frequency', 'PF', 'PF_A', 'PF_B', 'PF_C', 'Volts_AB', 'Volts_AN', 'Volts_BC', 'Volts_BN', 'Volts_CA', 'Volts_CN', 'Volts_LL', 'Volts_LN', 'kVAR_Demand', 'kVA_Demand', 'kVAR', 'kVA', 'kW', 'kW_A', 'kW_B', 'kW_C', 'kWh','Temperature','GasFlowRate_Unscaled','GasFlowTotal_Unscaled','WaterFlowTotal','WaterFlowRate','kWh_rec'], 'Units':['amperes', 'amperes', 'amperes', 'amperes', 'hertz', 'no-units', 'no-units', 'no-units', 'no-units', 'volts', 'volts', 'volts', 'volts', 'volts', 'volts', 'volts', 'volts', 'kilovolt-amperes-reactive', 'kilovolt-amperes', 'kilovolt-amperes-reactive', 'kilovolt-amperes', 'kilowatts', 'kilowatts', 'kilowatts', 'kilowatts', 'kilowatt-hours','degrees-fahrenheit','cubic-feet-per-hour','cubic-feet','us-gallons','us-gallons-per-minute','kilowatts']})
 
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
    """
    # split the single CSV/XLSX into distinct files for each device
    df = read_data_file(input_path)
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
 
def parse_arguments():
    """
    Parse command-line arguments for the backfill data formatter.
    Returns:
        Parsed arguments or None if user wants interactive mode
    """
    parser = ArgumentParser(
        description='Backfill Data Formatter - Process single CSV or XLSX telemetry files',
        epilog='If no arguments provided, interactive mode will be used.'
    )
    parser.add_argument(
        '-i', '--input',
        type=str,
        help='Path to input CSV or XLSX file'
    )
    parser.add_argument(
        '-o', '--output',
        type=str,
        default=None,
        help='Output directory path (default: current working directory)'
    )

    args = parser.parse_args()

    # If no arguments provided, return None to trigger interactive mode
    if args.input is None:
        return None

    return args

### MAIN
if __name__ == "__main__":
    print("=" * 60)
    print("Backfill Data Formatter - Single File Processor")
    print("Supports: CSV and XLSX files")
    print("=" * 60)
    print()

    # Try to parse command-line arguments
    args = parse_arguments()

    # Get input file path
    if args and args.input:
        # Command-line mode
        input_filepath = args.input
        if not check_input(input_filepath):
            print("\nExiting due to invalid input file.")
            sys.exit(1)
    else:
        # Interactive mode
        print("Interactive Mode")
        print("(Use --help to see command-line options)")
        print()
        valid_input_file = False
        while not valid_input_file:
            input_filepath = input('Enter path to CSV or XLSX file: ')
            valid_input_file = check_input(input_filepath)
            if not valid_input_file:
                print("Please try again.\n")

    # Get output directory (default: current working directory)
    if args and args.output:
        outputdirname = args.output
        if not os.path.exists(outputdirname):
            print(f"Creating output directory: {outputdirname}")
            os.makedirs(outputdirname)
    else:
        # Default to current working directory
        outputdirname = os.getcwd()
        print(f"Output directory: {outputdirname}")

    # Process the file
    print(f"\nProcessing file: {os.path.basename(input_filepath)}")
    print("This may take a moment...")

    output = pivot_flat_file(input_filepath)

    print("\n" + "=" * 60)
    print("Processing complete!")
    print(f"Output saved to: {outputdirname}/")
    print("Folder structure: {{building}}_{{device}}_{{start-date}}_{{end-date}}/")
    print("=" * 60)