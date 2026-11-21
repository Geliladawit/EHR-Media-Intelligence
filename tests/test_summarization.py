"""Tests for Task 3: AI-powered clinical summarization."""

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ehr_media.summarization.cache import SummaryCache
from ehr_media.summarization.service import (
    extract_patient_context,
    iter_bundles,
    summarize_patient,
)
from ehr_media.summarization.summarizer import (
    ClinicalSummary,
    MockSummarizer,
    _parse_model_json,
    _truncate_words,
    get_summarizer,
)
from ehr_media.fhir.bundles import build_all_bundles
from ehr_media.ingestion.pipeline import ingest_raw
from ehr_media.ingestion.models import RawRecord


def make_bundle():
    raws = [
        RawRecord.model_validate({
            "record_id": "R1", "mrn": "MRN-1", "patient_name": "Jane Doe",
            "dob": "1985-03-14", "gender": "F", "record_type": "lab",
            "record_date": "2024-01-15", "encounter_id": "ENC-1",
            "content": "CBC: WBC 11.8 (H), Hgb 10.2 (L). Mild anemia."}),
        RawRecord.model_validate({
            "record_id": "R2", "mrn": "MRN-1", "patient_name": "Jane Doe",
            "dob": "1985-03-14", "gender": "F", "record_type": "imaging",
            "record_date": "2024-01-20", "encounter_id": "ENC-1",
            "content": "Chest X-ray: no acute infiltrate. Mild cardiomegaly."}),
        RawRecord.model_validate({
            "record_id": "R3", "mrn": "MRN-1", "patient_name": "Jane Doe",
            "dob": "1985-03-14", "gender": "F", "record_type": "discharge_summary",
            "record_date": "2024-01-25", "encounter_id": "",
            "content": "Admitted with chest pain, ruled out MI. Started on atorvastatin."}),
    ]
    batch = ingest_raw(raws)
    bundles, errors = build_all_bundles(batch)
    assert errors == []
    return next(iter(bundles.values()))


@pytest.fixture
def bundle():
    return make_bundle()


# --- Context extraction -------------------------------------------------------

def test_extract_context_decodes_records(bundle):
    header, records_text, record_hash = extract_patient_context(bundle)
    assert "Jane Doe" in header
    assert "lab" in records_text.lower()
    assert "Chest X-ray" in records_text
    assert len(record_hash) == 64
    # sorted newest first
    assert records_text.index("2024-01-25") < records_text.index("2024-01-15")


def test_record_hash_changes_with_content(bundle):
    _, _, h1 = extract_patient_context(bundle)
    # tamper one attachment's decoded text -> different hash
    for e in bundle["entry"]:
        res = e["resource"]
        if res.get("resourceType") == "DocumentReference":
            att = res["content"][0]["attachment"]
            att["data"] = base64.b64encode(b"totally different text").decode()
            break
    _, _, h2 = extract_patient_context(bundle)
    assert h1 != h2


# --- Summarizer implementations ----------------------------------------------

def test_mock_summarizer_schema(bundle):
    mock = MockSummarizer()
    header, records_text, _ = extract_patient_context(bundle)
    result = mock.summarize(f"{header}\n\n{records_text}")
    for field in ("summary_text", "chief_concern", "key_diagnoses",
                  "recent_media", "anomalies"):
        assert field in result
    assert len(result["summary_text"].split()) <= 200


def test_parse_model_json_tolerates_fences():
    raw = '```json\n{"summary_text": "hi", "chief_concern": "x", "key_diagnoses": [], "recent_media": [], "anomalies": []}\n```'
    parsed = _parse_model_json(raw)
    assert parsed["summary_text"] == "hi"


def test_parse_model_json_rejects_non_json():
    with pytest.raises(Exception):
        _parse_model_json("no json here at all")


def test_truncate_words_sentence_boundary():
    text = "First sentence. " + "word " * 300 + "Last sentence."
    out = _truncate_words(text, 50)
    assert len(out.split()) <= 50


def test_get_summarizer_fallback_without_key(monkeypatch):
    monkeypatch.setattr("ehr_media.config.LLM_PROVIDER", "anthropic")
    monkeypatch.setattr("ehr_media.config.ANTHROPIC_API_KEY", "")
    s = get_summarizer()
    assert isinstance(s, MockSummarizer)


