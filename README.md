# AI Workflow Automation for Job Scraping

An automated job scraping system that uses AI-powered workflows to collect freelance and contract job opportunities from multiple Belgian job platforms. Built with LangGraph for workflow orchestration, Playwright for browser automation, and Gemini for intelligent content analysis.

## Overview

This system automates the discovery of IT freelance opportunities by:

1. **Scraping job platforms** - Automated browser sessions navigate job sites, handle login/authentication, and extract job listings
2. **AI-powered filtering** - Uses Gemini LLM to analyze job descriptions and filter for relevant IT/software development roles
3. **Deduplication** - Stores jobs in Azure SQL Database with unique constraints to avoid duplicates
4. **CSV export** - Generates CSV files for easy review and tracking

## Runtime Model

- **Worker job** runs the scraping workflows on a schedule (Azure Container Apps Job).
- **API app** serves read-only endpoints to query jobs stored in Azure SQL Database.

## Supported Job Sources

| Source | Type | Description |
|--------|------|-------------|
| **Connecting Expertise** | Web scraping | Belgian IT freelance platform |
| **Pro Unity** | Web scraping | IT staffing and freelance jobs |
| **BNP Paribas Fortis** | Web scraping | Bank IT career portal |
| **Elia** | Web scraping | Energy sector IT jobs |
| **AG Insurance** | Email parsing | Job notifications via M365 Graph API |

## Architecture

```
src/
├── api/                    # FastAPI REST endpoints
│   └── routes/
│       ├── jobs.py         # Job CRUD endpoints
│       ├── workflows.py    # Workflow management
│       └── executions.py   # Execution tracking
├── browser/                # Playwright automation
│   ├── manager.py          # Browser lifecycle management
│   └── actions.py          # Reusable browser actions
├── db/                     # Database layer
│   ├── models.py           # SQLAlchemy models
│   ├── connection.py       # Async SQL Server connection
│   └── job_repository.py   # Job CRUD operations
├── providers/              # LLM providers
│   ├── gemini.py           # Google Gemini
│   ├── openai.py           # OpenAI GPT
│   └── anthropic.py        # Anthropic Claude
├── integrations/
│   └── m365.py             # Microsoft 365 Graph API
├── workflows/
│   ├── base.py             # Base workflow class
│   ├── registry.py         # Workflow registration
│   └── examples/           # Job scraper workflows
│       ├── connecting_expertise.py
│       ├── pro_unity.py
│       ├── bnppf_jobs.py
│       ├── elia_jobs.py
│       └── ag_insurance.py
```

## Prerequisites

- Python 3.11+
- Docker & Docker Compose
- ODBC Driver 18 for SQL Server (for local dev)
- Google Gemini API key (or OpenAI/Anthropic)

## Installation

1. **Clone and setup virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/Mac
   .venv\Scripts\activate     # Windows
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

3. **Start SQL Server**
   ```bash
   docker-compose up -d
   ```

4. **Create the database (local dev)**
   ```bash
   docker exec -it workflow_sqlserver /opt/mssql-tools/bin/sqlcmd \
     -S localhost -U sa -P "YourStrong!Passw0rd" \
     -Q "IF DB_ID('workflow_db') IS NULL CREATE DATABASE workflow_db;"
   ```

5. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys and credentials
   ```

## Configuration

Edit `.env` file:

```env
# Database
DATABASE_URL=mssql+aioodbc:///?odbc_connect=Driver%3D%7BODBC+Driver+18+for+SQL+Server%7D%3BServer%3Dtcp%3Alocalhost%2C1433%3BDatabase%3Dworkflow_db%3BUid%3Dsa%3BPwd%3DYourStrong!Passw0rd%3BEncrypt%3Dno%3BTrustServerCertificate%3Dyes%3BConnection+Timeout%3D30%3B

# LLM Provider (gemini, openai, anthropic)
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_api_key_here

# Browser settings
BROWSER_HEADLESS=true

# M365 (for AG Insurance email workflow)
M365_TENANT_ID=your_tenant_id
M365_CLIENT_ID=your_client_id
M365_CLIENT_SECRET=your_client_secret
M365_USER_EMAIL=your_email
```

## Usage

### Running Individual Workflows

```bash
# Connecting Expertise
python test_workflow.py

# Pro Unity
python test_workflow_prounity.py

# BNP Paribas Fortis
python test_workflow_bnppf.py

# Elia
python test_workflow_elia.py

# AG Insurance (email-based)
python test_workflow_ag.py
```

### Starting the API Server

```bash
python run.py
```

API available at `http://localhost:8000`

