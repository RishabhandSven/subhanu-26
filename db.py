"""Database module for Subhanu Technologies LMS AI prototype.

Handles PostgreSQL connection using psycopg, DDL schema management,
transparent risk recomputation, and idempotent seeding.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional
from dotenv import load_dotenv
import psycopg
from psycopg.rows import dict_row

load_dotenv()

DEFAULT_DB_URL = "postgresql://lms_user:lms_pass@localhost:5433/subhanu_lms"


def get_db_url() -> str:
    """Retrieve database connection URL from environment or fallback default."""
    return os.getenv("DATABASE_URL", DEFAULT_DB_URL)


@contextmanager
def get_connection(db_url: Optional[str] = None) -> Generator[psycopg.Connection, None, None]:
    """Provide a transactional psycopg connection with dictionary row factory."""
    url = db_url or get_db_url()
    conn = psycopg.connect(url, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(conn: Optional[psycopg.Connection] = None) -> None:
    """Initialize database schema tables, types, and indexes idempotently."""

    schema_sql = """
    DO $$ BEGIN
        CREATE TYPE user_role AS ENUM ('STUDENT', 'TRAINER', 'ADMIN');
    EXCEPTION
        WHEN duplicate_object THEN null;
    END $$;

    DO $$ BEGIN
        CREATE TYPE eval_status AS ENUM ('PENDING_APPROVAL', 'APPROVED', 'REJECTED');
    EXCEPTION
        WHEN duplicate_object THEN null;
    END $$;

    DO $$ BEGIN
        CREATE TYPE risk_level AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'FLAGGED');
    EXCEPTION
        WHEN duplicate_object THEN null;
    END $$;

    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        email VARCHAR(255) UNIQUE NOT NULL,
        full_name VARCHAR(255) NOT NULL,
        role user_role NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS study_outputs (
        id SERIAL PRIMARY KEY,
        student_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title VARCHAR(255) NOT NULL,
        source_text TEXT NOT NULL,
        summary TEXT NOT NULL,
        key_terms JSONB NOT NULL DEFAULT '[]'::jsonb,
        flashcards JSONB NOT NULL DEFAULT '[]'::jsonb,
        missing_info_notes TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS evaluations (
        id SERIAL PRIMARY KEY,
        student_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        trainer_id INT REFERENCES users(id) ON DELETE SET NULL,
        topic VARCHAR(255) NOT NULL,
        rubric TEXT NOT NULL,
        answer_text TEXT NOT NULL,
        ai_score NUMERIC(5, 2) NOT NULL,
        feedback TEXT NOT NULL,
        missing_concepts JSONB NOT NULL DEFAULT '[]'::jsonb,
        status eval_status NOT NULL DEFAULT 'PENDING_APPROVAL',
        final_score NUMERIC(5, 2),
        trainer_comments TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS risk_flags (
        id SERIAL PRIMARY KEY,
        student_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        risk_level risk_level NOT NULL DEFAULT 'LOW',
        score_average NUMERIC(5, 2) NOT NULL DEFAULT 0.00,
        unapproved_or_low_eval_count INT NOT NULL DEFAULT 0,
        reason TEXT NOT NULL,
        computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS parent_audit_logs (
        id SERIAL PRIMARY KEY,
        student_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        actor_id INT REFERENCES users(id) ON DELETE SET NULL,
        action VARCHAR(100) NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        logged_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
    CREATE INDEX IF NOT EXISTS idx_study_outputs_student ON study_outputs(student_id);
    CREATE INDEX IF NOT EXISTS idx_evaluations_student_status ON evaluations(student_id, status);
    CREATE INDEX IF NOT EXISTS idx_risk_flags_student ON risk_flags(student_id);
    CREATE INDEX IF NOT EXISTS idx_parent_audit_student ON parent_audit_logs(student_id);
    """

    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
    else:
        with get_connection() as c:
            with c.cursor() as cur:
                cur.execute(schema_sql)


def recompute_risk(student_id: int, conn: Optional[psycopg.Connection] = None) -> Dict[str, Any]:
    """Transparently recompute risk status for a student based on evaluations.

    Rules:
    - If average score < 50.0 or low/unapproved evaluations count >= 3 -> FLAGGED
    - Else if average score < 65.0 -> HIGH
    - Else if average score < 80.0 -> MEDIUM
    - Else -> LOW
    """

    def _execute(active_conn: psycopg.Connection) -> Dict[str, Any]:
        with active_conn.cursor() as cur:
            cur.execute(
                """
                SELECT 
                    COUNT(*) as total_count,
                    COALESCE(AVG(COALESCE(final_score, ai_score)), 0.00) as avg_score,
                    COUNT(*) FILTER (
                        WHERE COALESCE(final_score, ai_score) < 50.0 
                           OR status = 'PENDING_APPROVAL'
                    ) as low_or_pending_count
                FROM evaluations
                WHERE student_id = %s
                """,
                (student_id,),
            )
            row = cur.fetchone() or {}
            total = int(row.get("total_count", 0))
            avg_score = float(row.get("avg_score", 0.0))
            low_or_pending = int(row.get("low_or_pending_count", 0))

            if total == 0:
                level = "LOW"
                reason = "No evaluations recorded yet; baseline low risk."
            elif avg_score < 50.0 or low_or_pending >= 3:
                level = "FLAGGED"
                reason = (
                    f"Student requires immediate trainer intervention. Average score is {avg_score:.1f}% "
                    f"and {low_or_pending} evaluations are below threshold or pending review."
                )
            elif avg_score < 65.0:
                level = "HIGH"
                reason = f"Academic performance indicates high struggle risk (avg: {avg_score:.1f}%)."
            elif avg_score < 80.0:
                level = "MEDIUM"
                reason = f"Moderate performance requiring periodic monitoring (avg: {avg_score:.1f}%)."
            else:
                level = "LOW"
                reason = f"Good academic standing (avg: {avg_score:.1f}%)."

            cur.execute(
                """
                INSERT INTO risk_flags (student_id, risk_level, score_average, unapproved_or_low_eval_count, reason, computed_at)
                VALUES (%s, %s::risk_level, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING id, student_id, risk_level, score_average, unapproved_or_low_eval_count, reason, computed_at
                """,
                (student_id, level, round(avg_score, 2), low_or_pending, reason),
            )
            record = cur.fetchone()
            return dict(record) if record else {}

    if conn is not None:
        return _execute(conn)
    with get_connection() as c:
        return _execute(c)


def seed_data(conn: Optional[psycopg.Connection] = None) -> None:
    """Seed initial prototype users, evaluations, risk flag, and parent audit log idempotently."""

    def _execute(active_conn: psycopg.Connection) -> None:
        with active_conn.cursor() as cur:
            # 1. Seed demo users for each of the three roles
            users_to_seed = [
                ("admin@subhanu.local", "Platform Admin", "ADMIN"),
                ("trainer.doe@subhanu.local", "Alex Trainer", "TRAINER"),
                ("student.flagged@subhanu.local", "Jordan Flagged Student", "STUDENT"),
                ("student.active@subhanu.local", "Sam Regular Student", "STUDENT"),
            ]

            user_ids: Dict[str, int] = {}
            for email, name, role in users_to_seed:
                cur.execute(
                    """
                    INSERT INTO users (email, full_name, role)
                    VALUES (%s, %s, %s::user_role)
                    ON CONFLICT (email) DO UPDATE 
                    SET full_name = EXCLUDED.full_name
                    RETURNING id, email
                    """,
                    (email, name, role),
                )
                row = cur.fetchone()
                if row:
                    user_ids[email] = row["id"]

            trainer_id = user_ids.get("trainer.doe@subhanu.local")
            flagged_student_id = user_ids.get("student.flagged@subhanu.local")
            admin_id = user_ids.get("admin@subhanu.local")

            if flagged_student_id and trainer_id:
                # 2. Seed evaluations for the flagged student (ensuring status remains PENDING_APPROVAL)
                cur.execute(
                    """
                    SELECT COUNT(*) as cnt FROM evaluations WHERE student_id = %s
                    """,
                    (flagged_student_id,),
                )
                eval_count = cur.fetchone()["cnt"]

                if eval_count == 0:
                    evaluations_seed = [
                        (
                            flagged_student_id,
                            trainer_id,
                            "Database Normalization Basics",
                            "Assess understanding of 1NF, 2NF, and 3NF functional dependencies.",
                            "1NF is about rows and columns, 2NF removes some repeats.",
                            42.00,
                            "Answer misses transitive dependencies and candidate keys.",
                            '["3NF definition", "transitive dependency", "lossless decomposition"]',
                            "PENDING_APPROVAL",
                        ),
                        (
                            flagged_student_id,
                            trainer_id,
                            "ACID Properties in Relational Databases",
                            "Assess atomicity, consistency, isolation, and durability guarantees.",
                            "ACID means reliable data storage across servers.",
                            48.50,
                            "Lacks concrete definitions of Isolation levels and durability logs.",
                            '["WAL logging", "Isolation anomalies", "Atomicity rollback"]',
                            "PENDING_APPROVAL",
                        ),
                        (
                            flagged_student_id,
                            trainer_id,
                            "Index Selection Strategies",
                            "Assess B-Tree vs Hash index usage and query selectivity.",
                            "Indexes make searches run faster always.",
                            45.00,
                            "Failed to address write overhead, composite index order, and scan thresholds.",
                            '["B-tree vs Hash index", "write amplification", "index selectivity"]',
                            "PENDING_APPROVAL",
                        ),
                    ]

                    for item in evaluations_seed:
                        cur.execute(
                            """
                            INSERT INTO evaluations (
                                student_id, trainer_id, topic, rubric, answer_text,
                                ai_score, feedback, missing_concepts, status
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::eval_status)
                            """,
                            item,
                        )

                # 3. Transparently compute and store risk status
                recompute_risk(flagged_student_id, active_conn)

                # 4. Seed parent audit log entry
                cur.execute(
                    """
                    SELECT COUNT(*) as cnt FROM parent_audit_logs WHERE student_id = %s
                    """,
                    (flagged_student_id,),
                )
                audit_count = cur.fetchone()["cnt"]

                if audit_count == 0:
                    cur.execute(
                        """
                        INSERT INTO parent_audit_logs (student_id, actor_id, action, metadata)
                        VALUES (%s, %s, %s, %s::jsonb)
                        """,
                        (
                            flagged_student_id,
                            admin_id,
                            "RISK_STATUS_FLAGGED_NOTIFIED",
                            '{"channel": "parent_portal_summary", "triggered_by": "risk_recompute_engine", "risk_level": "FLAGGED", "intervention_recommended": true}',
                        ),
                    )

    if conn is not None:
        _execute(conn)
    else:
        with get_connection() as c:
            _execute(c)


if __name__ == "__main__":
    print("Initializing Subhanu LMS Database schema...")
    try:
        init_db()
        print("Schema created/verified successfully.")
        print("Seeding demo data...")
        seed_data()
        print("Seed completed successfully.")
    except Exception as e:
        print(f"Database operation failed: {e}")
