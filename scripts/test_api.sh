#!/bin/bash
# Test script for new API endpoints

API_URL="http://localhost:8000"

echo "=== Testing Account Creation ==="
curl -X POST $API_URL/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test-city",
    "max_cpu": 5,
    "max_memory_gi": 10,
    "max_concurrent_jobs": 1
  }' | python3 -m json.tool

echo -e "\n=== Testing List Tenants ==="
curl -s $API_URL/auth/tenants | python3 -m json.tool

echo -e "\n=== Testing Health Check ==="
curl -s $API_URL/health | python3 -m json.tool
