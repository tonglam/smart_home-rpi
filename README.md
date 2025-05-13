# Smart Home RPi

A Raspberry Pi-based smart home monitoring system with sensors for sound, motion, and door status.

## Project Structure

```
smart_home-rpi/
├── src/                    # Source code
│   ├── __init__.py
│   ├── sensors/           # Sensor-specific modules
│   │   ├── __init__.py
│   │   ├── camera.py
│   │   ├── reed.py
│   │   └── sound.py
│   ├── utils/            # Utility modules
│   │   ├── __init__.py
│   │   ├── cloudflare.py
│   │   ├── database.py
│   │   └── mqtt.py
│   └── main.py           # Application entry point
├── tests/                # Test files
│   ├── __init__.py
│   ├── test_database.py
│   ├── test_mqtt.py
│   └── test_r2_upload.py
├── .env.example         # Example environment variables
├── .gitignore          # Git ignore file
├── LICENSE             # License file
├── README.md           # Project documentation
└── requirements.txt    # Project dependencies
```

## Setup

1. Clone the repository
2. Create a virtual environment: `python -m venv .venv`
3. Activate the virtual environment:
   - Windows: `.venv\Scripts\activate`
   - Unix/MacOS: `source .venv/bin/activate`
4. Install dependencies: `pip install -r requirements.txt`
5. Copy `.env.example` to `.env` and fill in your configuration
6. Run the application: `python src/main.py`

## Testing

Run tests with pytest:

```bash
pytest tests/
```

## License

See [LICENSE](LICENSE) file for details.
