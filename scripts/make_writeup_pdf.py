"""Generate the 1-page submission write-up PDF.

Usage: python scripts/make_writeup_pdf.py
Output: docs/EHR_Media_Intelligence_Writeup.pdf
"""

from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "EHR_Media_Intelligence_Writeup.pdf"


_TRANS = str.maketrans({
    "—": "-", "–": "-", "’": "'", "‘": "'", "“": '"', "”": '"',
    "…": "...", "→": "->", "≥": ">=", "≤": "<=", "×": "x",
})


class Writeup(FPDF):
    def normalize_text(self, text):
        return super().normalize_text(text.translate(_TRANS))

    def header(self):
        self.set_font("helvetica", "B", 15)
        self.cell(0, 8, "EHR Media Intelligence — Submission Write-up", new_x="LMARGIN", new_y="NEXT")
        self.set_font("helvetica", "", 9)
        self.set_text_color(100)
        self.cell(0, 5, "AI pipeline: messy EHR exports -> FHIR R4 -> LLM summaries -> semantic search", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font("helvetica", "I", 8)
        self.set_text_color(140)
        self.cell(0, 8, f"Page {self.page_no()}/{{nb}}", align="C")

    def section(self, title):
        self.set_font("helvetica", "B", 11)
        self.set_text_color(20, 90, 90)
        self.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(30)

    def body(self, text):
        self.set_font("helvetica", "", 9.5)
        self.multi_cell(0, 4.6, text)
        self.ln(1)


def main() -> None:
    pdf = Writeup()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()

    pdf.section("What was built")
    pdf.body(
        "A five-stage Python pipeline: (1) ingestion of heterogeneous EHR exports "
        "(JSON, CSV, delimited text) into a validated Pydantic intermediate "
        "representation with a per-record audit log; (2) mapping to HL7 FHIR R4 "
        "(Patient, Encounter, DocumentReference, DiagnosticReport) assembled into "
        "per-patient Bundles and stored in SQLite; (3) LLM-generated clinical "
        "summaries with structured fields and a disclaimer, cached by record hash "
        "so re-runs cost zero API calls; (4) semantic search via "
        "all-MiniLM-L6-v2 embeddings in FAISS IndexIDMap joined to SQLite "
        "metadata, exposed as POST /search with resource-type and date filters; "
        "(5) a Tailwind + vanilla JS clinician UI with a patient detail drawer. "
        "56 pytest tests cover cleaning edge cases, FHIR validity, cache "
        "correctness, and search ranking/latency (~40ms warm)."
    )

    pdf.section("Tradeoffs made and why")
    pdf.body(
        "FAISS over ChromaDB: IndexIDMap binds vector ids to SQLite primary keys, "
        "giving a deterministic join that survives re-indexing with far less "
        "dependency weight. Per-record embeddings rather than per-patient: "
        "record-level resource_type/date filters stay precise, and the patient "
        "summary is fetched separately — avoids cross-record semantic pollution. "
        "fhir.resources 7.1.0 (R4B namespace) over 6.5.0: v6 is pydantic-v1-only "
        "and incompatible with the mandated Pydantic v2. LLM provider-agnostic "
        "design: a Summarizer protocol with Claude, OpenAI-compatible (covers "
        "Gemini/OpenRouter/Groq/Ollama), and deterministic mock implementations; "
        "used a free-tier Gemini key via Google's OpenAI-compatible endpoint "
        "because neither Claude nor OpenAI offers a usable free tier. Tailwind "
        "CDN over a build step to keep the frontend runnable with zero tooling."
    )

    pdf.section("What I would improve with more time")
    pdf.body(
        "Structured FHIR Observations for lab/vital results instead of narrative "
        "conclusions, and US Core profiling for real Epic interoperability. "
        "Incremental indexing on record change rather than rebuild, plus soft-"
        "delete propagation into the vector store. Query expansion / hybrid "
        "(BM25 + vector) retrieval for better recall on terse clinical queries. "
        "Auth and audit logging for real clinical deployment, OCR for scanned "
        "PDF media, and a proper frontend build (React + compiled Tailwind)."
    )

    pdf.section("FHIR and clinical concepts researched")
    pdf.body(
        "FHIR date vs dateTime: DocumentReference.date, Encounter.period.start and "
        "DiagnosticReport.effectiveDateTime require a time component — date-only "
        "strings fail R4 validation, while Patient.birthDate is a plain date. "
        "DocumentReference.context.encounter is a list of References whereas "
        "DiagnosticReport.encounter is singular; DocumentReference.status='current' "
        "is required. urn:uuid fullUrls must be RFC-4122 UUIDs — uuid5 gives "
        "deterministic valid ids. Bundle type='collection' with internally "
        "resolving subject/encounter references. Demographic normalization: MRN "
        "canonicalization, gender code mapping, and conservative name+DOB "
        "fallback identity resolution when MRN is absent."
    )

    pdf.section("How AI summary quality was validated")
    pdf.body(
        "Prompt engineering enforces the four required sections (chief concern, "
        "key diagnoses, recent imaging/labs, anomalies) with a hard sub-200-word "
        "cap, additionally enforced defensively in code. Every summary carries a "
        "fixed disclaimer that it is AI-generated and not a clinical decision. "
        "Generated summaries were spot-checked against source records: extracted "
        "concerns, diagnoses, and flagged abnormalities (troponin, BNP, HbA1c, "
        "blood cultures) matched the underlying notes. Cache correctness is unit-"
        "tested — identical record hashes produce zero API calls, changed content "
        "forces regeneration — and the deterministic mock summarizer covers schema "
        "compliance in CI without keys."
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUT))
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
