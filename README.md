# Trisonica Data Logger

Data logging and visualization tool for Trisonica ultrasonic anemometer, for Linux systems.

## Features

- **Real-time data logging** with Rich-based terminal interface
- **Auto-detection** of Trisonica devices on serial ports
- **CSV data export** with dynamic column management
- **Statistics tracking** with min/max/mean/std calculations
- **Data visualization** with time-series plots and wind roses
- **Error handling** and data quality monitoring
- **Portable filepaths** - works anywhere

## File Structure

```
datalogger/
├── datalogger.py          # Main logging application
├── DataVis.py             # Post-processing visualization tool
├── test_datalogger.py     # Test suite
├── OUTPUT/                # Created automatically - data logs go here
└── PLOTS/                 # Created automatically - visualization plots go here
```

## Requirements

```bash
pip install serial pyserial rich pandas matplotlib windrose
```

## Usage

### Data Logging

```bash
# Auto-detect Trisonica and start logging
python datalogger.py

# Specify serial port
python datalogger.py --port /dev/ttyUSB0

# Custom log directory
python datalogger.py --log-dir /path/to/logs

# Hide raw data stream
python datalogger.py --hide-raw

# Disable statistics logging
python datalogger.py --no-stats
```

### Data Visualization

```bash
# Visualize specific files
python DataVis.py file1.csv file2.csv

# Process all CSV files in directory
python DataVis.py --dir /path/to/logs

# Recursive search in subdirectories
python DataVis.py --dir /path/to/logs --recursive

# Custom output directory
python DataVis.py --output /path/to/plots file.csv
```

### Testing

```bash
python test_datalogger.py
```

## Data Output

### Log Files (OUTPUT/)
- `TrisonicaData_YYYY-MM-DD_HHMMSS.csv` - Main data log
- `TrisonicaStats_YYYY-MM-DD_HHMMSS.csv` - Statistics summary

### Plot Files (PLOTS/)
- Individual parameter plots (e.g., `S_filename.png`, `T_filename.png`)
- `WindRose_filename.png` - Wind pattern visualization
- `Summary_filename.png` - Multi-parameter overview

## Supported Parameters

| Code | Description | Unit |
|------|-------------|------|
| S    | Total wind speed | m/s |
| S2   | Alt wind speed calc | m/s |
| D    | Wind direction | 0-360° |
| U    | East-west component | m/s |
| V    | North-south component | m/s |
| W    | Vertical component | m/s |
| T    | Temperature | °C |
| H    | Humidity | % |
| P    | Pressure | hPa |
| PI   | Pitch angle | ° |
| RO   | Roll angle | ° |
| MD   | Magnetic heading | ° |
| TD   | True heading | ° |

## Log Format Support

- **CSV format** - Modern structured logs
- **Tagged format** - Legacy `[timestamp], param value, param value` format
- **JSON format** - macOS parsed_json column format

## Error Handling

- Filters out error values (-99.50, -99.70)
- Tracks sensor health and data quality
- Errors are noted in the Stats file generated after each measurement

## Platform Support

Optimized for Linux but portable to other Unix-like systems. Serial port patterns:
- `/dev/ttyUSB*` - USB-to-serial adapters
- `/dev/ttyACM*` - USB CDC devices
- `/dev/ttyS*` - Traditional serial ports
- `/dev/serial/by-id/*` - Persistent device names