def test_get_summarizer_gemini_uses_compat_endpoint(monkeypatch):
    monkeypatch.setattr("ehr_media.config.LLM_PROVIDER", "gemini")
    monkeypatch.setattr("ehr_media.config.GOOGLE_API_KEY", "fake-key")
    monkeypatch.setattr("ehr_media.config.LLM_MODEL", "gemini-test-model")
    monkeypatch.setattr("ehr_media.config.LLM_BASE_URL", "")
    s = get_summarizer()
    from ehr_media.summarization.summarizer import OpenAICompatSummarizer
    assert isinstance(s, OpenAICompatSummarizer)
    assert s.name == "gemini-test-model"
    assert "generativelanguage" in s._client.base_url.host


def test_get_summarizer_no_model_falls_back_to_mock(monkeypatch):
    monkeypatch.setattr("ehr_media.config.LLM_PROVIDER", "gemini")
    monkeypatch.setattr("ehr_media.config.GOOGLE_API_KEY", "fake-key")
    monkeypatch.setattr("ehr_media.config.LLM_MODEL", "")
    assert isinstance(get_summarizer(), MockSummarizer)


# --- Cache behavior ------------------------------------------------------------

def test_cache_hit_skips_summarizer(bundle, tmp_path):
    cache = SummaryCache(tmp_path / "t.db")
    mock = MockSummarizer()
    calls = []

    class Spy(MockSummarizer):
        def summarize(self, context):
            calls.append(1)
            return super().summarize(context)

    spy = Spy()
    s1, cached1 = summarize_patient(bundle, "MRN:MRN1", spy, cache)
    s2, cached2 = summarize_patient(bundle, "MRN:MRN1", spy, cache)

    assert not cached1 and cached2
    assert len(calls) == 1  # second call served from cache
    assert s1.record_hash == s2.record_hash
    cache.close()


def test_cache_miss_on_changed_records(bundle, tmp_path):
    cache = SummaryCache(tmp_path / "t.db")
    spy_calls = []

    class Spy(MockSummarizer):
        def summarize(self, context):
            spy_calls.append(1)
            return super().summarize(context)

    spy = Spy()
    summarize_patient(bundle, "MRN:MRN1", spy, cache)

    # corrupt a record -> new hash -> regeneration
    for e in bundle["entry"]:
        res = e["resource"]
        if res.get("resourceType") == "DocumentReference":
            att = res["content"][0]["attachment"]
            att["data"] = base64.b64encode(b"new clinical content").decode()
            break
    _, cached = summarize_patient(bundle, "MRN:MRN1", spy, cache)
    assert not cached
    assert len(spy_calls) == 2
    cache.close()


def test_force_bypasses_cache(bundle, tmp_path):
    cache = SummaryCache(tmp_path / "t.db")
    spy_calls = []

    class Spy(MockSummarizer):
        def summarize(self, context):
            spy_calls.append(1)
            return super().summarize(context)

    spy = Spy()
    summarize_patient(bundle, "MRN:MRN1", spy, cache)
    _, cached = summarize_patient(bundle, "MRN:MRN1", spy, cache, force=True)
    assert not cached and len(spy_calls) == 2
    cache.close()


# --- Summary schema ------------------------------------------------------------

def test_summary_has_disclaimer(bundle, tmp_path):
    cache = SummaryCache(tmp_path / "t.db")
    summary, _ = summarize_patient(bundle, "MRN:MRN1", MockSummarizer(), cache)
    assert "AI-generated" in summary.disclaimer
    assert summary.model == "mock-v1"
    assert summary.record_hash
    cache.close()


def test_summary_roundtrips_through_cache(bundle, tmp_path):
    cache = SummaryCache(tmp_path / "t.db")
    s1, _ = summarize_patient(bundle, "MRN:MRN1", MockSummarizer(), cache)
    cached = cache.get("MRN:MRN1", s1.record_hash)
    s2 = ClinicalSummary.model_validate(cached)
    assert s2.summary_text == s1.summary_text
    assert s2.disclaimer == s1.disclaimer
    cache.close()
