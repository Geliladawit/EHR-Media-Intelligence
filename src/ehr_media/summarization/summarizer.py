"""Summarizer implementations and provider factory.

Summarizer protocol: summarize(context) -> dict matching ClinicalSummary
fields (summary_text, chief_concern, key_diagnoses, recent_media, anomalies).
The service layer attaches metadata (patient_key, record_hash, disclaimer).
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, Field

from ehr_media import config
from ehr_media.summarization.prompts import (
    DISCLAIMER,
    MAX_SUMMARY_WORDS,
    SYSTEM_PROMPT,
    build_user_prompt,
)

log = logging.getLogger(__name__)


class ClinicalSummary(BaseModel):
    patient_key: str
    summary_text: str
    chief_concern: str = ""
    key_diagnoses: list[str] = Field(default_factory=list)
    recent_media: list[str] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER
    model: str = "unknown"
    record_hash: str = ""
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class Summarizer(Protocol):
    name: str

    def summarize(self, context: str) -> dict: ...


def _truncate_words(text: str, max_words: int = MAX_SUMMARY_WORDS) -> str:
    """Hard-cap summary_text at max_words, preferring a sentence boundary."""
    words = text.split()
    if len(words) <= max_words:
        return text
    clipped = " ".join(words[:max_words])
    # back off to last sentence end if one exists in the tail
    sentences = re.split(r"(?<=[.!?])\s+", clipped)
    if len(sentences) > 1:
        clipped = " ".join(sentences[:-1]) or clipped
    return clipped


def _parse_model_json(raw: str) -> dict:
    """Extract the JSON object from a model response, tolerating fences/prose."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("model did not return a JSON object")
    return {
        "summary_text": _truncate_words(str(data.get("summary_text", ""))),
        "chief_concern": str(data.get("chief_concern", "")),
        "key_diagnoses": [str(x) for x in data.get("key_diagnoses", [])],
        "recent_media": [str(x) for x in data.get("recent_media", [])],
        "anomalies": [str(x) for x in data.get("anomalies", [])],
    }


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------

class MockSummarizer:
    """Deterministic offline summarizer for tests and keyless runs.

    Extracts flagged findings and diagnosis-like phrases with regex so output
    is reproducible and exercises the same schema as real LLM output.
    """

    name = "mock-v1"

    _FLAG_RE = re.compile(r"[^.]*\((?:H|L|high|low|critically high|markedly elevated)[^)]*\)[^.]*\.")
    _IMPRESSION_RE = re.compile(r"(?:Impression|impression|consistent with|diagnosis of|found to have)[:\s]+([^.]+\.)")

    def summarize(self, context: str) -> dict:
        flags = self._FLAG_RE.findall(context)
        impressions = [m.group(1).strip() for m in self._IMPRESSION_RE.finditer(context)]

        anomalies = [f.strip() for f in flags[:6]]
        diagnoses = list(dict.fromkeys(impressions))[:8]
        media = [ln.split("|")[1].strip() for ln in context.splitlines()
                 if "|" in ln and any(t in ln.lower() for t in ("imaging", "lab", "x-ray", "ct ", "mri"))][:6]

        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", context)
                     if s.strip() and len(s.split()) > 4]
        lead = sentences[:4]
        summary_text = _truncate_words(" ".join(lead) or context[:400])

        return {
            "summary_text": summary_text,
            "chief_concern": impressions[0].rstrip(".") if impressions else "",
            "key_diagnoses": diagnoses,
            "recent_media": media,
            "anomalies": anomalies,
        }


class OpenAICompatSummarizer:
    """Chat-completions client for OpenAI and OpenAI-compatible endpoints.

    Works with: OpenAI (default base_url), Gemini
    (https://generativelanguage.googleapis.com/v1beta/openai/), OpenRouter,
    Groq, Mistral, Ollama, GitHub Models — set LLM_BASE_URL + LLM_MODEL.
    """

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        from openai import OpenAI
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self.name = model
        self._model = model

    def summarize(self, context: str) -> dict:
        from openai import RateLimitError

        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                resp = self._client.chat.completions.create(
                    model=self._model,
                    temperature=0.2,
                    max_tokens=800,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_user_prompt(context)},
                    ],
                )
                raw = resp.choices[0].message.content or ""
                return _parse_model_json(raw)
            except RateLimitError as exc:
                last_exc = exc
                # Honor the provider's retry hint when present, else backoff
                delay = 20 * (attempt + 1)
                m = re.search(r"retry in ([\d.]+)s", str(exc), re.IGNORECASE)
                if m:
                    delay = float(m.group(1)) + 2
                log.warning("Rate limited (attempt %d/5); sleeping %.0fs",
                            attempt + 1, delay)
                time.sleep(delay)
        raise last_exc  # type: ignore[misc]


class ClaudeSummarizer:
    """Anthropic Claude via the official SDK."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5"):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self.name = model
        self._model = model

    def summarize(self, context: str) -> dict:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            temperature=0.2,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(context)}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return _parse_model_json(raw)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

# provider -> (env var holding the API key, default base_url or None)
_PROVIDER_DEFAULTS = {
    "openai": ("OPENAI_API_KEY", None),
    "gemini": ("GOOGLE_API_KEY",
               "https://generativelanguage.googleapis.com/v1beta/openai/"),
    "openai_compat": ("LLM_API_KEY", None),
}


def get_summarizer(provider: str | None = None) -> Summarizer:
    """Resolve the configured summarizer. Falls back to Mock with a warning.

    The model name always comes from LLM_MODEL in .env — no built-in defaults.
    """
    provider = (provider or config.LLM_PROVIDER).lower()
    if provider == "mock":
        return MockSummarizer()

    model = config.LLM_MODEL
    if not model:
        log.warning("LLM_MODEL is not set in .env; falling back to MockSummarizer")
        return MockSummarizer()

    if provider == "anthropic":
        if not config.ANTHROPIC_API_KEY:
            log.warning("EHR_LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty; "
                        "falling back to MockSummarizer")
            return MockSummarizer()
        return ClaudeSummarizer(api_key=config.ANTHROPIC_API_KEY, model=model)

    # All other providers go through the OpenAI-compatible client
    key_name, default_base = _PROVIDER_DEFAULTS.get(provider, ("OPENAI_API_KEY", None))
    api_key = getattr(config, key_name, "") or config.OPENAI_API_KEY or config.GOOGLE_API_KEY
    if not api_key:
        log.warning("No API key for provider '%s'; falling back to MockSummarizer", provider)
        return MockSummarizer()

    base_url = config.LLM_BASE_URL or default_base
    return OpenAICompatSummarizer(api_key=api_key, model=model, base_url=base_url)
