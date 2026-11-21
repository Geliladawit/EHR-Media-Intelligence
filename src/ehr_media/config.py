"""Central configuration: paths, env vars, model settings."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
DB_PATH = ROOT / "ehr_media.db"

# LLM provider: "anthropic" | "openai" | "gemini" | "openai_compat" | "mock"
LLM_PROVIDER = os.getenv("EHR_LLM_PROVIDER", "mock").lower()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")

# Optional overrides for OpenAI-compatible endpoints (OpenRouter, Groq,
# Ollama, Gemini's OpenAI-compat endpoint, etc.)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "") or os.getenv("OPENAI_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "") or os.getenv("OPENAI_MODEL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
