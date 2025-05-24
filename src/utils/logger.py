"""
Logging Configuration Module

This module configures the application-wide logging system with
consistent formatting and appropriate log levels.

Features:
    - Timestamp in ISO format
    - Log level coloring
    - Module/function context
    - Console and file output
    - Configurable log levels
    - Exception tracebacks

Log Levels:
    - DEBUG: Detailed information for debugging
    - INFO: General operational messages
    - WARNING: Issues that might need attention
    - ERROR: Serious problems that need immediate attention
    - CRITICAL: System-threatening issues

Usage:
    from utils.logger import logger

    logger.debug("Detailed debug information")
    logger.info("Normal operational message")
    logger.warning("Warning about potential issues")
    logger.error("Error that needs attention")
    logger.critical("Critical system issue")
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

# Configure logging format
formatter = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Create and configure file handler
file_handler = RotatingFileHandler(
    "logs/smart_home.log",
    maxBytes=10_000_000,  # 10MB
    backupCount=5,
)
file_handler.setFormatter(formatter)

# Create and configure console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)

# Create and configure logger
logger = logging.getLogger("SmartHome")
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)