### API Endpoints

- `GET /api/jobs/` - List all jobs with pagination
- `GET /api/jobs/stats` - Job counts by source
- `GET /api/jobs/{id}` - Get specific job
- `DELETE /api/jobs/{id}` - Delete a job

### Running the Worker Job

```bash
python run_worker.py
```

Control behavior with environment variables:

- `WORKFLOW_LIST` - Comma-separated workflow names to run
- `WORKFLOW_INPUTS` - JSON object keyed by workflow name with input data
- `OUTPUT_DIR` - Where CSVs are written (default `.`)
- `MAX_PAGES` - Default pagination value (optional)

## Workflow Structure

Each workflow follows this pattern:

1. **Login** - Authenticate with the platform
2. **Navigate** - Go to job listings page
3. **Fetch Jobs** - Extract job data from multiple pages
4. **Get Details** - Visit each job for full description
5. **AI Filter** - Use LLM to assess job relevance
6. **Save to DB** - Store in Azure SQL Database (skip duplicates)
7. **Generate Output** - Create CSV file

## Database Schema

### Jobs Table

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| source | String | Platform identifier |
| reference | String | Job ID from source |
| title | String | Job title |
| client | String | Company/client name |
| description_summary | Text | AI-generated summary |
| location | String | Work location |
| start_date | String | Contract start |
| end_date | String | Contract end |
| skills | Text | Required skills |
| url | String | Original job URL |
| raw_data | JSON | Complete original data |

Unique constraint on `(source, reference)` prevents duplicates.

## Azure Container Apps Deployment

This project is container-ready and can run in Azure Container Apps with Azure SQL Database.

### 1) Build and push the container image

```bash
docker build -t <acr_name>.azurecr.io/ai-workflow-engine:latest .
docker push <acr_name>.azurecr.io/ai-workflow-engine:latest
```

### 2) Configure environment variables in the Container App

At minimum:

```env
APP_ENV=production
LOG_LEVEL=INFO
PORT=8000

# Azure SQL Database (ODBC connection string must be URL-encoded)
DATABASE_URL=mssql+aioodbc:///?odbc_connect=Driver%3D%7BODBC+Driver+18+for+SQL+Server%7D%3BServer%3Dtcp%3A<server>.database.windows.net%2C1433%3BDatabase%3D<db>%3BUid%3D<user>%40<server>%3BPwd%3D<password>%3BEncrypt%3Dyes%3BTrustServerCertificate%3Dno%3BConnection+Timeout%3D30%3B

# LLM provider
LLM_PROVIDER=gemini
GEMINI_API_KEY=...

# Browser settings
BROWSER_HEADLESS=true
```

Optional (only for email-based workflows):

```env
M365_TENANT_ID=...
M365_CLIENT_ID=...
M365_CLIENT_SECRET=...
M365_USER_EMAIL=...
```

### 3) Create the Container App

Use your preferred deployment flow (Portal, `az containerapp`, or IaC). Ensure:

- The container app exposes port `8000`.
- `min_replicas` is at least `1` if you run long workflows, so background tasks are not interrupted.
- Outbound access is allowed to the target job sites, LLM provider, and M365 Graph APIs.

### Notes

- Workflow CSV files are written to `output_dir` in `input_data` (default `.`). For persistence, mount Azure Files and pass `output_dir` to the workflow input, or move the outputs to Azure Blob Storage.
- Database tables are created at startup (`init_db`). If you plan to use Alembic migrations, run them during deployment instead of relying on auto-create.

## Adding New Job Sources

1. Create new workflow in `src/workflows/examples/`:
   ```python
   from ..base import BaseWorkflow
   from ..registry import register_workflow

   @register_workflow("new_source")
   class NewSourceWorkflow(BaseWorkflow):
       def get_entry_point(self) -> str:
           return "login"

       def define_nodes(self) -> dict:
           return {
               "login": self.login_step,
               "fetch": self.fetch_step,
               # ...
           }

       def define_edges(self) -> list:
           return [
               ("login", "fetch"),
               ("fetch", "END"),
           ]
   ```

2. Add to `JobSource` enum in `src/db/models.py`

3. Export in `src/workflows/examples/__init__.py`

4. Create test file

## Development

### Project Structure

- **LangGraph** - Workflow orchestration with state machines
- **Playwright** - Headless browser automation
- **SQLAlchemy** - Async SQL Server ORM
- **FastAPI** - REST API framework
- **Pydantic** - Configuration and validation

### Running Tests

```bash
pytest tests/
```

## License

Private - RMA Solutions
