#!/usr/bin/env python3

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import re
import os
import glob
import numpy as np
import json
import argparse
from pathlib import Path
from datetime import datetime

try:
    from windrose import WindroseAxes
    windrose_installed = True
except ImportError:
    windrose_installed = False

# Maps the short column names to a full description and unit for plotting.
PLOT_METADATA = {
    'S': ('3D Wind Speed', 'Speed (m/s)'),
    'S1': ('Sonic Speed 1', 'Speed (m/s)'),
    'S2': ('2D Wind Speed', 'Speed (m/s)'),
    'S3': ('Sonic Speed 3', 'Speed (m/s)'),
    'D': ('Wind Direction', 'Direction (°)'),
    'U': ('U-Vector (Zonal Wind)', 'Speed (m/s)'),
    'V': ('V-Vector (Meridional Wind)', 'Speed (m/s)'),
    'W': ('W-Vector (Vertical Wind)', 'Speed (m/s)'),
    'T': ('Air Temperature', 'Temperature (°C)'),
    'T1': ('Temperature 1', 'Temperature (°C)'),
    'T2': ('Temperature 2', 'Temperature (°C)'),
    'H': ('Relative Humidity', 'Humidity (%)'),
    'P': ('Atmospheric Pressure', 'Pressure (hPa)'),
    'PI': ('Pitch Angle', 'Angle (°)'),
    'RO': ('Roll Angle', 'Angle (°)'),
    'MD': ('Magnetic Heading', 'Direction (°)'),
    'TD': ('True Heading', 'Direction (°)')
}

