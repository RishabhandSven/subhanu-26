# Subhanu Technologies LMS AI - Prompts & Governance Log

This document records the prompt engineering methodology, structured output schemas, governance safeguards, evaluation edge cases, and walkthrough talking points used during the development of the Subhanu Technologies LMS AI prototype.

---

## 1. Staged Prompt Iterations & Structured Schemas

### Prompt 1: Grounded Study Pack Generator (`generate_study_pack`)

- **Objective**: Generate concise summaries, key technical terms, and revision flashcards strictly grounded in provided instructional source text.
- **Model**: `gemini-3.8-flash`
- **Output Mode**: `response_mime_type="application/json"` with Pydantic schema validation (`StudyPackOutput`).

#### Prompt Text
```
You are an expert instructional designer and LMS AI assistant for Subhanu Technologies.
Your task is to create a structured study pack for students.

STRICT GROUNDING & ANTI-HALLUCINATION RULES:
1. Rely ONLY on the information present in the provided `Source Text`.
2. Do NOT invent, assume, extrapolate, or fabricate any facts or concepts not directly supported by the text.
3. If important aspects, context, or expected concepts related to "{title}" are missing or absent from the source text, you MUST explicitly document them in the `missing_info_notes` field (e.g., "The provided text does not cover X, Y, or Z.").
4. Flashcards and key terms must only contain definitions and answers that are directly verifiable from the source text.

Title: {title}

Source Text:
\"\"\"
{source_text}
\"\"\"
```

#### JSON Schema Structure
```json
{
  "title": "string",
  "summary": "string",
  "key_terms": [
    {
      "term": "string",
      "definition": "string"
    }
  ],
  "flashcards": [
    {
      "question": "string",
      "answer": "string"
    }
  ],
  "missing_info_notes": "string | null"
}
```

---

### Prompt 2: Descriptive Answer Rubric Evaluator (`evaluate_descriptive_answer`)

- **Objective**: Rigorously grade a student's open-ended descriptive response against a trainer-defined rubric, outputting numerical marks, detailed feedback, and missing concepts.
- **Model**: `gemini-3.8-flash`
- **Output Mode**: `response_mime_type="application/json"` with Pydantic schema validation (`EvaluationOutput`).

#### Prompt Text
```
You are an expert evaluator and academic tutor for Subhanu Technologies LMS.
Evaluate the following student's descriptive answer strictly using the provided rubric.

GOVERNANCE & EVALUATION RULES:
1. Provide a rigorous, fair numerical score between 0.0 and 100.0 based on the rubric criteria.
2. Provide constructive feedback detailing strengths, misconceptions, and areas for improvement.
3. List any missing concepts or omitted rubric criteria under `missing_concepts`.
4. The `suggested_status` MUST strictly be "PENDING_APPROVAL". Never mark as approved. Trainer review is mandatory.

Topic: {topic}

Rubric:
\"\"\"
{rubric}
\"\"\"

Student Answer:
\"\"\"
{answer_text}
\"\"\"
```

#### JSON Schema Structure
```json
{
  "topic": "string",
  "ai_score": "float (0.0 - 100.0)",
  "feedback": "string",
  "missing_concepts": ["string"],
  "suggested_status": "PENDING_APPROVAL"
}
```

---

## 2. What Was Accepted vs. Modified

| Component | Initial AI Proposal | Why It Was Modified / Hardened | Final Implementation |
| :--- | :--- | :--- | :--- |
| **Grade Approval** | Model allowed to suggest `APPROVED` if score was high ($>90\%$). | Auto-approving bypasses human pedagogical responsibility. Students could see unvalidated grades. | Model schema locks `suggested_status` to `"PENDING_APPROVAL"`. In `agents.py`, python code overrides and forces `"PENDING_APPROVAL"` regardless of model response. |
| **Missing Information** | Model attempted to supply missing background knowledge using general LLM training weights. | Hallucination risk: In course environments, students must be evaluated on the assigned curriculum, not conflicting external definitions. | Added mandatory `missing_info_notes` field and explicit prompt rule instructing the LLM to record what the text lacked. |
| **Evaluation Score Type** | Raw integer scores or subjective grade letters (`A`, `B-`). | Letter grades lack the granularity needed for deterministic threshold arithmetic. | Standardized on `float` bounded between `0.0` and `100.0` in Pydantic (`ge=0.0, le=100.0`) and `NUMERIC(5, 2)` in PostgreSQL. |
| **Risk Scoring** | Using an LLM prompt to classify student risk as Low/Medium/High. | LLM scoring of risk is non-deterministic, cost-inefficient, and creates auditing liabilities. | Replaced with deterministic SQL calculation in `db.recompute_risk()` based on average scores and pending counts. |

---

## 3. Key Edge Cases Verified

1. **Short / Incomplete Source Text**:
   - *Input*: Title = "Complete Guide to PostgreSQL Indexing", Source Text = "B-Trees are the default index type in Postgres."
   - *Result*: The model generated 1 term and 1 flashcard, and successfully populated `missing_info_notes`: *"The provided source text does not cover Hash, GiST, GIN, BRIN indexes, partial indexes, or performance trade-offs."*
2. **Partial / Incomplete Descriptive Answer**:
   - *Input*: Question on ACID properties where the student only defined Atomicity.
   - *Result*: `ai_score` was calculated at `25.0/100.0`. `missing_concepts` populated: `["Consistency guarantees", "Isolation levels", "Durability and WAL"]`. `status` was set to `PENDING_APPROVAL`.
3. **Student Grade Visibility Boundary**:
   - *Check*: Student makes request `GET /evaluations?student_id=3`.
   - *Result*: `main.py` enforces SQL clause `WHERE status = 'APPROVED'`. The three seeded evaluations (which are in `PENDING_APPROVAL`) return an empty list `[]` to the student until a trainer clicks **Approve** in the trainer portal.
4. **Trainer Override Dynamics**:
   - *Check*: Trainer reviews a `42.0%` evaluation, inputs `final_score = 65.0%` with note *"Gave partial credit for verbal clarification in class"*, and approves.
   - *Result*: Evaluation updates to `APPROVED` with `final_score = 65.0`. `recompute_risk` automatically recalculates with the updated score.

---

## 4. Live Walkthrough Talking Points

When presenting or demonstrating the Subhanu LMS prototype, highlight these four core talking points:

1. **Backend-First Architectural Purity**:
   - *"Notice how clean the workspace is: exactly 8 files, zero frontend build step, yet completely functional. The demo UI is totally decoupled and can be deleted the moment the real LMS frontend is ready to integrate."*
2. **Pedagogical Safety & Grounding**:
   - *"In our study pack generator, notice the `missing_info_notes` alert. If the instructor uploads an incomplete text, our Gemini prompt explicitly flags the gaps to the student rather than hallucinating external facts."*
3. **Human-in-the-Loop Evaluation Governance**:
   - *"Every AI evaluation is locked in `PENDING_APPROVAL`. Notice what happens when we switch to the Student role in the UI: the student cannot see their grade until a human trainer reviews the rubric, optionally adjusts the score, and clicks Approve."*
4. **Transparent Academic Risk & Audit Trail**:
   - *"Instead of letting a black-box LLM guess if a student is at risk, we compute risk deterministically in PostgreSQL. When a student falls below 50% or accumulates unapproved evaluations, they are flagged, and all parent communications are immutably logged in `parent_audit_logs`."*
