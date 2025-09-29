#!/bin/bash

# Trisonica Data Logger - Linux Deployment Script

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration - Keep everything in linux directory
LINUX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$LINUX_DIR"
LOG_DIR="$LINUX_DIR/OUTPUT"
VENV_DIR="$INSTALL_DIR/venv"

echo -e "${BLUE}Trisonica Data Logger - Linux Deployment${NC}"
echo "=============================================="

# Function to print status
print_status() {
    echo -e "${GREEN}[OK] $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}[WARNING] $1${NC}"
}

print_error() {
    echo -e "${RED}[ERROR] $1${NC}"
}

# Check if running on Linux
check_linux() {
    if [[ "$OSTYPE" != "linux-gnu"* ]]; then
        print_warning "This script is designed for Linux, but continuing anyway..."
    fi
    print_status "Running on Linux/Unix-like system"
}

# Check Python installation
check_python() {
    if ! command -v python3 &> /dev/null; then
        print_error "Python 3 is not installed"
        echo "Please install Python 3:"
        echo "  Ubuntu/Debian: sudo apt install python3 python3-pip python3-venv"
        echo "  CentOS/RHEL:   sudo yum install python3 python3-pip"
        echo "  Fedora:        sudo dnf install python3 python3-pip"
        exit 1
    fi
    
    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
    print_status "Python $PYTHON_VERSION found"
}

# Check package manager and offer to install system packages
check_package_manager() {
    if command -v apt &> /dev/null; then
        print_status "APT package manager detected (Debian/Ubuntu)"
        echo "Would you like to install system packages for serial communication? (y/N)"
        read -r response
        if [[ "$response" =~ ^[Yy]$ ]]; then
            sudo apt update
            sudo apt install -y python3-serial python3-dev build-essential || print_warning "Failed to install system packages"
            # Add user to dialout group for serial access
            sudo usermod -a -G dialout $USER || print_warning "Could not add user to dialout group"
            print_status "System packages installed. Please log out and back in for group changes to take effect."
        fi
    elif command -v yum &> /dev/null; then
        print_status "YUM package manager detected (CentOS/RHEL)"
        echo "Would you like to install system packages for serial communication? (y/N)"
        read -r response
        if [[ "$response" =~ ^[Yy]$ ]]; then
            sudo yum install -y python3-serial python3-devel gcc || print_warning "Failed to install system packages"
            sudo usermod -a -G dialout $USER || print_warning "Could not add user to dialout group"
        fi
    elif command -v dnf &> /dev/null; then
        print_status "DNF package manager detected (Fedora)"
        echo "Would you like to install system packages for serial communication? (y/N)"
        read -r response
        if [[ "$response" =~ ^[Yy]$ ]]; then
            sudo dnf install -y python3-serial python3-devel gcc || print_warning "Failed to install system packages"
            sudo usermod -a -G dialout $USER || print_warning "Could not add user to dialout group"
        fi
    else
        print_warning "No recognized package manager found (optional)"
    fi
}

# Create directories
create_directories() {
    echo -e "${BLUE}Creating directories...${NC}"
    
    mkdir -p "$LOG_DIR"
    mkdir -p "$INSTALL_DIR/backups"
    mkdir -p "$INSTALL_DIR/PLOTS"
    
    print_status "Directories created"
}

# Create virtual environment
create_venv() {
    echo -e "${BLUE}Creating Python virtual environment...${NC}"
    
    if [ -d "$VENV_DIR" ]; then
        print_warning "Virtual environment already exists, recreating..."
        rm -rf "$VENV_DIR"
    fi
    
    python3 -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"
    
    # Upgrade pip
    pip install --upgrade pip
    
    print_status "Virtual environment created"
}

# Install Python dependencies
install_dependencies() {
    echo -e "${BLUE}Installing Python dependencies...${NC}"
    
    source "$VENV_DIR/bin/activate"
    
    # Install required packages
    pip install pyserial rich psutil
    
    # Optional packages for advanced features and visualization
    pip install matplotlib numpy pandas || print_warning "Optional visualization packages failed to install"
    pip install windrose || print_warning "Windrose package failed to install (optional for wind plots)"
    
    print_status "Dependencies installed"
}

# Copy application files
copy_files() {
    echo -e "${BLUE}Preparing application files...${NC}"
    
    # Files are already in the linux directory, just make executable
    chmod +x "$INSTALL_DIR/datalogger.py"
    
    print_status "Files prepared"
}

