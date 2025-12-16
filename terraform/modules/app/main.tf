provider "kubernetes" {
  host                   = var.cluster_endpoint
  cluster_ca_certificate = base64decode(var.cluster_ca)
  
  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args = [
      "eks",
      "get-token",
      "--cluster-name",
      var.cluster_name
    ]
  }
}

# Namespace
resource "kubernetes_namespace" "sumo_k8" {
  metadata {
    name = "sumo-k8"
  }
}

# Service Account
resource "kubernetes_service_account" "controller" {
  metadata {
    name      = "sumo-k8-controller"
    namespace = kubernetes_namespace.sumo_k8.metadata[0].name
  }
}

# RBAC
resource "kubernetes_cluster_role" "controller" {
  metadata {
    name = "sumo-k8-controller"
  }
  
  rule {
    api_groups = [""]
    resources  = ["namespaces", "pods", "configmaps", "persistentvolumeclaims"]
    verbs      = ["get", "list", "watch", "create", "update", "patch", "delete"]
  }
  
  rule {
    api_groups = ["batch"]
    resources  = ["jobs"]
    verbs      = ["get", "list", "watch", "create", "update", "patch", "delete"]
  }
}

resource "kubernetes_cluster_role_binding" "controller" {
  metadata {
    name = "sumo-k8-controller"
  }
  
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind     = "ClusterRole"
    name     = kubernetes_cluster_role.controller.metadata[0].name
  }
  
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.controller.metadata[0].name
    namespace = kubernetes_namespace.sumo_k8.metadata[0].name
  }
}

# StorageClass
resource "kubernetes_storage_class" "ebs_gp3" {
  metadata {
    name = "ebs-gp3"
    annotations = {
      "storageclass.kubernetes.io/is-default-class" = "true"
    }
  }
  
  provisioner       = "ebs.csi.aws.com"
  volume_binding_mode = "WaitForFirstConsumer"
  reclaim_policy   = "Delete"
  allow_volume_expansion = true
  
  parameters = {
    type  = "gp3"
    fsType = "ext4"
    encrypted = "true"
  }
}

# ConfigMap
resource "kubernetes_config_map" "config" {
  metadata {
    name      = "sumo-k8-config"
    namespace = kubernetes_namespace.sumo_k8.metadata[0].name
  }
  
  data = {
    LOG_LEVEL                        = "INFO"
    MAX_FILE_SIZE_MB                 = "100"
    MAX_JOB_DURATION_HOURS           = "24"
    MAX_CONCURRENT_JOBS_PER_TENANT   = "10"
    CONFIGMAP_CLEANUP_DELAY_SECONDS  = "300"
    DB_POOL_MIN                      = "2"
    DB_POOL_MAX                      = "10"
    CORS_ORIGINS                     = "*"
    RESULT_STORAGE_TYPE              = "auto"
    RESULT_STORAGE_SIZE_GI           = "10"
    S3_BUCKET                        = var.s3_bucket
    S3_REGION                        = var.s3_region
  }
}

# Secret
resource "kubernetes_secret" "secrets" {
  metadata {
    name      = "sumo-k8-secrets"
    namespace = kubernetes_namespace.sumo_k8.metadata[0].name
  }
  
  data = {
    DATABASE_URL = base64encode(var.database_url != null ? var.database_url : "postgresql://postgres:postgres@postgres-service:5432/sumo_k8")
    POSTGRES_PASSWORD = base64encode("postgres")
    ADMIN_KEY = base64encode(var.admin_key)
  }
  
  type = "Opaque"
}

