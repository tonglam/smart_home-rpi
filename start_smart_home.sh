#!/bin/bash

# Exit on error
set -e

# Change to script directory
cd "$(dirname "$0")"

# Check if running on Raspberry Pi
if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
    echo "Warning: This script is designed for Raspberry Pi"
fi

# Check for required system packages
for pkg in python3-pip python3-venv python3-numpy; do
    if ! dpkg -l | grep -q "^ii  $pkg "; then
        echo "Error: Required package $pkg is not installed"
        echo "Please run: sudo apt-get install -y $pkg"
        exit 1
    fi
done

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv --system-site-packages
fi

# Activate virtual environment
source .venv/bin/activate

# Update pip and install requirements
echo "Updating pip and installing requirements..."
pip install --upgrade pip
pip install -r requirements.txt

# Check if .env exists
if [ ! -f ".env" ]; then
    echo "Error: .env file not found"
    echo "Please copy .env.example to .env and configure it"
    exit 1
fi

# Check GPIO access
if [ ! -r "/dev/gpiomem" ]; then
    echo "Error: Cannot access GPIO. Please check permissions"
    echo "Run: sudo usermod -a -G gpio $USER"
    exit 1
fi

# Start the application
echo "Starting Smart Home application..."
exec python src/main.py 