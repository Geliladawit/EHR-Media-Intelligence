"""Request/response schemas for the search API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    resource_type: str | None = None
    date_from: str | None = None   # YYYY-MM-DD
    date_to: str | None = None     # YYYY-MM-DD
    top_k: int = Field(default=5, ge=1, le=50)


class SearchResult(BaseModel):
    patient_key: str
    patient_name: str | None = None
    mrn: str | None = None
    record_id: str
    resource_type: str
    record_date: str
    score: float
    snippet: str


class SearchResponse(BaseModel):
    query: str
    count: int
    results: list[SearchResult]


class PatientSummary(BaseModel):
    patient_key: str
    summary_text: str
    chief_concern: str = ""
    key_diagnoses: list[str] = Field(default_factory=list)
    recent_media: list[str] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)
    disclaimer: str = ""
    model: str = ""


class PatientResource(BaseModel):
    resource_type: str
    record_id: str | None = None
    record_date: str | None = None
    resource_type_label: str | None = None
    content: str | None = None
    author: str | None = None
