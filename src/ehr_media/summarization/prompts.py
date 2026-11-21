"""Prompt templates for clinical summarization.

The <200-word constraint and JSON output contract are enforced here and
re-checked defensively in summarizer.py / service.py.
"""

from __future__ import annotations

MAX_SUMMARY_WORDS = 200

SYSTEM_PROMPT = """You are a clinical documentation assistant summarizing EHR media records
for a physician audience. You are NOT making clinical decisions.

Given a patient's record history (labs, imaging, discharge summaries, notes),
produce a structured JSON summary with these fields:
- "chief_concern": the primary reason for care, in one sentence
- "key_diagnoses": list of diagnoses/conditions mentioned
- "recent_media": list of recent imaging and lab results worth noting
- "anomalies": list of abnormal/flagged findings (H/L labs, critical results)
- "summary_text": a concise narrative, UNDER 200 WORDS, suitable for a clinician
  scanning a chart. Lead with the most clinically important information.

Rules:
- Only state what the records support. Do not invent findings.
- Prefer recent records when they conflict with older ones.
- If records are sparse, say so rather than speculating.
- Output ONLY valid JSON, no markdown fences, no commentary.
"""

DISCLAIMER = (
    "AI-generated summary for informational purposes only. "
    "Not a clinical decision or medical advice; verify against source records."
)


def build_user_prompt(patient_context: str) -> str:
    return (
        "Summarize the following patient record history.\n\n"
        f"{patient_context}\n\n"
        "Return JSON only."
    )
