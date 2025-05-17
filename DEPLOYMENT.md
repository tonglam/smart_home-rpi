# Smart Home RPi Deployment Guide

This guide explains how to deploy the Smart Home RPi application using Docker on a fresh Raspberry Pi OS installation.

## Fresh Installation Steps (For New Raspberry Pi)

### 1. Initial Setup

First, ensure your Raspberry Pi is running and connected to the internet. Open a terminal and run:

```bash
# Update the system
sudo apt-get update
sudo apt-get upgrade -y

# Install required dependencies
sudo apt-get install -y curl git
```

### 2. Install Docker

Install Docker using the official installation script:

```bash
# Download and run Docker installation script
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add your user to the docker group
sudo usermod -aG docker $USER

# Important: Reboot for changes to take effect
sudo reboot
```

### 3. Install Docker Compose

After the reboot, install Docker Compose:

```bash
sudo apt-get update
sudo apt-get install -y docker-compose
```

### 4. Deploy Smart Home Application

Now set up the application:

```bash
# Create application directory
mkdir smart-home
cd smart-home

# Create required files and directories
mkdir logs
curl -O https://raw.githubusercontent.com/tonglam/smart_home-rpi/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/tonglam/smart_home-rpi/main/.env.example
mv .env.example .env

# Edit your environment configuration
nano .env

# Pull and start the container
docker-compose pull
docker-compose up -d
```

### 5. Verify Installation

Check if everything is running correctly:

```bash
# Check container status
docker-compose ps

# View logs
docker-compose logs
```

## Managing the Application

### View Logs

```bash
# View logs
docker-compose logs

# Follow logs
docker-compose logs -f
```

### Update the Application

```bash
# Pull the latest version
docker-compose pull

# Restart with the new version
docker-compose up -d
```

### Stop the Application

```bash
docker-compose down
```

### Check Status

```bash
docker-compose ps
```

## Troubleshooting

### GPIO Access Issues

If you encounter GPIO access issues, ensure that:

1. The container is running with `privileged: true`
2. The GPIO device is properly mounted
3. The user has the right permissions

### Camera Issues

If the camera is not working:

1. Ensure the camera is enabled in raspi-config: `sudo raspi-config`
2. Check that the camera module is properly connected
3. Verify the camera device is properly mounted in docker-compose.yml

### Network Issues

If you experience network connectivity issues:

1. Check your network configuration in .env
2. Verify that required ports are not blocked
3. Ensure Docker has network access

For more help, check the logs using `docker-compose logs` or file an issue on the GitHub repository.

## Security Best Practices

1. Keep your `.env` file secure and never commit it to version control
2. Regularly update your system: `sudo apt-get update && sudo apt-get upgrade`
3. Update Docker images regularly: `docker-compose pull`
4. Monitor system logs for any suspicious activity
5. Back up your configuration files regularly
6. Use strong passwords in your .env file
7. Keep your Raspberry Pi's operating system updated

## Monitoring and Maintenance

### Container Management

- View logs:

```bash
docker-compose logs -f
```

- Check container status:

```bash
docker-compose ps
```

- View resource usage:

```bash
docker stats
```
