"""LLM agent helpers for Subhanu Technologies LMS AI prototype.

Uses Google GenAI SDK (`google-genai`) with `gemini-3.8-flash` and strict
JSON schema / MIME output typing for study packs and descriptive answer evaluations.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")


class KeyTermItem(BaseModel):
    term: str = Field(description="Key concept or technical term identified in the source text.")
    definition: str = Field(description="Clear explanation strictly grounded in source_text.")


class FlashcardItem(BaseModel):
    question: str = Field(description="Review question testing comprehension.")
    answer: str = Field(description="Direct, factual answer derived from source_text.")


class StudyPackOutput(BaseModel):
    title: str = Field(description="Title of the study module.")
    summary: str = Field(description="Concise summary synthesized strictly from the source text.")
    key_terms: List[KeyTermItem] = Field(
        default_factory=list,
        description="Key terms and definitions present in the source text.",
    )
    flashcards: List[FlashcardItem] = Field(
        default_factory=list,
        description="Flashcards for spaced repetition and self-study.",
    )
    missing_info_notes: Optional[str] = Field(
        default=None,
        description=(
            "Explicit statements highlighting relevant topics, facts, or context "
            "missing from the source text to prevent hallucination."
        ),
    )


class EvaluationOutput(BaseModel):
    topic: str = Field(description="Topic under evaluation.")
    ai_score: float = Field(
        ge=0.0,
        le=100.0,
        description="AI calculated score between 0.0 and 100.0 matching rubric standards.",
    )
    feedback: str = Field(
        description="Constructive and actionable feedback explaining the score and breakdown."
    )
    missing_concepts: List[str] = Field(
        default_factory=list,
        description="Key concepts or required points from the rubric that were omitted or incorrectly explained.",
    )
    suggested_status: str = Field(
        default="PENDING_APPROVAL",
        description="Always stays in PENDING_APPROVAL. Evaluations are never automatically approved without trainer review.",
    )


def get_genai_client(api_key: Optional[str] = None) -> genai.Client:
    """Instantiate Google GenAI client checking environment flags."""
    key = api_key or os.getenv("GEMINI_API_KEY")
    use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "false").lower() in ("true", "1", "yes")

    if use_vertex:
        project = os.getenv("GOOGLE_CLOUD_PROJECT", "YOUR_PROJECT_ID")
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        return genai.Client(vertexai=True, project=project, location=location)

    return genai.Client(api_key=key)


def generate_study_pack(
    title: str,
    source_text: str,
    client: Optional[genai.Client] = None,
    model: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    """Generate structured study pack grounded strictly in provided source_text.

    Explicitly flags missing information rather than fabricating facts.
    """
    ai_client = client or get_genai_client()

    prompt = f"""
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
"""

    response = ai_client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=StudyPackOutput,
            temperature=0.2,
        ),
    )

    if response.text is None:
        raise ValueError("Empty response received from Gemini model.")

    output_model = StudyPackOutput.model_validate_json(response.text)
    return output_model.model_dump()


def evaluate_descriptive_answer(
    topic: str,
    rubric: str,
    answer_text: str,
    client: Optional[genai.Client] = None,
    model: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    """Evaluate a student's descriptive response against a trainer rubric.

    Important governance rule:
    AI evaluations provide a score and feedback but ALWAYS remain in `PENDING_APPROVAL`
    status to await trainer verification.
    """
    ai_client = client or get_genai_client()

    prompt = f"""
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
"""

    response = ai_client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=EvaluationOutput,
            temperature=0.1,
        ),
    )

    if response.text is None:
        raise ValueError("Empty response received from Gemini model.")

    output_model = EvaluationOutput.model_validate_json(response.text)
    result = output_model.model_dump()
    # Enforce non-negotiable governance constraint
    result["suggested_status"] = "PENDING_APPROVAL"
    return result


if __name__ == "__main__":
    print("Subhanu LMS AI Agents module loaded successfully.")
    print(f"Target model: {DEFAULT_MODEL}")
    print("Structured output schemas: StudyPackOutput, EvaluationOutput.")
