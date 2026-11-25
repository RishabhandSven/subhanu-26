"""FastAPI backend for Subhanu Technologies LMS AI prototype.

Exposes role-aware endpoints for students, trainers, and admins with
grounded study pack generation, human-in-the-loop evaluation approvals,
academic risk tracking, and parent audit logging.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import agents
import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("subhanu-lms")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Attempt database initialization and idempotent seeding on startup."""
    try:
        db.init_db()
        db.seed_data()
        logger.info("Database initialized and demo data seeded successfully.")
    except Exception as exc:
        logger.warning(
            "PostgreSQL database connection failed on startup (%s). "
            "Ensure PostgreSQL is running and DATABASE_URL is configured. Continuing with app boot.",
            exc,
        )
    yield


app = FastAPI(
    title="Subhanu Technologies LMS AI API",
    version="1.0.0",
    description="Backend-first AI prototype for Subhanu LMS supporting Student, Trainer, and Admin workflows.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# Request / Response Schemas
# ---------------------------------------------------------
class StudyPackRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=255)
    source_text: str = Field(..., min_length=10)
    student_id: int = Field(default=3, description="Student user ID")


class EvaluationRequest(BaseModel):
    student_id: int = Field(..., description="Student receiving evaluation")
    trainer_id: Optional[int] = Field(default=2, description="Trainer authoring or running evaluation")
    topic: str = Field(..., min_length=2, max_length=255)
    rubric: str = Field(..., min_length=5)
    answer_text: str = Field(..., min_length=5)


class ReviewEvaluationRequest(BaseModel):
    status: str = Field(..., description="New status: APPROVED or REJECTED")
    final_score: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    trainer_comments: Optional[str] = Field(default=None)


class ParentAuditLogRequest(BaseModel):
    student_id: int
    action: str = Field(..., min_length=3, max_length=100)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------
# Role Authorization Helper
# ---------------------------------------------------------
VALID_ROLES = {"STUDENT", "TRAINER", "ADMIN"}


def enforce_role(
    x_role: Optional[str] = Header(None, alias="X-Role"),
    allowed_roles: tuple[str, ...] = ("STUDENT", "TRAINER", "ADMIN"),
) -> str:
    """Validate incoming role against allowed roles list."""
    role = (x_role or "").upper().strip()
    if not role or role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid 'X-Role' header. Allowed values: STUDENT, TRAINER, ADMIN.",
        )
    if role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied for role '{role}'. Required one of: {list(allowed_roles)}.",
        )
    return role


# ---------------------------------------------------------
# Health Check & Metadata
# ---------------------------------------------------------
@app.get("/health", tags=["System"])
def health_check() -> Dict[str, Any]:
    """Health probe indicating server and database status."""
    db_ok = False
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "online",
        "service": "Subhanu LMS AI Prototype",
        "database_connected": db_ok,
    }


@app.get("/users", tags=["Users"])
def list_users(x_role: Optional[str] = Header(None, alias="X-Role")) -> List[Dict[str, Any]]:
    """List users registered in system for reference and role-switching."""
    enforce_role(x_role, ("STUDENT", "TRAINER", "ADMIN"))
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, email, full_name, role, created_at FROM users ORDER BY id ASC")
                return cur.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database query failed: {exc}")


# ---------------------------------------------------------
# Study Pack Endpoints
# ---------------------------------------------------------
@app.post("/study-packs", tags=["Study Packs"])
def create_study_pack(
    payload: StudyPackRequest,
    x_role: Optional[str] = Header(None, alias="X-Role"),
) -> Dict[str, Any]:
    """Generate and persist a study pack grounded strictly in source_text.

    Allowed roles: STUDENT, TRAINER, ADMIN.
    """
    enforce_role(x_role, ("STUDENT", "TRAINER", "ADMIN"))

    try:
        ai_result = agents.generate_study_pack(
            title=payload.title,
            source_text=payload.source_text,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI Study Pack generation error: {exc}")

    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO study_outputs (
                        student_id, title, source_text, summary, key_terms, flashcards, missing_info_notes
                    )
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                    RETURNING id, student_id, title, summary, key_terms, flashcards, missing_info_notes, created_at
                    """,
                    (
                        payload.student_id,
                        payload.title,
                        payload.source_text,
                        ai_result.get("summary", ""),
                        psycopg.types.json.Jsonb(ai_result.get("key_terms", [])),
                        psycopg.types.json.Jsonb(ai_result.get("flashcards", [])),
                        ai_result.get("missing_info_notes"),
                    ),
                )
                row = cur.fetchone()
                return dict(row) if row else {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save study pack: {exc}")


@app.get("/study-packs", tags=["Study Packs"])
def get_study_packs(
    student_id: Optional[int] = Query(None),
    x_role: Optional[str] = Header(None, alias="X-Role"),
) -> List[Dict[str, Any]]:
    """Fetch study outputs. Students can only view their own outputs."""
    role = enforce_role(x_role, ("STUDENT", "TRAINER", "ADMIN"))

    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                if role == "STUDENT":
                    if not student_id:
                        raise HTTPException(status_code=400, detail="student_id query param is required for students.")
                    cur.execute(
                        """
                        SELECT id, student_id, title, summary, key_terms, flashcards, missing_info_notes, created_at
                        FROM study_outputs
                        WHERE student_id = %s
                        ORDER BY created_at DESC
                        """,
                        (student_id,),
                    )
                else:
                    if student_id:
                        cur.execute(
                            """
                            SELECT id, student_id, title, summary, key_terms, flashcards, missing_info_notes, created_at
                            FROM study_outputs
                            WHERE student_id = %s
                            ORDER BY created_at DESC
                            """,
                            (student_id,),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT id, student_id, title, summary, key_terms, flashcards, missing_info_notes, created_at
                            FROM study_outputs
                            ORDER BY created_at DESC
                            """
                        )
                return cur.fetchall()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database query failed: {exc}")


