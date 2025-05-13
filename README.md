# Smart Home RPi

A Raspberry Pi-based smart home monitoring system with sensors for sound, door (reed switch), and camera, featuring MQTT integration and cloud connectivity.

## Quick Start

1. **Clone the repository**
2. **Create a virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
4. **Configure environment**
   - Copy `.env.example` to `.env` and fill in your MQTT and Supabase credentials.
5. **Run the application**
   ```bash
   python src/main.py
   ```

## Deployment (as a Service)

- See [DEPLOYMENT.md](DEPLOYMENT.md) for running this app as a systemd service on Raspberry Pi OS.
- The service will auto-start on boot and auto-restart on crash.

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
