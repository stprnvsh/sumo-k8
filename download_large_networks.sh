#!/bin/bash
# Download larger SUMO test networks

set -e

cd test_networks/ready_to_use

echo "Downloading larger SUMO test networks..."

# LuST Scenario (Large Urban Scenario for Traffic) - Medium size
echo "1. Downloading LuST scenario (if available)..."
# Note: LuST is large, we'll try to get a smaller subset

# Try to get SUMO examples from official repo (smaller examples)
echo "2. Checking for SUMO example scenarios..."

# Download from SUMO examples if available
# These are typically in the SUMO repository under examples/

echo ""
echo "Available options:"
echo "  - LuST Scenario: Very large, realistic urban scenario"
echo "  - SUMO Examples: Official examples from repository"
echo "  - OpenStreetMap: Can generate from OSM data"
echo ""
echo "Note: Large networks may be several GB in size"
echo "      Consider downloading specific scenarios based on your needs"