def detect_log_format(file_path):
    """
    Detects the format of the log file (old tagged format vs new CSV format).
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            first_line = f.readline().strip()
            second_line = f.readline().strip()
            
        # Check if it's new CSV format (header line)
        if ',' in first_line and ('Time' in first_line or 'timestamp' in first_line):
            return 'csv'
        
        # Check if it's old tagged format
        if re.match(r'\[(.*?)\]\s*,', first_line) or re.match(r'\[(.*?)\]\s*,', second_line):
            return 'tagged'
            
        # Check if it's macOS JSON format
        if 'parsed_json' in first_line:
            return 'json'
            
        return 'unknown'
        
    except Exception as e:
        print(f"Error detecting format for {file_path}: {e}")
        return 'unknown'

def parse_csv_log(file_path):
    """
    Parses a CSV log file (new format).
    """
    print(f"[INFO] Processing CSV file: {os.path.basename(file_path)}")
    
    try:
        df = pd.read_csv(file_path)
        
        # Handle different timestamp column names
        timestamp_col = None
        for col in ['Time', 'timestamp', 'Timestamp']:
            if col in df.columns:
                timestamp_col = col
                break
                
        if timestamp_col is None:
            print(f"[ERROR] No timestamp column found in {file_path}")
            return None
            
        df[timestamp_col] = pd.to_datetime(df[timestamp_col], errors='coerce')
        df.dropna(subset=[timestamp_col], inplace=True)
        
        # Convert numeric columns
        for col in df.columns:
            if col != timestamp_col:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Filter out error values (-99.50) from wind speed columns
        wind_speed_cols = ['S', 'S2', 'S3']
        for col in wind_speed_cols:
            if col in df.columns:
                df.loc[df[col] == -99.50, col] = pd.NA
                print(f"    [INFO] Filtered {(df[col] == -99.50).sum()} error values (-99.50) from column '{col}'")
                
        df.set_index(timestamp_col, inplace=True)
        return df
        
    except Exception as e:
        print(f"[ERROR] Error processing CSV {file_path}: {e}")
        return None

def parse_json_log(file_path):
    """
    Parses a JSON log file (macOS format with parsed_json column).
    """
    print(f"[INFO] Processing JSON file: {os.path.basename(file_path)}")
    
    try:
        df = pd.read_csv(file_path)
        
        if 'timestamp' not in df.columns or 'parsed_json' not in df.columns:
            print(f"[ERROR] Missing required columns in {file_path}")
            return None
            
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        df.dropna(subset=['timestamp'], inplace=True)
        
        # Parse JSON data
        parsed_data = []
        for idx, row in df.iterrows():
            try:
                json_data = json.loads(row['parsed_json'])
                row_data = {'timestamp': row['timestamp']}
                for key, value in json_data.items():
                    try:
                        row_data[key] = float(value)
                    except (ValueError, TypeError):
                        row_data[key] = value
                parsed_data.append(row_data)
            except (json.JSONDecodeError, KeyError):
                continue
                
        if not parsed_data:
            print(f"[WARNING] No valid JSON data found in {file_path}")
            return None
            
        df = pd.DataFrame(parsed_data)
        df.set_index('timestamp', inplace=True)
        return df
        
    except Exception as e:
        print(f"[ERROR] Error processing JSON {file_path}: {e}")
        return None

def parse_tagged_log(file_path):
    """
    Parses a tagged log file (old format).
    """
    print(f"[INFO] Processing tagged file: {os.path.basename(file_path)}")
    
    parsed_data = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if "Mode" in line and "overriding" in line:
                    continue
                match = re.match(r'\[(.*?)\]\s*,(.*)', line)
                if not match:
                    continue
                timestamp_str, data_str = match.groups()
                row_data = {'Timestamp': timestamp_str}
                pairs = data_str.strip().split(',')
                for pair in pairs:
                    parts = re.split(r'\s+', pair.strip(), maxsplit=1)
                    if len(parts) == 2:
                        key, value = parts
                        row_data[key] = value
                parsed_data.append(row_data)

        if not parsed_data:
            print(f"[WARNING] No valid data found in {file_path}")
            return None

        df = pd.DataFrame(parsed_data)
        df['Timestamp'] = pd.to_datetime(df['Timestamp'], errors='coerce')
        df.dropna(subset=['Timestamp'], inplace=True)
        for col in df.columns.drop('Timestamp'):
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df.set_index('Timestamp', inplace=True)
        return df

    except Exception as e:
        print(f"[ERROR] Error processing tagged {file_path}: {e}")
        return None

def parse_trisonica_log(file_path):
    """
    Universal parser that detects format and calls appropriate parser.
    """
    log_format = detect_log_format(file_path)
    
    if log_format == 'csv':
        return parse_csv_log(file_path)
    elif log_format == 'json':
        return parse_json_log(file_path)
    elif log_format == 'tagged':
        return parse_tagged_log(file_path)
    else:
        print(f"[ERROR] Unknown format for {file_path}")
        return None

def save_time_series_plot(df, y_column, title, y_label, output_filename):
    """
    Generates and saves a time-series plot for any variable.
    """
    if df is None or y_column not in df.columns or df[y_column].isnull().all():
        print(f"    [SKIP] '{title}' plot (no data).")
        return
        
    print(f"    [PLOT] Generating '{title}' plot...")
    
    plt.style.use('default')
    fig, ax = plt.subplots(figsize=(15, 8))
    
    # Plot with different styles based on data density
    data_points = len(df[y_column].dropna())
    if data_points > 1000:
        ax.plot(df.index, df[y_column], linestyle='-', linewidth=0.8, alpha=0.7)
    else:
        ax.plot(df.index, df[y_column], marker='o', linestyle='-', markersize=3, linewidth=1)
    
    ax.set_title(title, fontsize=16, fontweight='bold')
    ax.set_xlabel("Time (UTC)", fontsize=12)
    ax.set_ylabel(y_label, fontsize=12)
    
    # Format x-axis based on data duration
    duration = df.index.max() - df.index.min()
    if duration.total_seconds() < 3600:  # Less than 1 hour
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    elif duration.total_seconds() < 86400:  # Less than 1 day
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    else:  # More than 1 day
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
    
    # Add grid and statistics
    ax.grid(True, alpha=0.3)
    
    # Add statistics text box
    stats_text = f'Points: {data_points}\n'
    if not df[y_column].isnull().all():
        stats_text += f'Min: {df[y_column].min():.2f}\n'
        stats_text += f'Max: {df[y_column].max():.2f}\n'
        stats_text += f'Mean: {df[y_column].mean():.2f}\n'
        stats_text += f'Std: {df[y_column].std():.2f}'
    
    ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, fontsize=10,
            verticalalignment='top', bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
    
    fig.autofmt_xdate()
    plt.tight_layout()
    
    plt.savefig(output_filename, dpi=300, bbox_inches='tight')
    plt.close(fig)

def save_wind_rose_plot(df, speed_col, dir_col, output_filename):
    """
    Generates and saves a wind rose plot.
    """
    if not windrose_installed:
        print("    [SKIP] Wind rose plot (windrose not installed).")
        return 
        
    if df is None or speed_col not in df.columns or dir_col not in df.columns:
        print("    [SKIP] Wind rose plot (missing data).")
        return

    # Check for valid data
    speed_data = df[speed_col].dropna()
    dir_data = df[dir_col].dropna()
    
    if len(speed_data) == 0 or len(dir_data) == 0:
        print("    [SKIP] Wind rose plot (no valid data).")
        return

    print("    [PLOT] Generating Wind Rose plot...")
    
    fig = plt.figure(figsize=(12, 10))
    ax = fig.add_subplot(111, projection='windrose')
    
    # Filter out invalid wind directions and speeds (including -99.50 error values)
    valid_idx = (df[dir_col] >= 0) & (df[dir_col] <= 360) & (df[speed_col] >= 0) & (df[speed_col] != -99.50)
    if not valid_idx.any():
        print("    [SKIP] Wind rose plot (no valid wind data).")
        plt.close(fig)
        return
    
    valid_df = df[valid_idx]
    
    ax.bar(valid_df[dir_col], valid_df[speed_col], normed=True, opening=0.8, 
           edgecolor='white', bins=8)
    
    ax.set_legend(title="Wind Speed (m/s)", loc='upper left', bbox_to_anchor=(1.1, 1.05))
    ax.set_title("Wind Rose", fontsize=16, fontweight='bold', y=1.08)
    
    # Add statistics
    stats_text = f'Data Points: {len(valid_df)}\n'
    stats_text += f'Mean Speed: {valid_df[speed_col].mean():.2f} m/s\n'
    stats_text += f'Max Speed: {valid_df[speed_col].max():.2f} m/s\n'
    stats_text += f'Prevailing Dir: {valid_df[dir_col].mode().iloc[0]:.0f}°'
    
    plt.figtext(0.02, 0.02, stats_text, fontsize=10,
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
    
    plt.savefig(output_filename, dpi=300, bbox_inches='tight')
    plt.close(fig)

def save_summary_plot(df, output_filename):
    """
    Creates a summary plot with multiple subplots for key parameters.
    """
    if df is None:
        return
        
    print("    [PLOT] Generating summary plot...")
    
    # Define key parameters to plot
    key_params = []
    if 'S2' in df.columns:
        key_params.append(('S2', '2D Wind Speed (m/s)'))
    elif 'S' in df.columns:
        key_params.append(('S', '3D Wind Speed (m/s)'))
        
    if 'D' in df.columns:
        key_params.append(('D', 'Wind Direction (°)'))
    if 'T' in df.columns:
        key_params.append(('T', 'Temperature (°C)'))
    if 'P' in df.columns:
        key_params.append(('P', 'Pressure (hPa)'))
    
    if not key_params:
        print("    [SKIP] Summary plot (no key parameters found).")
        return
        
    fig, axes = plt.subplots(len(key_params), 1, figsize=(15, 4*len(key_params)), sharex=True)
    
    if len(key_params) == 1:
        axes = [axes]
    
    for i, (param, label) in enumerate(key_params):
        if param in df.columns and not df[param].isnull().all():
            axes[i].plot(df.index, df[param], linewidth=1, alpha=0.8)
            axes[i].set_ylabel(label, fontsize=12)
            axes[i].grid(True, alpha=0.3)
            axes[i].set_title(f'{PLOT_METADATA.get(param, (param, param))[0]}', fontsize=14)
    
    axes[-1].set_xlabel("Time (UTC)", fontsize=12)
    
    # Format x-axis
    duration = df.index.max() - df.index.min()
    if duration.total_seconds() < 3600:
        axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    elif duration.total_seconds() < 86400:
        axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    else:
        axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
    
    plt.suptitle('Trisonica Data Summary', fontsize=16, fontweight='bold')
    fig.autofmt_xdate()
    plt.tight_layout()
    
    plt.savefig(output_filename, dpi=300, bbox_inches='tight')
    plt.close(fig)

def process_single_file(file_path, output_dir=None):
    """
    Process a single log file and generate all plots.
    """
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    
    # Create output directory
    if output_dir is None:
        # Get the directory containing this script
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PLOTS", f"{base_name}_plots")
    
    os.makedirs(output_dir, exist_ok=True)
    print(f"[INFO] Output directory: {output_dir}")
    
    # Parse the log file
    df = parse_trisonica_log(file_path)
    
    if df is None:
        print(f"[ERROR] Failed to parse {file_path}")
        return False
    
    print(f"[INFO] Loaded {len(df)} data points with {len(df.columns)} parameters")
    print(f"[INFO] Parameters: {', '.join(df.columns)}")
    
    # Generate individual parameter plots
    for column in df.columns:
        plot_title, y_axis_label = PLOT_METADATA.get(column, (column, column))
        output_png_path = os.path.join(output_dir, f"{column}_{base_name}.png")
        
        save_time_series_plot(
            df=df, 
            y_column=column, 
            title=f'{plot_title} - {base_name}',
            y_label=y_axis_label,
            output_filename=output_png_path
        )
    
    # Generate wind rose plot
    speed_col = 'S2' if 'S2' in df.columns else ('S' if 'S' in df.columns else None)
    dir_col = 'D' if 'D' in df.columns else None
    
    if speed_col and dir_col:
        wind_rose_path = os.path.join(output_dir, f"WindRose_{base_name}.png")
        save_wind_rose_plot(df=df, speed_col=speed_col, dir_col=dir_col, 
                           output_filename=wind_rose_path)
    
    # Generate summary plot
    summary_path = os.path.join(output_dir, f"Summary_{base_name}.png")
    save_summary_plot(df=df, output_filename=summary_path)
    
    print(f"[SUCCESS] Finished processing {base_name}")
    return True

def main():
    parser = argparse.ArgumentParser(description='Trisonica Data Visualization Tool')
    parser.add_argument('files', nargs='*', help='Specific CSV files to process')
    parser.add_argument('--dir', '-d', help='Directory to scan for CSV files')
    parser.add_argument('--output', '-o', help='Output directory for plots')
    parser.add_argument('--recursive', '-r', action='store_true', 
                       help='Search subdirectories recursively')
    
    args = parser.parse_args()
    
    # Determine files to process
    files_to_process = []
    
    if args.files:
        files_to_process = args.files
    else:
        # Scan for CSV files
        scan_dir = args.dir if args.dir else os.getcwd()
        
        if args.recursive:
            pattern = os.path.join(scan_dir, '**', '*.csv')
            files_to_process = glob.glob(pattern, recursive=True)
        else:
            pattern = os.path.join(scan_dir, '*.csv')
            files_to_process = glob.glob(pattern)
    
    if not files_to_process:
        print("[ERROR] No CSV files found to process.")
        return
    
    print(f"[INFO] Found {len(files_to_process)} CSV files to process")
    
    # Process each file
    success_count = 0
    for file_path in files_to_process:
        print(f"\n{'='*60}")
        try:
            if process_single_file(file_path, args.output):
                success_count += 1
        except Exception as e:
            print(f"[ERROR] Failed to process {file_path}: {e}")
    
    print(f"\n{'='*60}")
    print(f"[SUMMARY] Successfully processed {success_count}/{len(files_to_process)} files")
    
    if not windrose_installed:
        print("\n[NOTE] To generate wind rose plots, install windrose:")
        print("pip install windrose")

if __name__ == "__main__":
    main()