# Create launcher scripts
create_launchers() {
    echo -e "${BLUE}Creating launcher scripts...${NC}"
    
    # Main launcher
    cat > "$INSTALL_DIR/run_trisonica.sh" << EOF
#!/bin/bash
cd "$INSTALL_DIR"
source venv/bin/activate
python3 datalogger.py "\$@"
EOF
    
    # Quick start launcher
    cat > "$INSTALL_DIR/quick_start.sh" << EOF
#!/bin/bash
cd "$INSTALL_DIR"
source venv/bin/activate
echo "Starting Trisonica Logger with auto-detection..."
python3 datalogger.py --port auto
EOF
    
    # Log viewer
    cat > "$INSTALL_DIR/view_logs.sh" << EOF
#!/bin/bash
LOG_DIR="$LOG_DIR"
echo "Recent log files:"
ls -la "\$LOG_DIR"/*.csv 2>/dev/null | tail -10
echo
echo "Latest data (last 20 lines):"
tail -20 "\$LOG_DIR"/TrisonicaData_*.csv 2>/dev/null | head -20
EOF
    
    # System info for Linux
    cat > "$INSTALL_DIR/system_info.sh" << EOF
#!/bin/bash
echo "System Information:"
echo "OS: \$(lsb_release -d 2>/dev/null | cut -f2 || uname -a)"
echo "Kernel: \$(uname -r)"
echo "Architecture: \$(uname -m)"
echo "Memory: \$(free -h | grep '^Mem:' | awk '{print \$2}')"
echo "Python: \$(python3 --version)"
echo
echo "Disk Usage:"
df -h "$LOG_DIR" 2>/dev/null || echo "Log directory not found"
echo
echo "USB/Serial Devices:"
ls -la /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || echo "No USB serial devices found"
echo
echo "User Groups:"
groups \$USER
EOF
    
    # Serial permissions helper
    cat > "$INSTALL_DIR/fix_permissions.sh" << EOF
#!/bin/bash
echo "Fixing serial port permissions..."
echo "This will add your user to the dialout group and set permissions on serial devices."
echo "You may need to log out and back in for changes to take effect."
echo
sudo usermod -a -G dialout \$USER
sudo chmod 666 /dev/ttyUSB* 2>/dev/null || echo "No /dev/ttyUSB* devices found"
sudo chmod 666 /dev/ttyACM* 2>/dev/null || echo "No /dev/ttyACM* devices found"
echo "Done. Please log out and back in, or run: newgrp dialout"
EOF
    
    # Make scripts executable
    chmod +x "$INSTALL_DIR"/*.sh
    
    print_status "Launcher scripts created"
}

# Test installation
test_installation() {
    echo -e "${BLUE}Testing installation...${NC}"
    
    cd "$INSTALL_DIR"
    source venv/bin/activate
    
    # Test Python imports
    if python3 -c "import serial, rich, psutil; print('All imports successful')" 2>/dev/null; then
        print_status "Python dependencies test passed"
    else
        print_error "Python dependencies test failed"
        exit 1
    fi
    
    # Test script syntax
    if python3 -m py_compile datalogger.py; then
        print_status "Script syntax test passed"
    else
        print_error "Script syntax test failed"
        exit 1
    fi
    
    print_status "Installation test completed"
}

# Check serial permissions
check_serial_permissions() {
    echo -e "${BLUE}Checking serial port permissions...${NC}"
    
    if ls /dev/ttyUSB* /dev/ttyACM* >/dev/null 2>&1; then
        print_status "Serial devices found"
        if groups $USER | grep -q "dialout"; then
            print_status "User is in dialout group"
        else
            print_warning "User is not in dialout group"
            echo "Run ./fix_permissions.sh to fix this"
        fi
    else
        print_warning "No serial devices currently connected"
    fi
}

# Main installation function
main() {
    echo -e "${BLUE}Starting Linux installation...${NC}"
    
    check_linux
    check_python
    check_package_manager
    create_directories
    create_venv
    install_dependencies
    copy_files
    create_launchers
    test_installation
    check_serial_permissions
    
    echo
    echo -e "${GREEN}Installation complete!${NC}"
    echo
    echo "Usage:"
    echo "  Direct run:      ./run_trisonica.sh"
    echo "  Quick start:     ./quick_start.sh"
    echo "  View logs:       ./view_logs.sh"
    echo "  System info:     ./system_info.sh"
    echo "  Fix permissions: ./fix_permissions.sh"
    echo
    echo "Files:"
    echo "  Application:     $INSTALL_DIR/"
    echo "  Logs:           $LOG_DIR/"
    echo "  Virtual env:    $VENV_DIR/"
    echo
    echo "Command line options:"
    echo "  Auto-detect:     ./run_trisonica.sh"
    echo "  Specific port:   ./run_trisonica.sh --port /dev/ttyUSB0"
    echo "  Show raw data:   ./run_trisonica.sh --show-raw"
    echo "  Custom log dir:  ./run_trisonica.sh --log-dir /path/to/logs"
    echo
    echo -e "${YELLOW}Important for Linux:${NC}"
    echo "1. Make sure your user is in the 'dialout' group for serial access"
    echo "2. Run ./fix_permissions.sh if you have permission issues"
    echo "3. You may need to log out and back in after permission changes"
    echo
    echo "Ready to start logging your Trisonica data!"
    echo
    echo "Would you like to run a quick test? (y/N)"
    read -r response
    if [[ "$response" =~ ^[Yy]$ ]]; then
        echo -e "${BLUE}Running quick test...${NC}"
        ./run_trisonica.sh --help
    fi
}

# Run main installation
main "$@"