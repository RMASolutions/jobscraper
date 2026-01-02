# Deployment Guide (Azure Container Apps + Azure SQL Database)

This guide assumes your Azure Container Registry (ACR), Container App, Container Apps Job, and Azure SQL Database already exist. It focuses on building/pushing images and configuring the Container App + Job.

## 1) Build the container images locally

From the repo root:

```bash
# API image
docker build -f Dockerfile.api -t ai-workflow-engine-api:latest .

# Worker image (includes Playwright)
docker build -f Dockerfile.worker -t ai-workflow-engine-worker:latest .
```

## 2) Tag and push the images to ACR

```bash
# Replace with your ACR login server
ACR_LOGIN_SERVER=<your-acr>.azurecr.io

# Tag the images
docker tag ai-workflow-engine-api:latest ${ACR_LOGIN_SERVER}/ai-workflow-engine-api:latest
docker tag ai-workflow-engine-worker:latest ${ACR_LOGIN_SERVER}/ai-workflow-engine-worker:latest

# Login and push
az acr login --name <your-acr>
docker push ${ACR_LOGIN_SERVER}/ai-workflow-engine-api:latest
docker push ${ACR_LOGIN_SERVER}/ai-workflow-engine-worker:latest
```

## 3) Configure the API Container App

### Container image
- Image: `${ACR_LOGIN_SERVER}/ai-workflow-engine-api:latest`
- Command: `python run.py` (default from `Dockerfile.api`, can be set explicitly)

### Ingress + port
- Ingress: enabled (external or internal based on your need)
- Target port: `8000`
- Transport: HTTP

### Environment variables

Set these in the Container App configuration:

```env
# App
APP_ENV=production
LOG_LEVEL=INFO
PORT=8000
UVICORN_RELOAD=false

# Azure SQL Database (ODBC connection string must be URL-encoded)
DATABASE_URL=mssql+aioodbc:///?odbc_connect=Driver%3D%7BODBC+Driver+18+for+SQL+Server%7D%3BServer%3Dtcp%3A<server>.database.windows.net%2C1433%3BDatabase%3D<db>%3BUid%3D<user>%40<server>%3BPwd%3D<password>%3BEncrypt%3Dyes%3BTrustServerCertificate%3Dno%3BConnection+Timeout%3D30%3B

```

Optional (only needed if you keep workflow endpoints enabled in the API image):

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=...
```

Optional (only for email-based workflows if enabled in worker):

```env
M365_TENANT_ID=...
M365_CLIENT_ID=...
M365_CLIENT_SECRET=...
M365_USER_EMAIL=...
```

### Notes
- The app creates database tables at startup via `init_db()`. If you want migrations, run Alembic separately during deployment.
- CSV outputs are written to `output_dir` in workflow input (defaults to `.`). For persistence in ACA, mount Azure Files and pass `output_dir` in the workflow input payload.

## 4) Validate

- Call `GET /health` to confirm the container is up.
- Call `GET /health/ready` to confirm database connectivity.

## 5) Configure the Container Apps Job (Worker)

### Container image
- Image: `${ACR_LOGIN_SERVER}/ai-workflow-engine-worker:latest`
- Command: `python run_worker.py`

### Environment variables

```env
# App
APP_ENV=production
LOG_LEVEL=INFO

# Azure SQL Database (ODBC connection string must be URL-encoded)
DATABASE_URL=mssql+aioodbc:///?odbc_connect=Driver%3D%7BODBC+Driver+18+for+SQL+Server%7D%3BServer%3Dtcp%3A<server>.database.windows.net%2C1433%3BDatabase%3D<db>%3BUid%3D<user>%40<server>%3BPwd%3D<password>%3BEncrypt%3Dyes%3BTrustServerCertificate%3Dno%3BConnection+Timeout%3D30%3B

# LLM provider
LLM_PROVIDER=gemini
GEMINI_API_KEY=...

# Browser automation
BROWSER_HEADLESS=true

# Worker config
WORKFLOW_LIST=connecting_expertise,pro_unity,bnppf_jobs,elia_jobs,ag_insurance
WORKFLOW_INPUTS={"connecting_expertise":{"username":"...","password":"...","max_pages":3},"pro_unity":{"username":"...","password":"..."}}
OUTPUT_DIR=/tmp
MAX_PAGES=3
WORKER_FAIL_FAST=false
```

Optional (only for email-based workflows):

```env
M365_TENANT_ID=...
M365_CLIENT_ID=...
M365_CLIENT_SECRET=...
M365_USER_EMAIL=...
```

### Schedule

Configure the job schedule (cron) in the Container Apps Job itself. This controls how often the worker runs.