# ---------------------------------------------------------
# Evaluation Endpoints
# ---------------------------------------------------------
@app.post("/evaluations", tags=["Evaluations"])
def create_evaluation(
    payload: EvaluationRequest,
    x_role: Optional[str] = Header(None, alias="X-Role"),
) -> Dict[str, Any]:
    """Run AI rubric evaluation and record it with mandatory PENDING_APPROVAL status.

    Only TRAINER and ADMIN can trigger evaluations.
    """
    enforce_role(x_role, ("TRAINER", "ADMIN"))

    try:
        ai_result = agents.evaluate_descriptive_answer(
            topic=payload.topic,
            rubric=payload.rubric,
            answer_text=payload.answer_text,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI Evaluation error: {exc}")

    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO evaluations (
                        student_id, trainer_id, topic, rubric, answer_text,
                        ai_score, feedback, missing_concepts, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, 'PENDING_APPROVAL')
                    RETURNING id, student_id, trainer_id, topic, rubric, answer_text,
                              ai_score, feedback, missing_concepts, status, final_score,
                              trainer_comments, created_at, updated_at
                    """,
                    (
                        payload.student_id,
                        payload.trainer_id,
                        payload.topic,
                        payload.rubric,
                        payload.answer_text,
                        ai_result.get("ai_score", 0.0),
                        ai_result.get("feedback", ""),
                        psycopg.types.json.Jsonb(ai_result.get("missing_concepts", [])),
                    ),
                )
                row = cur.fetchone()
                # Recompute academic risk when new evaluation is registered
                db.recompute_risk(payload.student_id, conn)
                return dict(row) if row else {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to record evaluation: {exc}")


@app.get("/evaluations", tags=["Evaluations"])
def list_evaluations(
    student_id: Optional[int] = Query(None),
    x_role: Optional[str] = Header(None, alias="X-Role"),
) -> List[Dict[str, Any]]:
    """List evaluations with strict role filtering.

    - STUDENT: only sees APPROVED evaluations for their student_id.
    - TRAINER / ADMIN: sees all evaluations across any status.
    """
    role = enforce_role(x_role, ("STUDENT", "TRAINER", "ADMIN"))

    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                if role == "STUDENT":
                    if not student_id:
                        raise HTTPException(status_code=400, detail="student_id query param is required for students.")
                    cur.execute(
                        """
                        SELECT id, student_id, trainer_id, topic, rubric, answer_text,
                               COALESCE(final_score, ai_score) as final_score,
                               feedback, trainer_comments, missing_concepts, status, created_at
                        FROM evaluations
                        WHERE student_id = %s AND status = 'APPROVED'
                        ORDER BY created_at DESC
                        """,
                        (student_id,),
                    )
                else:
                    query = """
                        SELECT id, student_id, trainer_id, topic, rubric, answer_text,
                               ai_score, final_score, feedback, trainer_comments,
                               missing_concepts, status, created_at, updated_at
                        FROM evaluations
                    """
                    params: List[Any] = []
                    if student_id:
                        query += " WHERE student_id = %s"
                        params.append(student_id)
                    query += " ORDER BY created_at DESC"
                    cur.execute(query, params)

                return cur.fetchall()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database query failed: {exc}")


@app.patch("/evaluations/{evaluation_id}/review", tags=["Evaluations"])
def review_evaluation(
    evaluation_id: int,
    payload: ReviewEvaluationRequest,
    x_role: Optional[str] = Header(None, alias="X-Role"),
) -> Dict[str, Any]:
    """Approve or Reject an evaluation with human-in-the-loop score override.

    Strictly restricted to TRAINER and ADMIN.
    """
    enforce_role(x_role, ("TRAINER", "ADMIN"))

    status_target = payload.status.upper().strip()
    if status_target not in ("APPROVED", "REJECTED"):
        raise HTTPException(status_code=400, detail="Target status must be APPROVED or REJECTED.")

    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, student_id, ai_score FROM evaluations WHERE id = %s
                    """,
                    (evaluation_id,),
                )
                existing = cur.fetchone()
                if not existing:
                    raise HTTPException(status_code=404, detail="Evaluation record not found.")

                score = payload.final_score if payload.final_score is not None else existing["ai_score"]

                cur.execute(
                    """
                    UPDATE evaluations
                    SET status = %s::eval_status,
                        final_score = %s,
                        trainer_comments = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING id, student_id, status, final_score, trainer_comments, updated_at
                    """,
                    (status_target, score, payload.trainer_comments, evaluation_id),
                )
                updated = cur.fetchone()

                # Recompute student risk after status or score change
                student_id = existing["student_id"]
                db.recompute_risk(student_id, conn)

                return dict(updated) if updated else {}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update evaluation: {exc}")


