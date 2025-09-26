#!/usr/bin/env python3

import serial
import datetime
import time
import sys
import signal
import re
import os
import glob
import argparse
from collections import deque
from typing import Dict, Optional, List
from dataclasses import dataclass, field
from pathlib import Path

# Linux optimized imports
from rich.console import Console
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.text import Text
from rich.align import Align
from rich import box
from rich.columns import Columns
from rich.tree import Tree
# from rich.sparkline import Sparkline  # Not available in all Rich versions
# from rich.bar import Bar

# --- Configuration ---
DEFAULT_BAUD_RATE = 115200
MAX_DATAPOINTS = 1000  # Adjust based on Linux system capabilities
UPDATE_INTERVAL = 0.05  # May need adjustment for different hardware
LOG_ROTATION_SIZE = 50 * 1024 * 1024  # 50MB logs

@dataclass
class Config:
    serial_port: str = "auto"
    baud_rate: int = DEFAULT_BAUD_RATE
    log_dir: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "OUTPUT")
    show_raw_data: bool = True
    save_statistics: bool = True
    plot_enabled: bool = False  # Future: real-time plotting
    max_log_size: int = LOG_ROTATION_SIZE

@dataclass
class DataPoint:
    timestamp: datetime.datetime
    raw_data: str
    parsed_data: Dict[str, str] = field(default_factory=dict)

@dataclass
class Statistics:
    min_val: float = 0.0
    max_val: float = 0.0
    mean_val: float = 0.0
    current_val: float = 0.0
    std_dev: float = 0.0
    count: int = 0
    values: deque = field(default_factory=lambda: deque(maxlen=100))

