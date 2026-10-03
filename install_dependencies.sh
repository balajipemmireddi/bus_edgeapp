#!/bin/bash
# Install all required dependencies for the Pi edge app

echo "=========================================="
echo "Installing Pi Edge App Dependencies"
echo "=========================================="
echo ""

# Update package lists
echo "[1/5] Updating package lists..."
sudo apt-get update

# Install system dependencies for face_recognition (dlib needs these)
echo "[2/5] Installing system dependencies for dlib..."
sudo apt-get install -y \
    build-essential \
    cmake \
    gfortran \
    git \
    wget \
    curl \
    libatlas-base-dev \
    libjasper-dev \
    libtiff5-dev \
    libjasper1 \
    libjasper-dev \
    libhdf5-dev \
    libharfbuzz0b \
    libwebp6 \
    libtiff5 \
    libjasper1 \
    libopenjp2-7 \
    libatlas3-base \
    libharfbuzz0b \
    libwebp6 \
    libtiff5 \
    libopenjp2-7

# Install Python dependencies
echo "[3/5] Installing Python dependencies..."
pip install --upgrade pip
pip install numpy==1.26.4
pip install opencv-python==4.10.0.84

# Install face_recognition from piwheels (pre-compiled for Pi)
echo "[4/5] Installing face_recognition from piwheels..."
pip install face-recognition --extra-index-url https://www.piwheels.org/simple

# Install zeroconf for mDNS hostname discovery (survives IP changes)
echo "[5/5] Installing zeroconf for auto IP discovery..."
pip install zeroconf

echo ""
echo "=========================================="
echo "Installation Complete!"
echo "=========================================="
echo ""
echo "Verify installation:"
python3 -c "import face_recognition; print('[OK] face_recognition installed')"
python3 -c "import cv2; print('[OK] opencv-python installed')"
python3 -c "import numpy; print('[OK] numpy installed')"
python3 -c "import zeroconf; print('[OK] zeroconf installed (mDNS discovery enabled)')"
echo ""
echo "Next steps:"
echo "1. Edit config.yaml with your Windows hostname and IP"
echo "2. Run: bash start_edge.sh"

