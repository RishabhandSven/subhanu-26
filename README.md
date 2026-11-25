# Subhanu Technologies LMS AI Prototype (Backend-First)

A role-aware, backend-first Learning Management System (LMS) AI prototype engineered for **Subhanu Technologies**. This platform demonstrates automated instructional content generation, human-in-the-loop rubric grading, student academic risk detection, and parent audit logging.

Built with **Python 3.10+**, **FastAPI**, **PostgreSQL** (`psycopg` v3), and the **Google GenAI SDK** (`gemini-3.8-flash`), with a zero-build detachable demo frontend.

---

## 1. Overview & Architectural Philosophy

The prototype is designed with a **backend-first** paradigm, decoupling AI orchestration and data governance from presentation layers:

- **Strict Minimal-File Discipline**: Exactly 8 files represent the complete system (4 core backend files, 1 detachable UI file, 1 configuration example, 1 ignore file, and 2 documentation files).
- **Three Clear Roles**:
  - `STUDENT`: Generates grounded study packs (summary, key terms, flashcards, missing information disclosures) and reads **only** approved evaluations and own outputs.
  - `TRAINER`: Runs rubric evaluations against descriptive answers, reviews/overrides scores, approves/rejects grades, monitors flagged students, and logs parent notifications.
  - `ADMIN`: Platform superuser with full trainer privileges plus cross-cohort observability and governance management.
- **Detachable Demo Interface**: The UI (`demo_ui/index.html`) is a standalone, client-side HTML/JS application interacting with the FastAPI REST API over CORS. When Subhanu's primary LMS frontend is connected, `demo_ui/` can be deleted with zero backend disruption.

---

## 2. Architecture & Design (HLD & LLD)

### High-Level Architecture (HLD)

```
+-----------------------------------------------------------------------------------+
|                                Client Layer                                       |
|  [Subhanu LMS Frontend / Detachable Demo UI: demo_ui/index.html]                  |
|    - Student Portal                - Trainer Dashboard      - Admin Audit Console |
+------------------------------------------+----------------------------------------+
                                           | HTTP REST / JSON (Header: X-Role)
                                           v
+-----------------------------------------------------------------------------------+
|                              FastAPI Service (main.py)                            |
|  - Role Guard & RBAC Middleware          - Health Probes (/health)                |
|  - Study Pack Endpoints (/study-packs)   - Evaluation Lifecycle (/evaluations)    |
|  - Risk Management (/risk-flags)         - Parent Audit Logging (/parent-audit)   |
+---------------------+-------------------------------------+-----------------------+
                      |                                     |
                      v                                     v
+------------------------------------+   +------------------------------------------+
|      Persistence Layer (db.py)     |   |          AI Engine (agents.py)           |
|  - PostgreSQL (psycopg v3)         |   |  - Google GenAI SDK (gemini-3.8-flash)   |
|  - DDL Schema & Indexes            |   |  - Strict Pydantic JSON Schemas          |
|  - Deterministic Risk Recompute    |   |  - Grounding & Anti-Hallucination Guard  |
|  - Idempotent Seed Data            |   |  - Mandatory PENDING_APPROVAL Lock       |
+------------------------------------+   +------------------------------------------+
```

### Low-Level Design (LLD) & Data Flow

1. **Study Pack Generation**:
   `POST /study-packs` $\rightarrow$ `agents.generate_study_pack()` $\rightarrow$ Gemini validates source text against requested topic. If concepts are absent, Gemini records them under `missing_info_notes` $\rightarrow$ Output persisted into `study_outputs` table with `student_id`.
2. **Descriptive Answer Evaluation & Review**:
   `POST /evaluations` $\rightarrow$ `agents.evaluate_descriptive_answer()` $\rightarrow$ Gemini computes `ai_score` (0–100), detailed rubric feedback, and `missing_concepts` $\rightarrow$ Record saved in `evaluations` table strictly with `status = 'PENDING_APPROVAL'`.
3. **Trainer Grade Review & Publishing**:
   `PATCH /evaluations/{id}/review` $\rightarrow$ Trainer accepts AI score or specifies `final_score` and `trainer_comments` $\rightarrow$ Status changes to `APPROVED` or `REJECTED`. Students only query `WHERE status = 'APPROVED'`.
4. **Risk Computation**:
   On every new evaluation or trainer review, `db.recompute_risk(student_id)` recalculates the student's average score and pending/low count, updating `risk_flags`.
5. **Parent Audit Logging**:
   `POST /parent-audit-logs` writes immutable audit entries when risk alerts or interventions are communicated to guardians.

### Attaching Hand-Drawn Diagrams
To attach your physical hand-drawn architecture sketch:
1. Save the scanned image or photo as `architecture_diagram.png` in the repository root.
2. Embed it in this document using: `![Architecture Diagram](architecture_diagram.png)`.

---

## 3. Risk & Governance Logic

### Why a Deterministic Rule-Based Risk Engine?
Rather than using opaque LLM evaluation for student academic risk, `db.py` uses a **transparent, deterministic rules engine**:
- **Auditability & Explainability**: Academic flagging directly impacts student interventions and parent communications. Deterministic rules ensure every flag is mathematically provable.
- **Cost & Latency Efficiency**: Risk recalculates in microseconds via SQL aggregation instead of calling LLM tokens on every grade change.
- **Fairness & Consistency**: Eliminates probabilistic variance or bias in determining academic risk.

