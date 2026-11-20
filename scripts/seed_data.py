"""Generate synthetic messy EHR export fixtures.

Creates ~50 patients with 2-5 clinical records each, writes ~60% to
data/samples/ehr_export.json and the rest to data/samples/clinical_notes.csv,
then injects deliberate data-quality bugs (duplicates, casing drift,
blank fields, conflicting identifiers) for the ingestion pipeline to fix.

Reproducible: fixed seeds.

Usage: python scripts/seed_data.py
"""

from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

random.seed(42)
Faker.seed(42)
fake = Faker()

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "samples"
N_PATIENTS = 50

# ---------------------------------------------------------------------------
# Clinical content templates — realistic, varied text for semantic search
# ---------------------------------------------------------------------------

LAB_TEMPLATES = [
    "CBC: WBC {wbc} ({wbc_flag}), Hgb {hgb} ({hgb_flag}), Plt {plt}. Findings consistent with {lab_dx}.",
    "BMP: Na {na}, K {k} ({k_flag}), Cr {cr} ({cr_flag}), BUN {bun}. Renal function {renal}.",
    "Lipid panel: LDL {ldl} ({ldl_flag}), HDL {hdl}, TG {tg}. {lipid_note}",
    "HbA1c {hba1c}% ({hba1c_flag}). Fasting glucose {glucose}. {dm_note}",
    "Troponin I {trop} ng/mL ({trop_flag}). CK-MB {ckmb}. Serial troponins {trop_trend}.",
    "TSH {tsh} ({tsh_flag}), free T4 {ft4}. Thyroid function {thyroid_note}.",
    "INR {inr} on warfarin. Therapeutic range 2-3. Dose adjustment {warfarin_note}.",
    "Urinalysis: {ua}. Nitrites {nitrite}, leukocyte esterase {le}.",
    "Blood cultures: {culture}. Preliminary gram stain {gram}.",
    "BNP {bnp} pg/mL ({bnp_flag}). Consistent with {bnp_dx}.",
]

IMAGING_TEMPLATES = [
    "Chest X-ray: {cxr_finding}. {cxr_impression}",
    "CT chest without contrast: {ct_finding}. Impression: {ct_impression}",
    "MRI brain without contrast: {mri_finding}. No acute intracranial abnormality: {mri_yesno}.",
    "Abdominal ultrasound: {us_finding}. {us_recommendation}",
    "Echocardiogram: EF {ef}%. {echo_finding}.",
    "Mammogram bilateral screening: {mammo}. BI-RADS {birads}.",
    "DEXA scan: T-score {dex} at lumbar spine. {dex_dx}.",
]

DISCHARGE_TEMPLATES = [
    "Admitted with {dx} on {admit_date}. Hospital course notable for {course}. Discharged on {meds}. Follow up with {fup_dept} in {fup_weeks} weeks.",
    "72yo {sex} admitted for {dx}. Ruled out {ruled_out}. Treated with {tx}. Discharge instructions reviewed. PCP follow-up in {fup_weeks} weeks.",
    "Presented with {sx}. Workup revealed {finding}. Started on {meds}. Stable for discharge. Return precautions discussed.",
    "Elective admission for {proc}. Procedure tolerated well. Post-op course {postop}. Discharged home with {meds}.",
]

NOTE_TEMPLATES = [
    "Progress note: {sex} with {dx} reports {sx}. Exam notable for {exam}. Plan: {plan}.",
    "Consult note: {dept} consulted for {reason}. Recommendations: {rec}.",
    "H&P: {age}yo {sex} presents with {sx}. PMH significant for {pmh}. Medications include {meds}.",
    "Nursing note: VS stable. Patient {nursing_status}. Pain score {pain}/10.",
    "Phone encounter: patient calls re: {topic}. Advised {advice}. Will f/u PRN.",
    "Procedure note: {proc} performed under {anesthesia}. Estimated blood loss {ebl} mL. Tolerated well.",
]

DIAGNOSES = [
    "type 2 diabetes mellitus", "essential hypertension", "hyperlipidemia",
    "chronic kidney disease stage 3", "heart failure with reduced ejection fraction",
    "COPD", "asthma", "osteoarthritis", "major depressive disorder",
    "GERD", "atrial fibrillation", "hypothyroidism", "anemia",
    "community-acquired pneumonia", "UTI", "acute coronary syndrome ruled out",
    "cellulitis", "diverticulitis", "cholelithiasis", "renal colic",
]
MEDICATIONS = [
    "metformin 500mg BID", "lisinopril 10mg daily", "atorvastatin 20mg nightly",
    "metoprolol succinate 25mg daily", "levothyroxine 75mcg daily",
    "albuterol inhaler PRN", "sertraline 50mg daily", "omeprazole 20mg daily",
    "warfarin 5mg daily", "insulin glargine 12 units nightly", "aspirin 81mg daily",
]
PROVIDERS = [
    "Dr. Sarah Chen, MD", "Dr. James Okafor, MD", "Dr. Maria Gonzalez, MD",
    "Dr. Robert Kim, MD", "NP Amanda Torres", "Dr. David Patel, MD",
    "Dr. Lisa Wong, MD", "PA Kevin Brooks",
]


