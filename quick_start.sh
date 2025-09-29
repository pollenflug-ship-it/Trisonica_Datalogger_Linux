#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
source venv/bin/activate
echo "Starting Trisonica Logger with auto-detection..."
python3 datalogger.py --port auto