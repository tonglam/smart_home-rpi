# Smart Home RPi Deployment Guide

## 1. Check Your System

```bash
# Check CPU architecture and userspace
uname -m          # Shows CPU architecture
getconf LONG_BIT  # Shows userspace bit width

# Important: We use 32-bit (armv7) for best compatibility
# Even if your system shows aarch64, we'll use armv7
```

**Always use armv7 (32-bit) version:**

- If `armv7l` → Use `latest-armv7` ✓
- If `aarch64` → Still use `latest-armv7` ✓ (for better GPIO compatibility)

## 2. Install Dependencies

```bash
# System packages
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    python3-full \
    python3-numpy \
    python3-pil \
    libcap-dev \
    ffmpeg \
    libjpeg-dev \
    zlib1g-dev \
    libtiff-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libwebp-dev \
    libjpeg62-turbo-dev \
    libopenjp2-7-dev

# Camera dependencies

# Clean up
sudo apt-get autoremove -y
sudo apt-get clean

# Update system
sudo apt-get update
sudo apt-get upgrade -y
sudo apt-get dist-upgrade -y

# Install camera stack
sudo apt-get install -y \
    libcamera0 \
    libcamera-apps-lite \
    python3-libcamera \
    python3-picamera2 \
    libcamera-tools \
    python3-kms++ \
    python3-prctl

# Enable camera interface (Method 1 - Using raspi-config)
sudo raspi-config  # Navigate to Interface Options > Camera > Enable

# Enable camera interface (Method 2 - Non-interactive)
sudo sed -i 's/^camera_auto_detect=.*/camera_auto_detect=1/' /boot/config.txt
sudo sed -i 's/^dtoverlay=vc4-kms-v3d.*/dtoverlay=vc4-kms-v3d/' /boot/config.txt

# Important: Reboot after installation
sudo reboot

# Check camera interface (modern method)
ls -l /dev/video*  # Should show video devices
libcamera-hello --list-cameras  # List available cameras

# Setup Python virtual environment with system packages
python3 -m venv ~/smart_home_env --system-site-packages
source ~/smart_home_env/bin/activate

# Install Python packages in virtual environment
pip3 install --no-cache-dir RPi.GPIO gpiozero
```

## 3. Deploy Application

```bash
# Setup
cd ~
git clone https://github.com/tonglam/smart_home-rpi.git smart-home
cd smart-home

# Activate virtual environment and install requirements
source ~/smart_home_env/bin/activate
pip3 install -r requirements.txt

# Configure
cp .env.example .env
nano .env  # Edit configuration

# Create systemd service
sudo tee /etc/systemd/system/smart-home.service << EOF
[Unit]
Description=Smart Home Service
After=network.target

[Service]
Type=simple
User=csseiot
Group=csseiot
WorkingDirectory=/home/csseiot/smart-home
Environment=PATH=/home/csseiot/smart_home_env/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/home/csseiot/smart_home_env/bin/python src/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Set proper permissions
sudo chmod 644 /etc/systemd/system/smart-home.service

# Enable and start the service
sudo systemctl daemon-reload
sudo systemctl enable smart-home
sudo systemctl start smart-home
```

## 4. Common Commands

Service Management:

```bash
sudo systemctl start smart-home    # Start service
sudo systemctl stop smart-home     # Stop service
sudo systemctl restart smart-home  # Restart service
sudo systemctl status smart-home   # Check status
```

Logs:

```bash
# View service logs
sudo journalctl -u smart-home -f

# View application logs
tail -f ~/smart-home/logs/smart_home.log
```

Update:

```bash
# Stop service
sudo systemctl stop smart-home

# Update code
cd ~/smart-home
git pull

# Update dependencies
source ~/smart_home_env/bin/activate
pip3 install -r requirements.txt

# Start service
sudo systemctl start smart-home
```

## Environment Variables Configuration

When copying `.env.example` to `.env`, configure these variables:

```bash
# Application Settings
APP_NAME=smart_home
LOG_LEVEL=INFO
TIMEZONE=UTC

# MQTT Configuration
MQTT_BROKER=localhost
MQTT_PORT=1883
MQTT_USERNAME=
MQTT_PASSWORD=
MQTT_TOPIC_PREFIX=home

# AWS Configuration (if using)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-west-2

# Supabase Configuration
SUPABASE_URL=
SUPABASE_KEY=

# Sensor Configuration
SENSOR_READ_INTERVAL=60  # seconds
ENABLE_TEMPERATURE=true
ENABLE_HUMIDITY=true
ENABLE_MOTION=true
ENABLE_CAMERA=false

# GPIO Pin Assignments
TEMP_SENSOR_PIN=4
HUMIDITY_SENSOR_PIN=17
MOTION_SENSOR_PIN=18

# Camera Settings (if enabled)
CAMERA_RESOLUTION=1920x1080
CAMERA_ROTATION=0
CAMERA_FRAMERATE=30
```

