# EHR Media Intelligence Platform

An AI powered pipeline that ingests messy, unstructured EHR media records (lab PDFs' text,
imaging notes, discharge summaries, scanned note CSV exports), normalizes them to
**HL7 FHIR R4**, generates **LLM clinical summaries**, and exposes a **semantic
search UI** so clinicians can find relevant records in seconds.

<img width="600" height="300" alt="Screenshot 2026-11-21 135741" src="https://github.com/user-attachments/assets/2b5aa367-7de1-4945-903d-4036028a53c3" />

<img width="600" height="400" alt="Screenshot 2026-11-21 135852" src="https://github.com/user-attachments/assets/b8ddbf31-66f6-44cb-8e47-2fc9593fd346" />

<img width="600" height="300" alt="Screenshot 2026-11-21 140002" src="https://github.com/user-attachments/assets/a6c67a53-a464-462d-a3a4-e178ee33b60c" />

## Architecture

```
raw exports (JSON / CSV / text)
        │  Task 1 - ingestion & cleaning
        ▼
CleanBatch (Pydantic IR + per record audit log)
        │  Task 2 - FHIR R4 normalization
        ▼
FHIR Bundles (Patient, Encounter, DocumentReference, DiagnosticReport)
        │        stored in SQLite (ehr_media.db)
        ├── Task 3 - LLM summarization (cached by record hash)
        │        ▼
        │   ClinicalSummary per patient (SQLite summaries table)
        └── Task 4 - embeddings + FAISS vector search
                 ▼
        POST /search + patient endpoints  ──►  Task 5 - Tailwind UI
```

## Quick start

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
pip install -e .                                # makes ehr_media importable

cp .env.example .env        # then fill in your keys (see below)
python scripts/seed_data.py # regenerate sample data (optional, outputs already generated)
python scripts/serve.py     # serves API + UI at http://127.0.0.1:8000
# equivalent: uvicorn ehr_media.api.main:app --port 8000
```

The FastAPI app auto-bootstraps on startup: if `ehr_media.db` or the vector
index is missing, it runs the full pipeline (ingest → FHIR → summaries → index)
so a fresh clone works with a single command. Summaries use your configured LLM
provider, or a deterministic mock if no key is set.

### `.env` variables

```env
EHR_LLM_PROVIDER=gemini        # anthropic | openai | gemini | openai_compat | mock
GOOGLE_API_KEY=                # Gemini key (AI Studio) - used when provider=gemini
OPENAI_API_KEY=                # OpenAI key - used when provider=openai/openai_compat
ANTHROPIC_API_KEY=             # Anthropic key - used when provider=anthropic
LLM_MODEL=                     # REQUIRED for any real provider, e.g. I used - gemini-3.5-flash-lite.
                               # No built in defaults - unset => MockSummarizer.
```

`gemini` routes through Google's OpenAI compatible endpoint
(`https://generativelanguage.googleapis.com/v1beta/openai/`), so one client
covers Gemini, OpenAI, OpenRouter, Groq, Mistral, Ollama and GitHub Models.

## Pipeline scripts

| Command | What it does |
|---|---|
| `python scripts/seed_data.py` | Generate ~50 synthetic patients / ~190 messy records (seeded, reproducible) into `data/samples/` |
| `python scripts/run_pipeline.py` | Full chain: ingest → clean → FHIR bundles → summaries → index |
| `python scripts/build_fhir.py` | Clean IR → FHIR Bundles → SQLite + validation report |
| `python scripts/build_summaries.py [--force] [--delay N]` | Generate/cache LLM summaries (`--force` ignores cache) |
| `python scripts/build_index.py [--rebuild]` | (Re)build FAISS index from bundles |

## Tests

```bash
pytest            # 56 tests: cleaning edge cases, FHIR validity, cache behavior, search ranking/filters/latency
```

## Design decisions & tradeoffs

- **FAISS `IndexIDMap(IndexFlatIP)` + SQLite metadata** over ChromaDB: FAISS ids are
  bound to `embeddings_meta.id` primary keys, giving a deterministic vector↔metadata
  join that survives re-indexing; ChromaDB would hide this mapping and add a heavier dep.
- **Per record embeddings** (`"{type} | {date} | {clinical text}"`), not per patient:
  required for `resource_type`/date filters to be precise - a patient level summary
  appended to every record would let an unrelated note match on another record's content.
  The AI summary is exposed separately via `GET /patients/{key}`.