def messy_date(d: date) -> str:
    fmt = random.choice(["%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%Y/%m/%d", "%d %b %Y"])
    return d.strftime(fmt)


def messy_gender(g: str) -> str:
    variants = {
        "M": ["M", "male", "Male", "m", ""],
        "F": ["F", "Female", "f", "female", ""],
    }
    return random.choice(variants[g])


def messy_mrn(n: int) -> str:
    return random.choice([f"MRN-{n:06d}", f"mrn-{n}", f"MRN {n:05d}", f"{n:07d}"])


def messy_phone() -> str:
    return random.choice([
        f"({fake.numerify('###')}) {fake.numerify('###-####')}",
        fake.numerify("##########"),
        f"{fake.numerify('###')}.{fake.numerify('###')}.{fake.numerify('####')}",
    ])


def messy_name(first: str, last: str) -> str:
    style = random.choice(["normal", "upper", "lower", "last_comma_first"])
    if style == "upper":
        return f"{first} {last}".upper()
    if style == "lower":
        return f"{first} {last}".lower()
    if style == "last_comma_first":
        return f"{last}, {first}"
    return f"{first} {last}"


def fill(template: str) -> str:
    """Fill a clinical template with random realistic values."""
    vals = {
        "wbc": f"{random.uniform(3.5, 15.0):.1f}",
        "wbc_flag": random.choice(["normal", "H", "L"]),
        "hgb": f"{random.uniform(8.5, 16.5):.1f}",
        "hgb_flag": random.choice(["normal", "H", "L"]),
        "plt": f"{random.uniform(120, 450):.0f}",
        "lab_dx": random.choice(["mild anemia", "leukocytosis", "normal study", "iron deficiency", "thrombocytopenia"]),
        "na": f"{random.uniform(135, 145):.0f}", "k": f"{random.uniform(3.2, 5.8):.1f}",
        "k_flag": random.choice(["normal", "H", "L"]),
        "cr": f"{random.uniform(0.6, 2.5):.2f}", "cr_flag": random.choice(["normal", "H", "L"]),
        "bun": f"{random.uniform(8, 40):.0f}",
        "renal": random.choice(["within normal limits", "mildly elevated", "stable CKD", "improved from baseline"]),
        "ldl": f"{random.uniform(70, 190):.0f}", "ldl_flag": random.choice(["optimal", "borderline", "high"]),
        "hdl": f"{random.uniform(35, 80):.0f}", "tg": f"{random.uniform(80, 400):.0f}",
        "lipid_note": random.choice(["Consistent with mixed dyslipidemia.", "At goal on current therapy.", "Recommend statin intensification."]),
        "hba1c": f"{random.uniform(5.2, 10.5):.1f}", "hba1c_flag": random.choice(["normal", "H", "critically high"]),
        "glucose": f"{random.uniform(80, 280):.0f}",
        "dm_note": random.choice(["Above target; adjust regimen.", "At goal.", "New diagnosis; start metformin."]),
        "trop": f"{random.uniform(0.01, 12.0):.2f}", "trop_flag": random.choice(["normal", "H", "significantly elevated"]),
        "ckmb": f"{random.uniform(1, 30):.1f}",
        "trop_trend": random.choice(["downtrending", "stable", "rising — concerning for ongoing ischemia"]),
        "tsh": f"{random.uniform(0.4, 8.0):.2f}", "tsh_flag": random.choice(["normal", "H", "L"]),
        "ft4": f"{random.uniform(0.8, 2.0):.1f}",
        "thyroid_note": random.choice(["Suggestive of hypothyroidism.", "Within normal limits.", "Hyperthyroid pattern."]),
        "inr": f"{random.uniform(1.5, 3.8):.1f}",
        "warfarin_note": random.choice(["therapeutic, continue dose.", "supratherapeutic — hold one dose.", "subtherapeutic — increase weekly dose."]),
        "ua": random.choice(["clear yellow, specific gravity 1.020", "cloudy, trace leukocytes", "amber, moderate bacteria"]),
        "nitrite": random.choice(["negative", "positive"]),
        "le": random.choice(["negative", "positive"]),
        "culture": random.choice(["no growth to date", "gram-negative rods in 1 of 2 bottles", "pending — final at 5 days"]),
        "gram": random.choice(["gram-positive cocci", "gram-negative bacilli", "no organisms seen"]),
        "bnp": f"{random.uniform(20, 1800):.0f}", "bnp_flag": random.choice(["normal", "H", "markedly elevated"]),
        "bnp_dx": random.choice(["heart failure exacerbation", "volume overload", "likely pulmonary process"]),
        "cxr_finding": random.choice(["no acute cardiopulmonary process", "mild cardiomegaly", "right lower lobe infiltrate", "bibasilar atelectasis", "clear lungs"]),
        "cxr_impression": random.choice(["No acute findings.", "Follow-up recommended.", "Correlate clinically.", "Stable compared to prior."]),
        "ct_finding": random.choice(["no pulmonary embolism", "small right lower lobe nodule 4mm", "bibasilar ground-glass opacities", "unremarkable study"]),
        "ct_impression": random.choice(["No acute abnormality.", "Nodule too small to characterize; recommend 12-month follow-up CT.", "Findings suggest atypical infection."]),
        "mri_finding": random.choice(["unremarkable", "chronic small vessel ischemic changes", "left parietal meningioma, stable", "no restricted diffusion"]),
        "mri_yesno": random.choice(["true", "false"]),
        "us_finding": random.choice(["gallstones without cholecystitis", "normal study", "hepatic cyst, benign appearance", "right hydronephrosis"]),
        "us_recommendation": random.choice(["Surgical consult if symptomatic.", "No follow-up needed.", "Repeat in 6 months."]),
        "ef": f"{random.uniform(25, 70):.0f}",
        "echo_finding": random.choice(["moderately reduced systolic function", "normal chamber sizes", "grade II diastolic dysfunction", "mild LVH"]),
        "mammo": random.choice(["no suspicious findings", "architectural distortion upper outer quadrant", "dense breasts, negative screening"]),
        "birads": random.choice(["1", "2", "4A"]),
        "dex": f"{-random.uniform(0.5, 3.5):.1f}",
        "dex_dx": random.choice(["consistent with osteoporosis", "osteopenia", "within normal limits for age"]),
        "dx": random.choice(DIAGNOSES), "sx": random.choice(["chest pain", "shortness of breath", "fever and cough", "abdominal pain", "headache", "syncope", "leg swelling", "fatigue"]),
        "admit_date": (date.today() - timedelta(days=random.randint(1, 30))).isoformat(),
        "course": random.choice(["diuresis with symptomatic improvement", "antibiotics with resolution of fever", "rate control of AFib", "transfusion of 2 units PRBC", "uncomplicated recovery"]),
        "meds": random.choice(MEDICATIONS) + " and " + random.choice(MEDICATIONS),
        "fup_dept": random.choice(["cardiology", "primary care", "pulmonology", "endocrinology", "surgery"]),
        "fup_weeks": random.choice(["1", "2", "4", "6"]),
        "ruled_out": random.choice(["MI", "PE", "appendicitis", "pneumonia", "DKA", "fracture"]),
        "tx": random.choice(["IV diuresis", "antibiotics", "anticoagulation", "insulin infusion", "narcotic pain control"]),
        "proc": random.choice(["laparoscopic cholecystectomy", "cataract extraction", "colonoscopy", "right heart catheterization", "total knee replacement"]),
        "postop": random.choice(["uncomplicated", "with mild ileus resolving", "with expected pain controlled on oral meds"]),
        "dept": random.choice(["Cardiology", "Nephrology", "Endocrinology", "Infectious Disease", "GI", "Neurology"]),
        "reason": random.choice(["evaluation of chest pain", "renal function decline", "glycemic control", "fever workup", "altered mental status", "electrolyte derangement"]),
        "rec": random.choice(["continue current management", "adjust diuretic dose", "repeat labs in 1 week", "schedule stress test", "refer to specialist clinic"]),
        "age": random.randint(22, 89),
        "sex": random.choice(["male", "female"]),
        "exam": random.choice(["lungs clear bilaterally", "mild peripheral edema", "abdomen soft, non-tender", "no focal neurologic deficits", "regular rate and rhythm"]),
        "plan": random.choice(["continue current meds, recheck labs in 2 weeks", "start new medication, f/u in 1 month", "order follow-up imaging", "refer to cardiology", "discharge with precautions"]),
        "pmh": random.choice(["HTN, DM2, hyperlipidemia", "COPD, former smoker", "hypothyroidism", "CKD stage 3", "s/p MI 2021", "asthma, allergic rhinitis"]),
        "nursing_status": random.choice(["resting comfortably", "ambulating independently", "anxious but cooperative", "sleeping"]),
        "pain": random.randint(0, 8),
        "topic": random.choice(["medication refill", "lab results", "appointment scheduling", "wound care", "symptom question"]),
        "advice": random.choice(["continue current regimen", "come in for evaluation", "hold medication until seen", "increase fluid intake"]),
        "anesthesia": random.choice(["local with sedation", "spinal", "general anesthesia"]),
        "ebl": random.randint(10, 300),
    }
    out = template
    for k, v in vals.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def make_record(patient: dict, idx: int, rec_date: date, record_type: str) -> dict:
    encounter_id = f"ENC-{random.randint(1000, 9999)}" if random.random() > 0.08 else ""
    record_id = f"REC-{patient['seq']}-{idx}"
    if record_type == "lab":
        content = fill(random.choice(LAB_TEMPLATES))
    elif record_type == "imaging":
        content = fill(random.choice(IMAGING_TEMPLATES))
    elif record_type == "discharge_summary":
        content = fill(random.choice(DISCHARGE_TEMPLATES))
    else:
        content = fill(random.choice(NOTE_TEMPLATES))
    return {
        "record_id": record_id,
        "mrn": patient["mrn_raw"],
        "patient_name": patient["name_raw"],
        "dob": patient["dob_raw"],
        "gender": patient["gender_raw"],
        "phone": patient["phone"],
        "record_type": record_type,
        "record_date": messy_date(rec_date),
        "encounter_id": encounter_id,
        "author": random.choice(PROVIDERS),
        "content": content,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_records: list[dict] = []
    patients_meta: list[dict] = []

    for i in range(1, N_PATIENTS + 1):
        gender = random.choice(["M", "F"])
        first, last = fake.first_name(), fake.last_name()
        dob = date.today() - timedelta(days=random.randint(18 * 365, 90 * 365))
        patients_meta.append({
            "seq": i,
            "mrn_raw": messy_mrn(i + 10000),
            "name_raw": messy_name(first, last),
            "dob_raw": messy_date(dob),
            "gender_raw": messy_gender(gender),
            "phone": messy_phone(),
        })

    # Each patient gets 2-5 records over the past year
    for p in patients_meta:
        n = random.randint(2, 5)
        for j in range(n):
            rec_date = date.today() - timedelta(days=random.randint(1, 365))
            rtype = random.choices(
                ["lab", "imaging", "discharge_summary", "clinical_note"],
                weights=[0.35, 0.25, 0.15, 0.25])[0]
            all_records.append(make_record(p, j, rec_date, rtype))

    # --- Inject cross-file identity drift on ~5 patients ---
    drift_patients = random.sample(patients_meta, 5)
    for p in drift_patients:
        p["name_variant"] = p["name_raw"].upper() if random.random() < 0.5 else p["name_raw"].lower()

    # ~5 exact duplicate records
    dup_sources = random.sample(all_records, 5)
    duplicates = [dict(r) for r in dup_sources]

    # Split ~60/40 JSON/CSV; ensure some drift patients appear in both files
    json_records = [r for r in all_records if random.random() < 0.6]
    csv_records = [r for r in all_records if r not in json_records]

    # Add a few CSV-only records for drift patients with variant name + phone
    for p in drift_patients[:3]:
        for j in range(random.randint(1, 2)):
            rec_date = date.today() - timedelta(days=random.randint(1, 365))
            rtype = random.choice(["lab", "imaging", "clinical_note"])
            rec = make_record(p, 90 + j, rec_date, rtype)
            rec["patient_name"] = p.get("name_variant", p["name_raw"])
            rec["phone"] = messy_phone()
            rec["record_id"] = f"REC-{p['seq']}-CSV{j}"
            csv_records.append(rec)

    # Blank out a few fields deliberately (encounter_id already ~8% blank;
    # add blank genders and a couple blank notes)
    for r in random.sample(all_records, 8):
        if r["gender"] != "":
            r["gender"] = ""
    for r in random.sample(all_records, 4):
        r["content"] = ""
    for r in random.sample(all_records, 3):
        r["mrn"] = ""

    # Inject duplicates into CSV file
    csv_records.extend(random.sample(duplicates, min(3, len(duplicates))))
    extra_dups = [dict(r) for r in random.sample(all_records, 2)]
    json_records.extend(extra_dups)

    # Write JSON export
    json_path = OUT_DIR / "ehr_export.json"
    json_path.write_text(json.dumps({"records": json_records}, indent=2), encoding="utf-8")

    # Write CSV export
    csv_path = OUT_DIR / "clinical_notes.csv"
    fieldnames = ["record_id", "mrn", "patient_name", "dob", "gender", "phone",
                  "record_type", "record_date", "encounter_id", "author", "content"]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in csv_records:
            writer.writerow(r)

    total = len(json_records) + len(csv_records)
    print(f"Wrote {len(json_records)} records -> {json_path}")
    print(f"Wrote {len(csv_records)} records -> {csv_path}")
    print(f"Total raw records: {total} | patients: {N_PATIENTS} | injected duplicates: {len(duplicates)}")


if __name__ == "__main__":
    main()