## Troubleshooting

GPIO Issues:

```bash
# Check GPIO permissions
ls -l /dev/gpiomem
sudo usermod -a -G gpio $USER
groups  # Should show 'gpio'

# Test GPIO access
python3 -c "import RPi.GPIO as GPIO; print('GPIO available')"
```

Service Issues:

```bash
# Check service status
sudo systemctl status smart-home

# Check detailed logs
sudo journalctl -u smart-home -n 100 --no-pager

# Restart service
sudo systemctl restart smart-home

# Check service configuration
sudo systemctl cat smart-home
```

Python Environment Issues:

```bash
# Recreate virtual environment
rm -rf ~/smart_home_env
python3 -m venv ~/smart_home_env --system-site-packages
source ~/smart_home_env/bin/activate
pip3 install -r requirements.txt
```

Camera Issues:

```bash
# 1. Check camera hardware detection
ls -l /dev/video*  # Should show video devices like /dev/video0
libcamera-hello --list-cameras  # List detected cameras

# 2. Test camera with libcamera tools
libcamera-hello  # Should show camera preview
libcamera-jpeg -o test.jpg  # Try to capture an image

# 3. Check camera permissions
ls -l /dev/video*  # Check video device permissions
sudo usermod -a -G video $USER  # Add user to video group
groups  # Verify user is in video group

# 4. Check camera configuration
cat /boot/config.txt | grep -E "camera|dtoverlay"
# Should show:
# camera_auto_detect=1
# dtoverlay=vc4-kms-v3d

# 5. If camera is not detected, try:
sudo nano /boot/config.txt
# Add or modify these lines:
camera_auto_detect=1
dtoverlay=vc4-kms-v3d

# 6. Test with Python
python3 -c "from picamera2 import Picamera2; Picamera2.global_camera_info()"

# 7. Reinstall camera packages if needed
sudo apt-get update
sudo apt-get install --reinstall \
    python3-libcamera \
    python3-picamera2 \
    libcamera-tools \
    libcamera-dev

# Note: After any config.txt changes or group modifications
sudo reboot
```

Note: Modern Raspberry Pi OS uses libcamera instead of the legacy camera stack. The `vcgencmd` command is no longer used for camera detection. Use `libcamera-hello` and related tools instead.

## Service Setup

```bash
# 1. Create logs directory
mkdir -p ~/smart-home/logs

# 2. Create service file
sudo tee /etc/systemd/system/smart-home.service << 'EOF'
[Unit]
Description=Smart Home RPi Service
After=network.target
Wants=network-online.target

[Service]
# Service will run as the specified user
User=adamslin
Group=adamslin

# Application directory
WorkingDirectory=/home/adamslin/smart-home

# Python environment and application
Environment="PATH=/home/adamslin/smart_home_env/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYTHONUNBUFFERED=1"

# Start the application
ExecStart=/home/adamslin/smart_home_env/bin/python src/main.py

# Restart configuration
Restart=always
RestartSec=5

# Give the service time to start
TimeoutStartSec=30

# Use a dedicated process group
KillMode=process

# Logging
StandardOutput=append:/home/adamslin/smart-home/logs/smart-home.log
StandardError=append:/home/adamslin/smart-home/logs/smart-home.error.log

[Install]
WantedBy=multi-user.target
EOF

# 3. Set proper permissions
sudo chmod 644 /etc/systemd/system/smart-home.service

# 4. Reload systemd and enable service
sudo systemctl daemon-reload
sudo systemctl enable smart-home
sudo systemctl start smart-home

# 5. Check service status
sudo systemctl status smart-home
```

Note: Replace `adamslin` in the service file with your actual username. You can find your username by running `echo $USER`.

## Git Setup and Updates

### Initial Git Setup

```bash
# Install latest Git (recommended)
sudo apt-get update
sudo apt-get install -y git

# Configure Git
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"

# Optional: Store credentials (be careful with this on shared systems)
git config --global credential.helper store
```

### Updating Code

```bash
# 1. Stop the service
sudo systemctl stop smart-home

# 2. Backup local changes (if any)
cd ~/smart-home
git status  # Check for local changes
git stash   # Backup local changes if needed

# 3. Update from repository
git fetch origin
git reset --hard origin/main  # or origin/master, depending on your branch

# 4. Update dependencies
source ~/smart_home_env/bin/activate
pip install -r requirements.txt

# 5. Restart service
sudo systemctl start smart-home

# 6. Verify update
sudo systemctl status smart-home
tail -f ~/smart-home/logs/smart-home.log
```

### Common Git Issues

```bash
# If Git asks for credentials repeatedly
git config --global credential.helper store

# If you need to reset local changes
git fetch origin
git reset --hard origin/main

# If you need to clean untracked files
git clean -fd  # Warning: This deletes untracked files!

# If you need to update Git itself
sudo apt-get update
sudo apt-get install --only-upgrade git
```
