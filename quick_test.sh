#!/bin/bash
API_URL="http://localhost:8000"
TENANT_ID="test-$(date +%s)"

# Create tenant
echo "Creating tenant..."
TENANT=$(curl -s -X POST "$API_URL/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"tenant_id\": \"$TENANT_ID\", \"max_cpu\": 10, \"max_memory_gi\": 20, \"max_concurrent_jobs\": 2}")

API_KEY=$(echo "$TENANT" | python3 -c "import sys, json; print(json.load(sys.stdin)['api_key'])" 2>/dev/null)
echo "API Key: $API_KEY"
echo ""

# Submit job
echo "Submitting job..."
JOB=$(curl -s -X POST "$API_URL/jobs" \
  -H "Authorization: Bearer $API_KEY" \
  -F "scenario_id=test" \
  -F "cpu_request=1" \
  -F "memory_gi=2" \
  -F "sumo_files=@test_networks/ready_to_use/emission.zip")

JOB_ID=$(echo "$JOB" | python3 -c "import sys, json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null)
echo "Job ID: $JOB_ID"
echo ""

# Wait and check status
echo "Waiting 5 seconds..."
sleep 5

echo "Job status:"
curl -s "$API_URL/jobs/$JOB_ID" -H "Authorization: Bearer $API_KEY" | python3 -m json.tool

echo ""
echo "Pod logs:"
kubectl logs -n "$TENANT_ID" -l job-name=sim-${JOB_ID:0:8} --tail=50 2>/dev/null || echo "Pod not found or no logs"
