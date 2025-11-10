#!/bin/bash
set -e

echo "=== Creating Tenant ==="
TENANT_RESPONSE=$(curl -s -X POST "http://localhost:8000/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"tenant_id\":\"test-$(date +%s)\",\"max_cpu\":10,\"max_memory_gi\":20,\"max_concurrent_jobs\":2}")

echo "$TENANT_RESPONSE" | python3 -m json.tool

TENANT_ID=$(echo "$TENANT_RESPONSE" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['tenant_id'])")
API_KEY=$(echo "$TENANT_RESPONSE" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['api_key'])")

echo ""
echo "Tenant ID: $TENANT_ID"
echo "API Key: ${API_KEY:0:30}..."

echo ""
echo "=== Submitting Job ==="
JOB_RESPONSE=$(curl -s -X POST "http://localhost:8000/jobs" \
  -H "Authorization: Bearer $API_KEY" \
  -F "scenario_id=bologna" \
  -F "cpu_request=2" \
  -F "memory_gi=4" \
  -F "sumo_files=@test_networks/zips/bologna-acosta.zip")

echo "$JOB_RESPONSE" | python3 -m json.tool

if echo "$JOB_RESPONSE" | python3 -c "import sys,json;d=json.load(sys.stdin);exit(0 if 'job_id' in d else 1)" 2>/dev/null; then
  JOB_ID=$(echo "$JOB_RESPONSE" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['job_id'])")
  echo ""
  echo "Job ID: $JOB_ID"
  
  echo ""
  echo "=== Monitoring Job ==="
  for i in {1..12}; do
    STATUS=$(curl -s "http://localhost:8000/jobs/$JOB_ID" -H "Authorization: Bearer $API_KEY" 2>/dev/null | \
      python3 -c "import sys,json;d=json.load(sys.stdin);print(d['status'])" 2>/dev/null)
    echo "[$i] Status: $STATUS"
    if [ "$STATUS" = "SUCCEEDED" ] || [ "$STATUS" = "FAILED" ]; then
      echo "Job completed!"
      break
    fi
    sleep 5
  done
  
  echo ""
  echo "=== Waiting for Reconciler (60 seconds) ==="
  sleep 60
  
  echo ""
  echo "=== Testing Results Endpoint ==="
  curl -s "http://localhost:8000/jobs/$JOB_ID/results" -H "Authorization: Bearer $API_KEY" | python3 -m json.tool
else
  echo ""
  echo "ERROR: Failed to submit job"
  echo "Response: $JOB_RESPONSE"
  exit 1
fi
