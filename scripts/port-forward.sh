#!/bin/bash
# Stable port-forward script that auto-restarts on failure

SERVICE_NAME="sumo-k8-controller"
NAMESPACE="sumo-k8"
LOCAL_PORT=8000
REMOTE_PORT=80

echo "Starting port-forward for $SERVICE_NAME..."
echo "Access API at: http://localhost:$LOCAL_PORT"
echo "Press Ctrl+C to stop"
echo ""

while true; do
    kubectl port-forward -n $NAMESPACE svc/$SERVICE_NAME $LOCAL_PORT:$REMOTE_PORT
    EXIT_CODE=$?
    
    if [ $EXIT_CODE -eq 0 ]; then
        echo "Port-forward stopped normally"
        break
    else
        echo "Port-forward died (exit code: $EXIT_CODE), restarting in 2 seconds..."
        sleep 2
    fi
done

