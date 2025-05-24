# Smart Home RPi

A Raspberry Pi-based smart home monitoring system with sensors for sound, door (reed switch), and camera, featuring MQTT integration and cloud connectivity.

## Get Started

1. **Clone the repository**
   ```bash
   git clone https://github.com/tonglam/smart_home-rpi.git
   cd smart_home-rpi
   ```
2. **Set up Python environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Configure environment**

   - Copy `.env.example` to `.env`.
   - Open `.env` and set the required environment variables according to your setup. Required credentials include:

     **# db (Supabase)**

     - `SUPABASE_URL`
     - `SUPABASE_KEY`

     **# mqtt**

     - `MQTT_BROKER_URL`
     - `MQTT_USERNAME`
     - `MQTT_PASSWORD`

     **# r2 (Cloudflare R2)**

     - `CLOUDFLARE_ACCOUNT_ID`
     - `R2_ACCESS_KEY_ID`
     - `R2_SECRET_ACCESS_KEY`

4. **(Optional) Configure Raspberry Pi WiFi auto-connect**
   4.1. Edit the WiFi config file:

   ```bash
   sudo nano /etc/wpa_supplicant/wpa_supplicant.conf
   ```

   4.2. Add or update the following block (replace with your WiFi details):

   ```bash
   ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
   update_config=1
   country=AU
   network={
       ssid="YourNetworkName"
       psk="YourWiFiPassword"
       scan_ssid=1
   }
   ```

5. **Run the application**
   ```bash
   python src/main.py
   ```

## Deployment (as a Service)

- For auto-start and crash recovery on Raspberry Pi OS, see [DEPLOYMENT.md](DEPLOYMENT.md) to set up as a systemd service.

## Testing

Run all tests with:

```bash
pytest tests/
```

## Project Structure

- `src/` — Main source code (sensors, utils, main entry)
- `tests/` — Test suite
- `.env.example` — Example environment config
- `requirements.txt` — Python dependencies

## License

See [LICENSE](LICENSE) for details.
