#!/bin/bash
# Network Configuration Diagnostic Script
# Run on Pi: bash test_network_config.sh
# Verifies that FACE_PROCESSOR_URL points to the correct Pi IP

echo "=========================================="
echo "  Bus Edge App - Network Configuration Test"
echo "=========================================="
echo ""

# Load .env if it exists
if [ -f ".env" ]; then
    source .env
    echo "✓ Loaded configuration from .env"
else
    echo "✗ .env not found - using defaults from .env.example"
    if [ -f ".env.example" ]; then
        source .env.example
    else
        echo "✗ .env.example not found either!"
        exit 1
    fi
fi

echo ""
echo "Current Configuration:"
echo "  BACKEND_URL: $BACKEND_URL"
echo "  FACE_PROCESSOR_URL: $FACE_PROCESSOR_URL"
echo "  BUS_ID: $BUS_ID"
echo ""

# Get this Pi's actual IP
echo "This Pi's IP addresses:"
hostname -I
THIS_PI_IP=$(hostname -I | awk '{print $1}')
echo "  Primary IP: $THIS_PI_IP"
echo ""

# Extract face processor IP from URL
EXPECTED_IP=$(echo "$FACE_PROCESSOR_URL" | grep -oP '(?<=http://)\d+\.\d+\.\d+\.\d+')
echo "FACE_PROCESSOR_URL points to: $EXPECTED_IP"
echo ""

# Verify it's correct
if [ "$THIS_PI_IP" = "$EXPECTED_IP" ]; then
    echo "✓ FACE_PROCESSOR_URL IP matches this Pi - CORRECT!"
else
    echo "✗ MISMATCH:"
    echo "  This Pi is: $THIS_PI_IP"
    echo "  Config points to: $EXPECTED_IP"
    echo ""
    echo "FIX: Update .env FACE_PROCESSOR_URL to http://$THIS_PI_IP:8095"
    exit 1
fi

echo ""
echo "=========================================="
echo "  Testing Connectivity"
echo "=========================================="
echo ""

# Test backend connectivity
echo "Testing Backend: $BACKEND_URL"
if curl -s "$BACKEND_URL/health" > /dev/null 2>&1; then
    echo "✓ Backend is reachable"
    curl -s "$BACKEND_URL/health" | python3 -m json.tool
else
    echo "✗ Cannot reach backend at $BACKEND_URL"
    echo "  Is backend running on Windows?"
    echo "  Is Windows IP correct? (run ipconfig on Windows)"
fi

echo ""

# Test face processor connectivity
echo "Testing Face Processor: $FACE_PROCESSOR_URL"
HEALTH=$(curl -s "$FACE_PROCESSOR_URL/health" 2>&1)
if echo "$HEALTH" | grep -q "face_processor"; then
    echo "✓ Face processor is running"
    echo "  Response: $HEALTH"
else
    echo "✗ Face processor not responding"
    echo "  Response: $HEALTH"
    echo "  Fix: Run 'bash start_edge.sh' to start face_processor"
fi

echo ""
echo "=========================================="
echo "  Summary"
echo "=========================================="
echo ""
echo "Network topology:"
echo "  Windows PC (backend): http://$BACKEND_URL"
echo "  This Pi (face processor): $FACE_PROCESSOR_URL"
echo ""
echo "If you see ✓ for both, everything is configured correctly!"
echo ""