# Deployment
resource "kubernetes_deployment" "controller" {
  metadata {
    name      = "sumo-k8-controller"
    namespace = kubernetes_namespace.sumo_k8.metadata[0].name
  }
  
  spec {
    replicas = 2
    
    selector {
      match_labels = {
        app = "sumo-k8-controller"
      }
    }
    
    template {
      metadata {
        labels = {
          app = "sumo-k8-controller"
        }
      }
      
      spec {
        service_account_name = kubernetes_service_account.controller.metadata[0].name
        
        node_selector = {
          "node-type" = "infrastructure"
        }
        
        container {
          name  = "app"
          image = "${var.image_repository}:${var.image_tag}"
          image_pull_policy = "Always"
          
          port {
            container_port = 8000
            name          = "http"
          }
          
          env {
            name = "DATABASE_URL"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.secrets.metadata[0].name
                key  = "DATABASE_URL"
              }
            }
          }
          
          env {
            name = "ADMIN_KEY"
            value_from {
              secret_key_ref {
                name     = kubernetes_secret.secrets.metadata[0].name
                key      = "ADMIN_KEY"
                optional = true
              }
            }
          }
          
          env_from {
            config_map_ref {
              name = kubernetes_config_map.config.metadata[0].name
            }
          }
          
          resources {
            requests = {
              cpu    = "100m"
              memory = "256Mi"
            }
            limits = {
              cpu    = "500m"
              memory = "512Mi"
            }
          }
          
          liveness_probe {
            http_get {
              path = "/health"
              port = 8000
            }
            initial_delay_seconds = 30
            period_seconds        = 30
          }
          
          readiness_probe {
            http_get {
              path = "/ready"
              port = 8000
            }
            initial_delay_seconds = 10
            period_seconds       = 10
          }
        }
      }
    }
  }
}

# Service
resource "kubernetes_service" "controller" {
  metadata {
    name      = "sumo-k8-controller"
    namespace = kubernetes_namespace.sumo_k8.metadata[0].name
  }
  
  spec {
    selector = {
      app = "sumo-k8-controller"
    }
    
    port {
      port        = 80
      target_port = 8000
    }
    
    type = "LoadBalancer"
  }
}

# PostgreSQL (if database_url is null)
resource "kubernetes_deployment" "postgres" {
  count = var.database_url == null ? 1 : 0
  
  metadata {
    name      = "postgres"
    namespace = kubernetes_namespace.sumo_k8.metadata[0].name
  }
  
  spec {
    replicas = 1
    
    selector {
      match_labels = {
        app = "postgres"
      }
    }
    
    template {
      metadata {
        labels = {
          app = "postgres"
        }
      }
      
      spec {
        node_selector = {
          "node-type" = "infrastructure"
        }
        
        container {
          name  = "postgres"
          image = "postgres:15"
          
          env {
            name  = "POSTGRES_DB"
            value = "sumo_k8"
          }
          
          env {
            name = "POSTGRES_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.secrets.metadata[0].name
                key  = "POSTGRES_PASSWORD"
              }
            }
          }
          
          port {
            container_port = 5432
          }
          
          volume_mount {
            name       = "postgres-storage"
            mount_path = "/var/lib/postgresql/data"
          }
          
          resources {
            requests = {
              cpu    = "100m"
              memory = "256Mi"
            }
          }
        }
        
        volume {
          name = "postgres-storage"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim.postgres[0].metadata[0].name
          }
        }
      }
    }
  }
}

resource "kubernetes_persistent_volume_claim" "postgres" {
  count = var.database_url == null ? 1 : 0
  
  metadata {
    name      = "postgres-pvc"
    namespace = kubernetes_namespace.sumo_k8.metadata[0].name
  }
  
  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = kubernetes_storage_class.ebs_gp3.metadata[0].name
    
    resources {
      requests = {
        storage = "10Gi"
      }
    }
  }
}

resource "kubernetes_service" "postgres" {
  count = var.database_url == null ? 1 : 0
  
  metadata {
    name      = "postgres-service"
    namespace = kubernetes_namespace.sumo_k8.metadata[0].name
  }
  
  spec {
    selector = {
      app = "postgres"
    }
    
    port {
      port        = 5432
      target_port = 5432
    }
  }
}

