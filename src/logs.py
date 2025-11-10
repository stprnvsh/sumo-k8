"""Log streaming and retrieval"""
import json
import time
import logging
from fastapi.responses import StreamingResponse
from kubernetes import client
from .k8s_client import k8s_available, k8s_core

logger = logging.getLogger(__name__)

def stream_job_logs(namespace: str, k8s_job_name: str):
    """Stream job logs in real-time using Server-Sent Events (SSE)"""
    if not k8s_available:
        def error_stream():
            yield f"data: {json.dumps({'error': 'Kubernetes not available'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")
    
    def log_stream():
        try:
            # Find the pod
            pods = k8s_core.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"job-name={k8s_job_name}"
            )
            
            if not pods.items:
                yield f"data: {json.dumps({'message': 'No pod found yet. Waiting...'})}\n\n"
                time.sleep(2)
                pods = k8s_core.list_namespaced_pod(
                    namespace=namespace,
                    label_selector=f"job-name={k8s_job_name}"
                )
                if not pods.items:
                    yield f"data: {json.dumps({'error': 'Pod not found'})}\n\n"
                    return
            
            pod_name = pods.items[0].metadata.name
            pod_phase = pods.items[0].status.phase
            
            yield f"data: {json.dumps({'pod': pod_name, 'phase': pod_phase, 'message': 'Starting log stream...'})}\n\n"
            
            last_line_count = 0
            consecutive_errors = 0
            max_errors = 10
            
            while True:
                try:
                    logs = k8s_core.read_namespaced_pod_log(
                        name=pod_name,
                        namespace=namespace,
                        tail_lines=1000
                    )
                    
                    lines = logs.split('\n')
                    new_lines = lines[last_line_count:]
                    
                    for line in new_lines:
                        if line.strip():
                            yield f"data: {json.dumps({'message': line})}\n\n"
                    
                    last_line_count = len(lines)
                    consecutive_errors = 0
                    
                    pods = k8s_core.list_namespaced_pod(
                        namespace=namespace,
                        label_selector=f"job-name={k8s_job_name}"
                    )
                    
                    if not pods.items:
                        yield f"data: {json.dumps({'message': 'Pod has terminated'})}\n\n"
                        break
                    
                    pod_phase = pods.items[0].status.phase
                    if pod_phase in ['Succeeded', 'Failed']:
                        final_logs = k8s_core.read_namespaced_pod_log(
                            name=pod_name,
                            namespace=namespace
                        )
                        final_lines = final_logs.split('\n')[last_line_count:]
                        for line in final_lines:
                            if line.strip():
                                yield f"data: {json.dumps({'message': line})}\n\n"
                        yield f"data: {json.dumps({'status': pod_phase, 'message': f'Job {pod_phase.lower()}'})}\n\n"
                        break
                    
                    time.sleep(1)
                    
                except client.exceptions.ApiException as e:
                    consecutive_errors += 1
                    if consecutive_errors >= max_errors:
                        yield f"data: {json.dumps({'error': f'Too many errors: {str(e)}'})}\n\n"
                        break
                    time.sleep(2)
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
                    break
                    
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
    return StreamingResponse(log_stream(), media_type="text/event-stream")