### Risk Threshold Formula
The `recompute_risk(student_id)` function executes:
$$\text{Average Score} = \text{AVG}(\text{COALESCE}(\text{final\_score}, \text{ai\_score}))$$
- **`FLAGGED`**: Average Score $< 50.0\%$ **OR** $\ge 3$ evaluations that are either low ($< 50.0\%$) or in `PENDING_APPROVAL`.
- **`HIGH`**: Average Score $< 65.0\%$.
- **`MEDIUM`**: Average Score $< 80.0\%$.
- **`LOW`**: Average Score $\ge 80.0\%$ (or no recorded evaluations).

### Trainer Review & Approval Safeguard
- AI is treated as an assistant, never the final authority.
- Every AI evaluation has `suggested_status = 'PENDING_APPROVAL'`.
- The student endpoint `GET /evaluations` strictly queries `status = 'APPROVED'`. Unapproved grades never leak to students.

### Parent Conversation Audit Log
The `parent_audit_logs` table records every guardian communication and intervention. 
**Sample JSON record**:
```json
{
  "id": 1,
  "student_id": 3,
  "student_name": "Jordan Flagged Student",
  "actor_id": 1,
  "action": "RISK_STATUS_FLAGGED_NOTIFIED",
  "metadata": {
    "channel": "parent_portal_summary",
    "triggered_by": "risk_recompute_engine",
    "risk_level": "FLAGGED",
    "intervention_recommended": true,
    "contact_phone": "+1-555-0199"
  },
  "logged_at": "2026-11-26T02:15:26Z"
}
```

---

## 4. Setup and Run Instructions

### Prerequisites
- Python 3.10+
- Docker (for PostgreSQL) or an existing PostgreSQL instance
- Google Gemini API Key (or GCP Vertex AI service credentials)

### Step 1: Clone and Configure Environment
1. Clone the repository and navigate into the workspace:
   ```bash
   cd "d:/subhanu 26"
   ```
2. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
3. Edit `.env` and set your API key:
   ```ini
   GEMINI_API_KEY=your_gemini_api_key
   GOOGLE_GENAI_USE_VERTEXAI=False
   GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID
   GOOGLE_CLOUD_LOCATION=us-central1
   DATABASE_URL=postgresql://postgres:password@localhost:5432/subhanu_lms
   ```

### Step 2: Start PostgreSQL with Docker
Run a lightweight PostgreSQL container:
```bash
docker run --name subhanu-postgres -e POSTGRES_PASSWORD=password -e POSTGRES_DB=subhanu_lms -p 5432:5432 -d postgres:16-alpine
```

### Step 3: Install Dependencies
Create a virtual environment and install requirements:
```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux / macOS:
# source .venv/bin/activate

pip install fastapi uvicorn "psycopg[binary]" google-genai pydantic
```

### Step 4: Run the Backend
Start the FastAPI server:
```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```
- Server URL: `http://127.0.0.1:8000`
- Interactive Swagger API Docs: `http://127.0.0.1:8000/docs`
- On initial startup, `main.py` automatically initializes tables, enums, indexes, and demo seed data (including a flagged student).

### Step 5: Open the Demo UI
Open `demo_ui/index.html` in your browser:
- On Windows: Double-click `demo_ui/index.html` or run:
  ```powershell
  Start-Process "demo_ui/index.html"
  ```
- Use the **Role Selector** in the header to switch between `Student`, `Trainer`, and `Admin` to test role-based behavior.

---

## 5. Cloud Deployment (GCP / Vertex AI & Cloud Run)

To run this backend on Google Cloud Platform:
1. **Enable Vertex AI**: Set `GOOGLE_GENAI_USE_VERTEXAI=True`, `GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID`, and `GOOGLE_CLOUD_LOCATION=us-central1`. Ensure the Cloud Run service account has the `roles/aiplatform.user` IAM role.
2. **Cloud SQL**: Deploy a Cloud SQL for PostgreSQL instance, set `DATABASE_URL` via Google Secret Manager, and connect using the Cloud SQL Auth Proxy or Unix socket.
3. **Cloud Run Deployment**:
   ```bash
   gcloud run deploy subhanu-lms-ai \
     --source . \
     --region us-central1 \
     --allow-unauthenticated \
     --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=True,GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID
   ```

---

## 6. Assumptions, Limitations & Future Roadmap (<500 Students)

### Design Assumptions
1. **Authentication Delegation**: In this prototype, role authorization is passed via the `X-Role` header for rapid testing. Production Subhanu LMS will authenticate users via JWT tokens issued by an identity provider (e.g. Firebase Auth, Auth0, or Supabase).
2. **Source Grounding**: Instructors provide textbook or lecture transcripts as plain text. The AI must report gaps rather than synthesizing external knowledge.

### Current Prototype Limitations
- Single PostgreSQL database instance without connection pooling (e.g. PgBouncer).
- Batch evaluation queues are synchronous; high concurrency descriptive grading would benefit from Celery or Google Cloud Tasks.
- Demo UI relies on hardcoded demo user IDs (`3` for flagged student, `2` for trainer, `1` for admin).

### Scaling to 500 Students (Next Steps)
1. **Vector RAG Integration**: Ingest course syllabi and PDFs into PostgreSQL using `pgvector` for semantic search before study pack generation.
2. **Async Grading Worker**: Decouple `POST /evaluations` using a message broker (Redis or Cloud Pub/Sub) so batches of 100+ submissions process in parallel without request timeouts.
3. **Automated Notification Webhooks**: Trigger real-time SMS/Email webhooks (e.g., Twilio or SendGrid) to parents when `recompute_risk` enters `FLAGGED` state.
4. **Fine-Grained RBAC & JWT**: Replace `X-Role` with cryptographically verified OAuth2/JWT tokens containing claims and tenant IDs.