class TrisonicaDataLoggerLinux:
    def __init__(self, config: Config):
        self.config = config
        self.serial_port = None
        self.log_file = None
        self.stats_file = None
        self.console = Console()
        self.running = False
        self.start_time = time.time()
        
        # Data storage
        self.data_points = deque(maxlen=MAX_DATAPOINTS)
        self.point_count = 0
        self.stats = {}
        
        # CSV column management
        self.csv_columns = ['timestamp']  # Start with timestamp
        self.csv_headers_written = False
        
        # Linux specific
        self.last_update = time.time()
        self.update_rate = 0.0
        
        # Visualization data storage
        self.viz_data = {
            'wind_speed': deque(maxlen=50),      # S or S2 values
            'temperature': deque(maxlen=50),     # T values
            'wind_direction': deque(maxlen=50),  # D values
            'timestamps': deque(maxlen=50)       # For trend analysis
        }

        # Data quality tracking
        self.data_quality = {
            'total_readings': 0,
            'error_count': 0,
            'last_error_time': None,
            'sensor_health': {},
            'connection_drops': 0,
            'last_connection_time': time.time()
        }

        # Recent wind measurements for trend analysis
        self.recent_wind_speeds = deque(maxlen=1000)  # Last 1000 wind speed readings
        self.recent_wind_directions = deque(maxlen=1000)  # Last 1000 wind direction readings
        
        # Ensure log directory exists
        os.makedirs(config.log_dir, exist_ok=True)
        
        # Setup logging
        self.setup_logging()
        
        # Signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        # Print startup banner
        self.print_startup_banner()

        # Initialize sensor health tracking
        for param in ['S', 'S2', 'D', 'T', 'H', 'P', 'U', 'V', 'W']:
            self.data_quality['sensor_health'][param] = {
                'status': 'Unknown',
                'error_rate': 0.0,
                'last_good_reading': None
            }
        
    def print_startup_banner(self):
        """Print a nice startup banner"""
        banner = """
╔═══════════════════════════════════════════════════════════════╗
║                 TRISONICA DATA LOGGER - Linux                ║
║                                                               ║
║  Optimized for Linux development and high-performance logging ║
╚═══════════════════════════════════════════════════════════════╝
"""
        self.console.print(banner, style="bold cyan")
        self.console.print(f"Platform: Linux")
        self.console.print(f"Python: {sys.version.split()[0]}")
        self.console.print(f"Log Directory: {self.config.log_dir}")
        self.console.print(f"Max Data Points: {MAX_DATAPOINTS:,}")
        self.console.print("─" * 60)
        
    def find_serial_ports(self) -> List[str]:
        """Find Linux serial ports"""
        patterns = [
            '/dev/ttyUSB*',      # USB-to-serial adapters
            '/dev/ttyACM*',      # USB CDC devices
            '/dev/ttyS*',        # Traditional serial ports
            '/dev/serial/by-id/*'  # Persistent device names
        ]
        
        ports = []
        for pattern in patterns:
            ports.extend(glob.glob(pattern))
            
        return sorted(set(ports))
        
    def auto_detect_serial_port(self) -> Optional[str]:
        """Auto-detect Trisonica on Linux"""
        ports = self.find_serial_ports()
        
        if not ports:
            self.console.print("[ERROR] No serial ports found!", style="bold red")
            return None
            
        self.console.print(f"[INFO] Found {len(ports)} serial port(s):")
        for i, port in enumerate(ports):
            self.console.print(f"  {i+1}. {port}")
            
        # Test each port
        for port in ports:
            try:
                self.console.print(f"[TEST] Testing {port}...", end="")
                ser = serial.Serial(port, self.config.baud_rate, timeout=2)
                
                # Read several lines to detect Trisonica
                trisonica_detected = False
                for _ in range(10):  # Multiple attempts to detect
                    try:
                        line = ser.readline().decode('ascii', errors='ignore').strip()
                        if line and any(param in line for param in ['S ', 'S2', 'D ', 'T ', 'U ', 'V ']):
                            trisonica_detected = True
                            break
                    except:
                        continue
                        
                ser.close()
                
                if trisonica_detected:
                    self.console.print(" [SUCCESS] Trisonica detected!", style="bold green")
                    return port
                else:
                    self.console.print(" [FAIL] No Trisonica data", style="dim")
                    
            except Exception as e:
                self.console.print(f" [ERROR] {e}", style="red")
                
        self.console.print("[WARNING] No Trisonica devices found", style="bold yellow")
        return None
        
    def setup_logging(self):
        """Setup enhanced logging for Linux"""
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
        
        # Data log
        self.log_filename = f"TrisonicaData_{timestamp}.csv"
        self.log_path = os.path.join(self.config.log_dir, self.log_filename)
        self.log_file = open(self.log_path, 'w', newline='')
        # Headers will be written dynamically when first data arrives
        
        # Statistics log
        if self.config.save_statistics:
            self.stats_filename = f"TrisonicaStats_{timestamp}.csv"
            self.stats_path = os.path.join(self.config.log_dir, self.stats_filename)
            self.stats_file = open(self.stats_path, 'w', newline='')
            self.stats_file.write("timestamp,parameter,min,max,mean,std_dev,count\n")
            
        self.console.print(f"[LOG] Data Log: {self.log_filename}")
        if self.config.save_statistics:
            self.console.print(f"[LOG] Stats Log: {self.stats_filename}")
            
    def signal_handler(self, signum, frame):
        """Enhanced signal handler"""
        self.console.print(f"\n[SHUTDOWN] Received signal {signum}, saving data and shutting down...", style="bold yellow")
        self.save_final_statistics()
        self.running = False
        
    def connect_serial(self) -> bool:
        """Connect with enhanced error handling"""
        if self.config.serial_port == "auto":
            port = self.auto_detect_serial_port()
            if not port:
                return False
        else:
            port = self.config.serial_port
            
        try:
            self.serial_port = serial.Serial(port, self.config.baud_rate, timeout=1)
            self.console.print(f"[SUCCESS] Connected to {port} at {self.config.baud_rate:,} baud", style="bold green")
            return True
        except serial.SerialException as e:
            self.console.print(f"[ERROR] Connection failed: {e}", style="bold red")
            return False
            
    def parse_data_line(self, line: str) -> Dict[str, str]:
        """Enhanced parsing with better error handling"""
        parsed = {}
        try:
            # Handle multiple possible formats
            if ',' in line:
                pairs = line.strip().split(',')
                for pair in pairs:
                    pair = pair.strip()
                    if ' ' in pair:
                        parts = pair.split(' ', 1)
                        if len(parts) == 2:
                            key, value = parts
                            parsed[key.strip()] = value.strip()
            else:
                # Space-separated format
                parts = line.strip().split()
                for i in range(0, len(parts)-1, 2):
                    if i+1 < len(parts):
                        parsed[parts[i]] = parts[i+1]
                        
        except Exception as e:
            # Log parsing errors but continue
            pass
            
        return parsed
    
    def update_csv_columns(self, parsed_data: Dict[str, str]):
        """Update CSV columns based on new parameters found"""
        new_columns = False
        for key in parsed_data.keys():
            if key not in self.csv_columns:
                self.csv_columns.append(key)
                new_columns = True
        
        # Write headers if this is the first data or if new columns were added
        if not self.csv_headers_written:
            self.log_file.write(','.join(self.csv_columns) + '\n')
            self.csv_headers_written = True
            
    def write_csv_row(self, timestamp: datetime.datetime, parsed_data: Dict[str, str]):
        """Write a properly formatted CSV row"""
        row_values = []
        for column in self.csv_columns:
            if column == 'timestamp':
                row_values.append(timestamp.isoformat())
            else:
                # Get value for this column, or empty string if not present
                value = parsed_data.get(column, '')
                row_values.append(value)
        
        self.log_file.write(','.join(row_values) + '\n')
        self.log_file.flush()
        
    def calculate_statistics(self, key: str, value: float):
        """Enhanced statistics with standard deviation"""
        if key not in self.stats:
            self.stats[key] = Statistics()
            
        stat = self.stats[key]
        stat.current_val = value
        stat.count += 1
        stat.values.append(value)
        
        if stat.count == 1:
            stat.min_val = stat.max_val = stat.mean_val = value
            stat.std_dev = 0.0
        else:
            stat.min_val = min(stat.min_val, value)
            stat.max_val = max(stat.max_val, value)
            
            # Calculate mean
            stat.mean_val = sum(stat.values) / len(stat.values)
            
            # Calculate standard deviation
            if len(stat.values) > 1:
                variance = sum((x - stat.mean_val) ** 2 for x in stat.values) / len(stat.values)
                stat.std_dev = variance ** 0.5
                
    def read_serial_data(self) -> Optional[DataPoint]:
        """Enhanced data reading with performance metrics"""
        if not self.serial_port or not self.serial_port.is_open:
            return None
            
        try:
            line = self.serial_port.readline().decode('ascii', errors='ignore').strip()
            if not line:
                return None
                
            timestamp = datetime.datetime.now()
            parsed = self.parse_data_line(line)
            
            # Update CSV columns and write properly formatted row
            self.update_csv_columns(parsed)
            self.write_csv_row(timestamp, parsed)
            
            # Update statistics and track data quality
            self.data_quality['total_readings'] += 1
            for key, value_str in parsed.items():
                try:
                    value = float(value_str)

                    # Check for error values and update sensor health
                    is_error = value == -99.50
                    self.update_sensor_health(key, value, is_error)

                    if not is_error:
                        self.calculate_statistics(key, value)

                        # Update visualization data
                        if key in ['S', 'S2']:  # Wind speed
                            self.viz_data['wind_speed'].append(value)
                            # Track recent wind speeds for 300-measurement analysis
                            self.recent_wind_speeds.append(value)
                        elif key == 'T':  # Temperature
                            self.viz_data['temperature'].append(value)
                        elif key == 'D':  # Wind direction
                            self.viz_data['wind_direction'].append(value)
                            # Track recent wind directions for 1000-measurement analysis
                            self.recent_wind_directions.append(value)

                except ValueError:
                    self.update_sensor_health(key, 0, True)
                    
            # Update timestamps for visualization
            self.viz_data['timestamps'].append(timestamp)
                    
            # Calculate update rate
            now = time.time()
            if now - self.last_update > 0:
                self.update_rate = 1.0 / (now - self.last_update)
            self.last_update = now
            
            return DataPoint(timestamp, line, parsed)
            
        except Exception as e:
            return None
            
    def save_final_statistics(self):
        """Save final statistics summary"""
        if not self.config.save_statistics or not self.stats_file:
            return
            
        timestamp = datetime.datetime.now().isoformat()
        for key, stat in self.stats.items():
            self.stats_file.write(f"{timestamp},{key},{stat.min_val:.6f},{stat.max_val:.6f},"
                                f"{stat.mean_val:.6f},{stat.std_dev:.6f},{stat.count}\n")
        self.stats_file.flush()
    
    def create_sparkline(self, data: deque, title: str, direction_data: deque = None) -> Panel:
        """Create a sparkline visualization"""
        if len(data) < 2:
            return Panel(f"[dim]Collecting {title} data...[/dim]", title=title)

        try:
            # Convert to list and ensure we have numeric data
            values = [float(x) for x in data if x is not None]
            if not values:
                return Panel(f"[dim]No {title} data[/dim]", title=title)

            # Add current value and trend
            current = values[-1]

            # Create sparkline (simplified without Sparkline library)
            # sparkline = Sparkline(values, width=30)
            sparkline = "█" * min(30, int(current/max(values) * 30)) if values else ""

            # Add wind direction if provided
            direction_text = ""
            if direction_data and len(direction_data) > 0:
                try:
                    current_dir = float(direction_data[-1])
                    # Convert to compass direction
                    compass_points = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                                     'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
                    index = int((current_dir + 11.25) / 22.5) % 16
                    # Fixed width formatting - pad compass direction to 3 characters
                    direction_text = f" from {compass_points[index]:>3} ({current_dir:3.0f}°)"
                except:
                    pass

            content = f"{sparkline}\n[bold]{title}: {current:.2f}{direction_text}[/bold]"
            return Panel(content, title=title, border_style="bright_blue")

        except Exception as e:
            return Panel(f"[red]Error: {e}[/red]", title=title)
    
    def create_wind_compass(self, directions: deque) -> Panel:
        """Create ASCII wind compass"""
        if len(directions) < 1:
            return Panel("[dim]No wind direction data[/dim]", title="Wind Direction")
        
        try:
            current_dir = float(directions[-1])
            
            # Simple 8-point compass
            compass_points = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
            index = int((current_dir + 22.5) / 45) % 8
            direction = compass_points[index]
            
            # Create simple compass visualization
            compass = f"""
     N
   NW+NE
  W  +  E
   SW+SE
     S
     
Current: {current_dir:.0f}° ({direction})
"""
            return Panel(compass, title="Wind Direction", border_style="bright_green")
            
        except Exception as e:
            return Panel(f"[red]Error: {e}[/red]", title="Wind Direction")
    
    def create_trend_bars(self, data: deque, title: str, max_bars: int = 10) -> Panel:
        """Create trend bars visualization"""
        if len(data) < 2:
            return Panel(f"[dim]Collecting {title} data...[/dim]", title=title)
        
        try:
            # Get last few values
            values = [float(x) for x in list(data)[-max_bars:] if x is not None]
            if not values:
                return Panel(f"[dim]No {title} data[/dim]", title=title)
            
            # Normalize values to 0-1 range
            min_val, max_val = min(values), max(values)
            if max_val == min_val:
                normalized = [0.5] * len(values)
            else:
                normalized = [(v - min_val) / (max_val - min_val) for v in values]
            
            # Create vertical bars
            bars = []
            for i, norm_val in enumerate(normalized):
                bar_height = int(norm_val * 8)  # 8 levels
                bar = "█" * bar_height + "░" * (8 - bar_height)
                bars.append(f"{values[i]:.1f}\n{bar}")
            
            content = "\n".join(bars[-5:])  # Show last 5 bars
            return Panel(content, title=f"{title} Trend", border_style="bright_yellow")
            
        except Exception as e:
            return Panel(f"[red]Error: {e}[/red]", title=title)
        
    def create_layout(self) -> Layout:
        """Create enhanced layout for Linux"""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=4),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=4)
        )
        layout["main"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=1)
        )
        layout["left"].split_column(
            Layout(name="current_data", size=19),
            Layout(name="bottom_left")
        )
        layout["bottom_left"].split_row(
            Layout(name="wind_viz", ratio=1),
            Layout(name="alerts", ratio=1)
        )
        layout["right"].split_column(
            Layout(name="parameter_descriptions", size=18),
            Layout(name="spacer", size=1),
            Layout(name="raw_data", size=9)
        )
        return layout
        
    def get_parameter_info(self, key: str, value: str):
        """Get unit and quality info for a parameter"""
        try:
            val = float(value)
            if key.startswith('S'):
                unit = "m/s"
                quality = "Good" if 0 <= val <= 50 else "Check"
            elif key.startswith('T'):
                unit = "°C"
                quality = "Good" if -40 <= val <= 60 else "Check"
            elif key == 'D':
                unit = "°"
                quality = "Good" if 0 <= val <= 360 else "Check"
            elif key in ['U', 'V', 'W']:
                unit = "m/s"
                quality = "Good" if -50 <= val <= 50 else "Check"
            elif key == 'H':
                unit = "%"
                quality = "Good" if 0 <= val <= 100 else "Check"
            elif key == 'P':
                unit = "hPa"
                quality = "Good" if 900 <= val <= 1100 else "Check"
            elif key in ['PI', 'RO']:
                unit = "°"
                quality = "Good" if -45 <= val <= 45 else "Check"
            elif key in ['MD', 'TD']:
                unit = "°"
                quality = "Good" if 0 <= val <= 360 else "Check"
            else:
                unit = ""
                quality = "Unknown"
        except:
            unit = ""
            quality = "Invalid"
        return unit, quality

    def update_sensor_health(self, parameter: str, value: float, is_error: bool = False):
        """Update sensor health status"""
        current_time = datetime.datetime.now()

        if parameter not in self.data_quality['sensor_health']:
            self.data_quality['sensor_health'][parameter] = {
                'status': 'Unknown',
                'error_rate': 0.0,
                'last_good_reading': None
            }

        sensor = self.data_quality['sensor_health'][parameter]

        if is_error:
            self.data_quality['error_count'] += 1
            self.data_quality['last_error_time'] = current_time
            sensor['status'] = 'Error'
        else:
            sensor['last_good_reading'] = current_time
            if parameter.startswith('T') and value > 100000:
                sensor['status'] = 'Malfunction'
            elif parameter == 'P' and value == -99.70:
                sensor['status'] = 'Offline'
            else:
                sensor['status'] = 'Good'

        # Calculate error rate
        if self.data_quality['total_readings'] > 0:
            sensor['error_rate'] = (self.data_quality['error_count'] / self.data_quality['total_readings']) * 100

    def get_compass_direction(self, degrees: float) -> str:
        """Convert degrees to compass direction"""
        directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                     'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
        index = int((degrees + 11.25) / 22.5) % 16
        return directions[index]

    def calculate_mean_direction(self, directions: list) -> float:
        """Calculate mean wind direction (handling circular nature of degrees)"""
        import math
        if not directions:
            return 0.0

        # Convert to radians and calculate vector components
        sin_sum = sum(math.sin(math.radians(d)) for d in directions)
        cos_sum = sum(math.cos(math.radians(d)) for d in directions)

        # Calculate mean direction in radians, then convert to degrees
        mean_rad = math.atan2(sin_sum, cos_sum)
        mean_deg = math.degrees(mean_rad)

        # Ensure positive angle (0-360)
        if mean_deg < 0:
            mean_deg += 360

        return mean_deg

    def update_display(self, layout: Layout):
        """Enhanced display with more information"""
        # Header with system info
        elapsed = time.time() - self.start_time
        runtime = str(datetime.timedelta(seconds=int(elapsed)))

        header_table = Table.grid(expand=True)
        header_table.add_column(justify="left", ratio=1)
        header_table.add_column(justify="center", ratio=1)
        header_table.add_column(justify="right", ratio=1)

        # System metrics
        memory_usage = f"{sys.getsizeof(self.data_points) / 1024:.1f} KB"

        header_table.add_row(
            "Trisonica Linux Logger",
            f"Runtime: {runtime}",
            f"Points: {self.point_count:,}"
        )
        header_table.add_row(
            f"Update Rate: {self.update_rate:.1f} Hz",
            f"Memory: {memory_usage}",
            f"Log: {os.path.basename(self.log_path)}"
        )

        layout["header"].update(Panel(header_table, title="System Status", style="bold blue"))
        
        # Combined comprehensive data and statistics table
        if self.data_points and self.stats:
            latest = self.data_points[-1]

            # Comprehensive stats table with current values, units, quality, and statistics
            comprehensive_table = Table(box=box.ROUNDED)
            comprehensive_table.add_column("Parameter", style="cyan", width=10)
            comprehensive_table.add_column("Current", style="green", width=8)
            comprehensive_table.add_column("Unit", style="dim", width=6)
            comprehensive_table.add_column("Quality", style="yellow", width=8)
            comprehensive_table.add_column("Min", style="blue", width=8)
            comprehensive_table.add_column("Max", style="red", width=8)
            comprehensive_table.add_column("Mean", style="magenta", width=8)
            comprehensive_table.add_column("Count", style="white", width=7)

            # Combine current values with statistics
            for key in latest.parsed_data.keys():
                value = latest.parsed_data[key]
                unit, quality = self.get_parameter_info(key, value)

                # Get statistics if available
                if key in self.stats:
                    stat = self.stats[key]
                    comprehensive_table.add_row(
                        key,
                        f"{stat.current_val:.2f}",
                        unit,
                        quality,
                        f"{stat.min_val:.2f}",
                        f"{stat.max_val:.2f}",
                        f"{stat.mean_val:.2f}",
                        f"{stat.count:,}"
                    )
                else:
                    # Parameter exists but no statistics yet
                    comprehensive_table.add_row(
                        key,
                        value,
                        unit,
                        quality,
                        "—",
                        "—",
                        "—",
                        "0"
                    )

            layout["current_data"].update(Panel(comprehensive_table, title="Live Data & Statistics"))
        elif self.data_points:
            # Fallback to simple current data if no statistics yet
            latest = self.data_points[-1]
            data_table = Table(title="Current Measurements", box=box.ROUNDED)
            data_table.add_column("Parameter", style="cyan", width=12)
            data_table.add_column("Value", style="green", width=10)
            data_table.add_column("Unit", style="dim", width=8)
            data_table.add_column("Quality", style="yellow", width=10)

            for key, value in latest.parsed_data.items():
                unit, quality = self.get_parameter_info(key, value)
                data_table.add_row(key, value, unit, quality)

            layout["current_data"].update(Panel(data_table, title="Current Measurements"))
        else:
            layout["current_data"].update(Panel("Waiting for data...", title="Current Measurements"))
            
        # Raw data display (now on right side)
        if self.data_points and self.config.show_raw_data:
            raw_lines = []
            for dp in list(self.data_points)[-5:]:
                timestamp = dp.timestamp.strftime('%H:%M:%S.%f')[:-3]
                # Use full raw data without truncation
                raw_data = dp.raw_data
                raw_lines.append(f"{timestamp}: {raw_data}")

            raw_text = "\n".join(raw_lines)
            layout["raw_data"].update(Panel(raw_text, title="Raw Data Stream", border_style="bright_cyan"))
        else:
            if not self.data_points:
                layout["raw_data"].update(Panel("No data received yet", title="Raw Data Stream", border_style="bright_cyan"))
            else:
                layout["raw_data"].update(Panel("Raw data display disabled", title="Raw Data Stream", border_style="bright_cyan"))

        # Spacer for layout
        layout["spacer"].update("")

        # Wind statistics panel (recent 300 measurements)
        if len(self.viz_data['wind_speed']) > 0 and len(self.viz_data['wind_direction']) > 0:
            current_speed = self.viz_data['wind_speed'][-1]
            current_dir = self.viz_data['wind_direction'][-1]

            # Get compass direction
            compass_dir = self.get_compass_direction(current_dir)

            # Calculate recent 1000-measurement statistics
            if len(self.recent_wind_speeds) > 0 and len(self.recent_wind_directions) > 0:
                recent_speeds = list(self.recent_wind_speeds)
                recent_dirs = list(self.recent_wind_directions)

                recent_min = min(recent_speeds)
                recent_max = max(recent_speeds)
                recent_avg = sum(recent_speeds) / len(recent_speeds)
                recent_count = len(recent_speeds)

                # Calculate mean direction
                mean_dir = self.calculate_mean_direction(recent_dirs)
                mean_compass = self.get_compass_direction(mean_dir)

                # Calculate gust difference (max - avg)
                gust_diff = recent_max - recent_avg

                # Calculate direction variability (range of directions)
                dir_range = max(recent_dirs) - min(recent_dirs)
                # Handle wraparound case (e.g., 350° to 10° = 20° range, not 340°)
                if dir_range > 180:
                    dir_range = 360 - dir_range
            else:
                recent_min = recent_max = recent_avg = current_speed
                recent_count = 1
                mean_dir = current_dir
                mean_compass = compass_dir
                gust_diff = 0.0
                dir_range = 0.0

            wind_content = f"""Current: {current_speed:.2f} m/s {compass_dir}
Direction: {current_dir:.0f}°

Recent Trend (last {recent_count}):
Min: {recent_min:.2f} m/s
Max: {recent_max:.2f} m/s
Avg: {recent_avg:.2f} m/s
Mean Dir: {mean_dir:.0f}° ({mean_compass})
Gust: +{gust_diff:.2f} m/s
Dir Range: {dir_range:.0f}°"""

            layout["wind_viz"].update(Panel(wind_content, title="Wind Speed", border_style="bright_blue"))
        else:
            layout["wind_viz"].update(Panel("Collecting wind data...\n\nWaiting for:\n• Wind speed (S/S2)\n• Wind direction (D)", title="Wind Speed", border_style="dim"))

        # Data Quality Dashboard
        error_rate = (self.data_quality['error_count'] / max(1, self.data_quality['total_readings'])) * 100

        # Create compact sensor status display
        sensor_status_lines = []
        key_sensors = ['S', 'T', 'P', 'D', 'H']
        for sensor in key_sensors:
            if sensor in self.data_quality['sensor_health']:
                health = self.data_quality['sensor_health'][sensor]
                status = health['status']

                # Color code status
                if status == 'Good':
                    status_display = "[green]●[/green] Good"
                elif status == 'Error':
                    status_display = "[red]●[/red] Error"
                elif status == 'Malfunction':
                    status_display = "[red]●[/red] Broken"
                elif status == 'Offline':
                    status_display = "[yellow]●[/yellow] Offline"
                else:
                    status_display = "[dim]●[/dim] Unknown"

                sensor_status_lines.append(f"{sensor:>2}: {status_display}")

        # Combine sensor status and summary
        quality_content = "\n".join(sensor_status_lines)
        quality_content += f"""

Error Rate: {error_rate:.1f}%
Total: {self.data_quality['total_readings']:,} | Errors: {self.data_quality['error_count']}"""

        border_color = "bright_green" if error_rate < 1.0 else "bright_yellow" if error_rate < 5.0 else "bright_red"
        layout["alerts"].update(Panel(quality_content, title="Data Quality", border_style=border_color))

        # Parameter descriptions panel
        desc_table = Table(title="Parameter Reference", box=box.SIMPLE, show_header=False)
        desc_table.add_column("Code", style="cyan", width=3)
        desc_table.add_column("Description", style="white")

        desc_data = [
            ("S", "Total wind speed (m/s)"),
            ("S2", "Alt wind speed calc (m/s)"),
            ("D", "Wind direction (0-360°)"),
            ("U", "East-west component (m/s)"),
            ("V", "North-south component (m/s)"),
            ("W", "Vertical component (m/s)"),
            ("T", "Temperature (°C)"),
            ("H", "Humidity (%)"),
            ("P", "Pressure (hPa)"),
            ("PI", "Pitch angle (°)"),
            ("RO", "Roll angle (°)"),
            ("MD", "Magnetic heading (°)"),
            ("TD", "True heading (°)")
        ]

        for code, desc in desc_data:
            desc_table.add_row(code, desc)

        layout["parameter_descriptions"].update(Panel(desc_table, title="Parameters", border_style="bright_yellow"))
            
        # Footer
        footer_info = []
        footer_info.append(f"Data: {self.log_filename}")
        if self.config.save_statistics:
            footer_info.append(f"Stats: {self.stats_filename}")
        footer_info.append("Press Ctrl+C to exit")
        
        footer_text = " | ".join(footer_info)
        layout["footer"].update(Panel(Align.center(footer_text), style="dim"))
        
    def run(self):
        """Main execution with Rich interface"""
        if not self.connect_serial():
            return False
            
        layout = self.create_layout()
        
        try:
            with Live(layout, refresh_per_second=20, screen=True) as live:
                self.running = True
                while self.running:
                    data_point = self.read_serial_data()
                    if data_point:
                        self.point_count += 1
                        self.data_points.append(data_point)
                        
                        # Save statistics periodically
                        if self.point_count % 100 == 0:
                            self.save_final_statistics()
                            
                    self.update_display(layout)
                    time.sleep(UPDATE_INTERVAL)
                    
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()
            
        return True
        
    def cleanup(self):
        """Enhanced cleanup"""
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
            self.console.print("[CLEANUP] Serial port closed", style="green")
            
        if self.log_file and not self.log_file.closed:
            self.log_file.close()
            self.console.print(f"[CLEANUP] Data log saved: {self.log_path}", style="green")
            
        if self.stats_file and not self.stats_file.closed:
            self.stats_file.close()
            self.console.print(f"[CLEANUP] Statistics saved: {self.stats_path}", style="green")
            
        # Final summary
        if self.point_count > 0:
            elapsed = time.time() - self.start_time
            avg_rate = self.point_count / elapsed if elapsed > 0 else 0
            self.console.print(f"\n[SUMMARY] Session Summary:")
            self.console.print(f"   Total Points: {self.point_count:,}")
            self.console.print(f"   Runtime: {datetime.timedelta(seconds=int(elapsed))}")
            self.console.print(f"   Average Rate: {avg_rate:.1f} Hz")
            self.console.print(f"   Data Quality: {len(self.stats)} parameters tracked")
            
        self.console.print("[SUCCESS] Cleanup complete", style="bold green")

def main():
    parser = argparse.ArgumentParser(description='Trisonica Data Logger for Linux')
    parser.add_argument('--port', default='auto', help='Serial port (default: auto-detect)')
    parser.add_argument('--baud', type=int, default=DEFAULT_BAUD_RATE, help='Baud rate')
    parser.add_argument('--log-dir', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "OUTPUT"), help='Log directory')
    parser.add_argument('--show-raw', action='store_true', default=True, help='Show raw data stream')
    parser.add_argument('--hide-raw', action='store_true', help='Hide raw data stream')
    parser.add_argument('--no-stats', action='store_true', help='Disable statistics logging')
    
    args = parser.parse_args()
    
    config = Config(
        serial_port=args.port,
        baud_rate=args.baud,
        log_dir=args.log_dir,
        show_raw_data=args.show_raw and not args.hide_raw,
        save_statistics=not args.no_stats
    )
    
    logger = TrisonicaDataLoggerLinux(config)
    sys.exit(0 if logger.run() else 1)

if __name__ == '__main__':
    main()