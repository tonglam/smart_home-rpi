# Use Python slim image as base (supports ARM)
FROM --platform=$TARGETPLATFORM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for GPIO and other packages
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libcap-dev \
    ffmpeg \
    libavcodec-dev \
    libavformat-dev \
    libavfilter-dev \
    libavdevice-dev \
    libswscale-dev \
    libswresample-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies with platform-specific handling
RUN if [ "$(uname -m)" = "armv7l" ] || [ "$(uname -m)" = "aarch64" ]; then \
    # ARM-specific installations
    pip install --no-cache-dir RPi.GPIO gpiozero && \
    CFLAGS="-I/usr/include/arm-linux-gnueabihf" pip install --no-cache-dir -r requirements.txt; \
    else \
    # Non-ARM installations
    pip install --no-cache-dir -r requirements.txt; \
    fi

# Copy the application code
COPY src/ ./src/
COPY start_smart_home.sh .

# Make the start script executable
RUN chmod +x start_smart_home.sh

# Create logs directory
RUN mkdir -p logs

# Copy .env file (will be overridden by mounted volume in production)
COPY .env.example .env

# Command to run the application
CMD ["./start_smart_home.sh"] 