- **`fhir.resources==7.1.0` with `fhir.resources.R4B` imports**: v6.x is pydantic-v1-only
  and cannot coexist with the mandated Pydantic v2; the R4B namespace is the R4-conformant
  package in 7.x. Validation uses the library's `.validate()` classmethod; all errors are
  collected into `data/fhir/validation_report.json` rather than raising.
- **Encounter grouping**: records sharing an `encounter_id` map to one Encounter;
  blank IDs get a deterministic `AUTO-{patient}-{date}` key so same day records group.
- **Mock summarizer default**: the app is fully runnable without API keys; the provider
  is env configured and the LLM call is isolated behind a `Summarizer` protocol.
- **Summary cache keyed by `record_hash`** (sha256 over sorted per record content
  hashes): re-runs on unchanged data make zero API calls; any record change
  invalidates only that patient.
- **Rate limit resilience**: the OpenAI compatible client retries 429s honoring the
  provider's `retryDelay`, and `build_summaries.py --delay` paces requests for free tiers.
- **Tailwind CDN + vanilla JS**: zero build tooling for reviewers; all clinical text is
  rendered via `textContent` (no HTML injection of untrusted record text).

## FHIR / clinical concepts researched

- **FHIR `date` vs `dateTime`**: `DocumentReference.date`, `Encounter.period.start`,
  and `DiagnosticReport.effectiveDateTime`/`issued` are `dateTime` and *require* a time
  component - date-only strings fail schema validation. `Patient.birthDate` is `date`.
- **`DocumentReference.context.encounter`** is a *list* of References (R4), whereas
  `DiagnosticReport.encounter` is a single Reference - an easy mapping mistake.
- **`DocumentReference.status="current"`** is required in R4.
- **`urn:uuid:` fullUrls** must be RFC-4122 UUIDs - arbitrary strings fail validation;
  `uuid.uuid5` gives deterministic, valid ids across runs.
- **Bundle `type="collection"`** for a per-patient document set; all references
  (`subject`, `encounter`, `context.encounter`) resolve to `fullUrl`s inside the bundle.
- **MRN as patient identifier**: canonicalized to alphanumerics; missing MRNs fall back
  to name+DOB keys (conservative - may split one human into two patients, noted in audit).

## How AI summary quality was validated

- Schema enforced output (chief concern, key diagnoses, recent media, anomalies) with a
  hard <200 word cap enforced in the prompt *and* defensively in code.
- Every summary carries a fixed disclaimer: "AI generated summary for informational
  purposes only. Not a clinical decision or medical advice."
- Spot checked generated summaries against source records: extracted chief concerns,
  diagnoses, and flagged abnormalities (e.g. troponin, BNP, HbA1c, blood cultures)
  matched the underlying notes; the mock summarizer's deterministic output is covered
  by unit tests while real provider output was reviewed manually.
- Cache correctness is tested: identical record hash → zero API calls; changed content
  → regeneration.

## Repository layout

```
src/ehr_media/
  ingestion/    Task 1: readers, cleaning rules, audit log, Pydantic IR
  fhir/         Task 2: R4 mappers, bundle assembly, SQLite store, validation report
  summarization/Task 3: prompts, provider factory (Claude/OpenAI compat/Gemini/Mock), cache
  search/       Task 4: embeddings, FAISS+SQLite store, indexer
  api/          FastAPI app, /search + /patients routes
  config.py     env driven settings
scripts/        seed_data, run_pipeline, build_fhir, build_summaries, build_index, serve
frontend/       Tailwind + vanilla JS UI
tests/          56 pytest tests
data/           samples/ (inputs), clean/ + fhir/ outputs (gitignored)
models/         all-MiniLM-L6-v2 weights - NOT committed (87MB); the loader
                auto-downloads from HuggingFace on first use, or place a
                manual copy at models/all-MiniLM-L6-v2/ to run offline
```

## Known limitations

- Synthetic data only - no real PHI anywhere in the repo.
- Name+DOB fallback can split patients when MRN is absent (by design, conservative).
- `DiagnosticReport` carries the narrative as `conclusion` rather than structured
  `Observation` components - acceptable for R4 base validation; US Core profiling
  would want coded observations.
- FAISS index is rebuilt from bundles; record level deletes aren't propagated yet.
