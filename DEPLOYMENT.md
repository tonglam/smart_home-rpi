# Deployment Guide: Smart Home RPi as a Systemd Service

## 1. Prerequisites

- Raspberry Pi OS (or compatible Linux)
- Python 3.9+
- Project cloned to `/home/pi/smart_home-rpi` (or your chosen path)

## 2. Setup

```bash
cd /home/pi/smart_home-rpi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Edit .env with your credentials
chmod +x start_smart_home.sh
```

## 3. Enable and Start the Service

```bash
sudo cp smart-home.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable smart-home.service   # Start on boot
sudo systemctl start smart-home.service
```

## 4. Monitoring & Logs

```bash
sudo systemctl status smart-home.service
journalctl -u smart-home.service -f
```

## 5. Updating

```bash
sudo systemctl restart smart-home.service
```

## 6. Reliability

- The service will **auto-restart on crash** (`Restart=on-failure` in the service file).
- The service will **auto-start on boot** (`enable` step above).

---

**Your app will keep running and recover from crashes or reboots automatically!**
