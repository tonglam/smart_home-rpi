import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "smart_home.log")

logger = logging.getLogger("smart_home")
# Set to DEBUG to see all levels of logs for now, can be changed back to INFO later
logger.setLevel(logging.DEBUG)

# File Handler (existing)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
file_handler.setFormatter(formatter)

# Console Handler (new)
console_handler = logging.StreamHandler()  # Defaults to sys.stderr
console_handler.setFormatter(formatter)  # Use the same formatter
console_handler.setLevel(
    logging.DEBUG
)  # Also set this handler's level if needed, or it inherits from logger

if not logger.hasHandlers():
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)  # Add the new console handler
    logger.propagate = False  # Prevent duplicate logs if root logger also has handlers
elif not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
    # If logger was already configured (e.g. by another module) but without console
    logger.addHandler(console_handler)

# Test log to confirm setup
# logger.debug("Logger debug test")
# logger.info("Logger info test")
# logger.warning("Logger warning test")
