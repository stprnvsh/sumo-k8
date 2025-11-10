# Production Deployment Guide

## Cost Controls & Safety Features

This system includes multiple layers of cost protection to prevent runaway cloud bills:

### 1. **Resource Limits**
- **CPU/Memory Limits**: Set equal to requests (no overcommit)
- **Per-Pod Limits**: Enforced via LimitRange
- **Namespace Quotas**: Hard limits on total resources per tenant
- **Validation**: Resource requests validated against tenant limits before job creation

### 2. **Job Timeouts**
- **Hard Timeout**: `activeDeadlineSeconds` set to 24 hours (configurable)
- **Auto-cleanup**: Jobs auto-delete after 2 minutes of completion (`ttl_seconds_after_finished`)
- **No Retries**: `backoff_limit=0` prevents failed jobs from retrying indefinitely

### 3. **File Size Limits**
- **Max File Size**: 100MB default (configurable via `MAX_FILE_SIZE_MB`)
- **Validation**: Files checked before processing

### 4. **Concurrent Job Limits**
- **Per-Tenant Limit**: Enforced at database level
- **Configurable**: Set per tenant in database

### 5. **Automatic Cleanup**
- **ConfigMaps**: Auto-deleted after job completion (5 min delay)
- **Orphan Cleanup**: Background job cleans up orphaned ConfigMaps hourly
- **Failed Job Cleanup**: Failed jobs marked and cleaned up

### 6. **Database Protection**
- **Connection Pooling**: Prevents connection exhaustion
- **Indexes**: Optimized queries to prevent slow operations
- **Constraints**: Database-level validation prevents invalid data

## Environment Variables

```bash
# Required
DATABASE_URL=postgresql://user:password@host:5432/sumo_k8

# Optional - Cost Controls
MAX_FILE_SIZE_MB=100                    # Max upload size
MAX_JOB_DURATION_HOURS=24               # Max job runtime
CONFIGMAP_CLEANUP_DELAY_SECONDS=300     # Delay before cleanup
MAX_CONCURRENT_JOBS_PER_TENANT=10      # Global max (overrides tenant limit)

# Optional - Database
DB_POOL_MIN=2                           # Min connections
DB_POOL_MAX=10                          # Max connections

# Optional - Application
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO
CORS_ORIGINS=http://localhost:3000
```

## Kubernetes Deployment

### 1. **Resource Requests/Limits for Controller**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sumo-k8-controller
spec:
  replicas: 2  # High availability
  template:
    spec:
      containers:
      - name: app
        resources:
          requests:
            cpu: "100m"
            memory: "256Mi"
          limits:
            cpu: "500m"
            memory: "512Mi"
```

### 2. **ServiceAccount with RBAC**

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: sumo-k8-controller
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: sumo-k8-controller
rules:
- apiGroups: [""]
  resources: ["namespaces", "pods", "configmaps", "resourcequotas", "limitranges"]
  verbs: ["get", "list", "create", "update", "delete"]
- apiGroups: ["batch"]
  resources: ["jobs"]
  verbs: ["get", "list", "create", "update", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: sumo-k8-controller
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: sumo-k8-controller
subjects:
- kind: ServiceAccount
  name: sumo-k8-controller
  namespace: default
```

### 3. **Network Policies** (Optional but Recommended)

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sumo-k8-controller
spec:
  podSelector:
    matchLabels:
      app: sumo-k8-controller
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - namespaceSelector: {}
    ports:
    - protocol: TCP
      port: 8000
  egress:
  - {}  # Allow all egress (controller needs to reach K8s API)
```

## Monitoring & Alerts

### Recommended Metrics to Monitor

1. **Job Duration**: Alert if jobs exceed expected duration
2. **Failed Jobs Rate**: Alert if failure rate > 10%
3. **Resource Usage**: Alert if quota usage > 80%
4. **Database Connections**: Alert if pool exhausted
5. **ConfigMap Count**: Alert if cleanup not working

### Example Prometheus Queries

```promql
# Jobs running longer than expected
sumo_job_duration_seconds > 86400

# Failed jobs rate
rate(sumo_jobs_failed_total[5m]) / rate(sumo_jobs_total[5m]) > 0.1

# Resource quota usage
sumo_quota_used_cpu / sumo_quota_limit_cpu > 0.8
```

## Security Checklist

- [x] API key authentication
- [x] Input validation (file size, resource requests)
- [x] Database connection pooling
- [x] Non-root container user
- [x] Resource limits on controller
- [x] RBAC for Kubernetes access
- [x] CORS configuration
- [x] Health/readiness endpoints
- [ ] Rate limiting (consider adding)
- [ ] TLS/HTTPS (use ingress)
- [ ] Secret management (use K8s secrets for DB password)

## Cost Optimization Tips

1. **Set Conservative Defaults**: Lower `MAX_JOB_DURATION_HOURS` if jobs typically finish faster
2. **Monitor Quota Usage**: Regularly review tenant quotas
3. **Cleanup Old Jobs**: Consider archiving old job records
4. **Use Spot Instances**: For non-critical workloads (if using Karpenter)
5. **Right-Size Resources**: Review actual CPU/memory usage and adjust

## Disaster Recovery

### Database Backup
```bash
# Daily backup
pg_dump $DATABASE_URL > backup-$(date +%Y%m%d).sql
```

### Restore
```bash
psql $DATABASE_URL < backup-YYYYMMDD.sql
```

## Troubleshooting

### Jobs Stuck in PENDING
- Check ResourceQuota limits
- Check node capacity
- Check Karpenter/autoscaler status

### High ConfigMap Count
- Check cleanup job logs
- Manually clean: `kubectl delete configmap -l cleanup=true --all-namespaces`

### Database Connection Errors
- Check pool size (`DB_POOL_MAX`)
- Check database max connections
- Monitor connection count

### High Costs
- Review job durations
- Check for runaway jobs
- Review resource requests vs actual usage
- Consider reducing quotas

