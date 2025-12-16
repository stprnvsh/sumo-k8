# Terraform Quick Start

## Setup

1. **Copy variables file:**
   ```bash
   cd terraform
   cp terraform.tfvars.example terraform.tfvars
   ```

2. **Edit `terraform.tfvars`:**
   - Set `vpc_id` and subnet IDs (or `vpc_id = null` to create new)
   - Set `admin_key` (secure random string)
   - Set `s3_bucket_name`

3. **Initialize:**
   ```bash
   terraform init
   ```

4. **Plan:**
   ```bash
   terraform plan
   ```

5. **Apply:**
   ```bash
   terraform apply
   ```

6. **Build and push image:**
   ```bash
   ECR_URL=$(terraform output -raw ecr_repository_url)
   docker build --platform linux/amd64 -t $ECR_URL:latest ..
   aws ecr get-login-password --region eu-central-2 | docker login --username AWS --password-stdin $ECR_URL
   docker push $ECR_URL:latest
   ```

7. **Update kubeconfig:**
   ```bash
   eval $(terraform output -raw kubeconfig_command)
   ```

8. **Get API URL:**
   ```bash
   terraform output api_url
   ```

## Destroy

```bash
terraform destroy
```