# ---------------------------------------------------------
# Risk Flags & Audit Endpoints
# ---------------------------------------------------------
@app.get("/risk-flags", tags=["Risk & Governance"])
def get_risk_flags(
    student_id: Optional[int] = Query(None),
    x_role: Optional[str] = Header(None, alias="X-Role"),
) -> List[Dict[str, Any]]:
    """List risk computation records for students.

    Restricted to TRAINER and ADMIN.
    """
    enforce_role(x_role, ("TRAINER", "ADMIN"))

    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                query = """
                    SELECT rf.id, rf.student_id, u.full_name as student_name, u.email as student_email,
                           rf.risk_level, rf.score_average, rf.unapproved_or_low_eval_count,
                           rf.reason, rf.computed_at
                    FROM risk_flags rf
                    JOIN users u ON u.id = rf.student_id
                """
                params: List[Any] = []
                if student_id:
                    query += " WHERE rf.student_id = %s"
                    params.append(student_id)
                query += " ORDER BY rf.computed_at DESC"
                cur.execute(query, params)
                return cur.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database query failed: {exc}")


@app.post("/risk-flags/recompute/{student_id}", tags=["Risk & Governance"])
def trigger_recompute_risk(
    student_id: int,
    x_role: Optional[str] = Header(None, alias="X-Role"),
) -> Dict[str, Any]:
    """Manually trigger transparent risk calculation for a student."""
    enforce_role(x_role, ("TRAINER", "ADMIN"))
    try:
        return db.recompute_risk(student_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Risk recompute failed: {exc}")


@app.get("/parent-audit-logs", tags=["Risk & Governance"])
def list_parent_audit_logs(
    student_id: Optional[int] = Query(None),
    x_role: Optional[str] = Header(None, alias="X-Role"),
) -> List[Dict[str, Any]]:
    """Retrieve audit history of parent updates and notifications.

    Restricted to TRAINER and ADMIN.
    """
    enforce_role(x_role, ("TRAINER", "ADMIN"))

    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                query = """
                    SELECT pal.id, pal.student_id, u.full_name as student_name,
                           pal.actor_id, pal.action, pal.metadata, pal.logged_at
                    FROM parent_audit_logs pal
                    JOIN users u ON u.id = pal.student_id
                """
                params: List[Any] = []
                if student_id:
                    query += " WHERE pal.student_id = %s"
                    params.append(student_id)
                query += " ORDER BY pal.logged_at DESC"
                cur.execute(query, params)
                return cur.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database query failed: {exc}")


@app.post("/parent-audit-logs", tags=["Risk & Governance"])
def add_parent_audit_log(
    payload: ParentAuditLogRequest,
    x_role: Optional[str] = Header(None, alias="X-Role"),
) -> Dict[str, Any]:
    """Log an intervention or communication action into the immutable parent audit log.

    Restricted to TRAINER and ADMIN.
    """
    enforce_role(x_role, ("TRAINER", "ADMIN"))

    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO parent_audit_logs (student_id, actor_id, action, metadata)
                    VALUES (%s, NULL, %s, %s::jsonb)
                    RETURNING id, student_id, action, metadata, logged_at
                    """,
                    (
                        payload.student_id,
                        payload.action,
                        psycopg.types.json.Jsonb(payload.metadata),
                    ),
                )
                row = cur.fetchone()
                return dict(row) if row else {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to record parent audit log: {exc}")
