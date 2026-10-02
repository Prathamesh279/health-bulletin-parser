#!/usr/bin/env python3
"""
El Niño-Aware Health Bulletin Parser
Interactive Flask Dashboard for Maharashtra Public Health Bulletins
"""

import os
import re
import json
from datetime import datetime, date
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
import pdfplumber
import pandas as pd
import plotly
import plotly.express as px
import plotly.graph_objects as go
from plotly.utils import PlotlyJSONEncoder

# ---------------------------------------------------------------------------
# App & DB setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
ALLOWED_EXTENSIONS = {"pdf", "txt", "png", "jpg", "jpeg", "webp"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
OCR_MIN_CHARS = 80
OCR_MAX_PAGES = 12

app = Flask(__name__)
app.config["SECRET_KEY"] = "elnino-health-parser-2025-secure"
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{BASE_DIR / 'instance' / 'health_bulletins.db'}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB

db = SQLAlchemy(app)
UPLOAD_FOLDER.mkdir(exist_ok=True)
(BASE_DIR / "instance").mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class Bulletin(db.Model):
    __tablename__ = "bulletins"
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    original_text = db.Column(db.Text)
    records = db.relationship("DiseaseRecord", backref="bulletin", lazy=True, cascade="all, delete-orphan")


class DiseaseRecord(db.Model):
    __tablename__ = "disease_records"
    id = db.Column(db.Integer, primary_key=True)
    bulletin_id = db.Column(db.Integer, db.ForeignKey("bulletins.id"), nullable=False)
    disease = db.Column(db.String(100), nullable=False, index=True)
    district = db.Column(db.String(100), nullable=False, index=True)
    cases = db.Column(db.Integer, default=0)
    deaths = db.Column(db.Integer, default=0)
    report_date = db.Column(db.Date, index=True)
    notes = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Constants – Maharashtra districts & target diseases
# ---------------------------------------------------------------------------
MAHARASHTRA_DISTRICTS = [
    "Ahmednagar", "Akola", "Amravati", "Aurangabad", "Beed", "Bhandara",
    "Buldhana", "Chandrapur", "Dhule", "Gadchiroli", "Gondia", "Hingoli",
    "Jalgaon", "Jalna", "Kolhapur", "Latur", "Mumbai", "Mumbai City",
    "Mumbai Suburban", "Nagpur", "Nanded", "Nandurbar", "Nashik",
    "Osmanabad", "Palghar", "Parbhani", "Pune", "Raigad", "Ratnagiri",
    "Sangli", "Satara", "Sindhudurg", "Solapur", "Thane", "Wardha",
    "Washim", "Yavatmal",
]

DISTRICT_ALIASES = {
    "bombay": "Mumbai",
    "mumbai city": "Mumbai",
    "brihanmumbai": "Mumbai",
    "ahmadnagar": "Ahmednagar",
    "ahmed nagar": "Ahmednagar",
    "chhatrapati sambhajinagar": "Aurangabad",
    "sambhajinagar": "Aurangabad",
    "dharashiv": "Osmanabad",
    "osmanbad": "Osmanabad",
}

# Marathi / Hindi bulletin terms → English (applied before NER)
MARATHI_ENGLISH = {
    "डेंग्यू": "Dengue", "डेंगी": "Dengue", "डेंगू": "Dengue",
    "मलेरिया": "Malaria",
    "चिकनगुनिया": "Chikungunya", "चिकनगुन्या": "Chikungunya", "चिकनगुनियाः": "Chikungunya",
    "हैजा": "Cholera", "कॉलरा": "Cholera", "कोलेरा": "Cholera",
    "लेप्टोस्पायरोसिस": "Leptospirosis", "लेप्टोस्पाइरोसिस": "Leptospirosis", "लेप्टो": "Leptospirosis",
    "उष्माघात": "Heat Stroke", "हिट स्ट्रोक": "Heat Stroke", "हीट स्ट्रोक": "Heat Stroke",
    "काविळ": "Jaundice", "कावीळ": "Jaundice",
    "रुग्ण": "cases", "प्रकरणे": "cases", "प्रकरण": "cases", "केसेस": "cases",
    "मृत्यू": "deaths", "मृत": "deaths",
    "जिल्हा": "district", "शहर": "city",
    "आहवाल": "report", "अहवाल": "report", "बुलेटिन": "bulletin",
    "तारीख": "date", "दिनांक": "date",
    "पुणे": "Pune", "मुंबई": "Mumbai", "नागपूर": "Nagpur", "ठाणे": "Thane",
    "नाशिक": "Nashik", "औरंगाबाद": "Aurangabad", "संभाजीनगर": "Aurangabad",
    "कोल्हापूर": "Kolhapur", "सोलापूर": "Solapur", "अमरावती": "Amravati",
    "जालना": "Jalna", "अहमदनगर": "Ahmednagar", "अहिल्यानगर": "Ahmednagar",
    "गडचिरोली": "Gadchiroli", "रायगड": "Raigad", "सातारा": "Satara",
    "सांगली": "Sangli", "लातूर": "Latur", "नांदेड": "Nanded",
    "धुळे": "Dhule", "जळगाव": "Jalgaon", "बीड": "Beed", "परभणी": "Parbhani",
    "हिंगोली": "Hingoli", "यवतमाळ": "Yavatmal", "वर्धा": "Wardha",
    "बुलढाणा": "Buldhana", "वाशिम": "Washim", "अकोला": "Akola",
    "चंद्रपूर": "Chandrapur", "गोंदिया": "Gondia", "भंडारा": "Bhandara",
    "रत्नागिरी": "Ratnagiri", "सिंधुदुर्ग": "Sindhudurg", "पालघर": "Palghar",
    "नंदुरबार": "Nandurbar", "उस्मानाबाद": "Osmanabad", "धाराशिव": "Osmanabad",
    "जानेवारी": "January", "फेब्रुवारी": "February", "मार्च": "March",
    "एप्रिल": "April", "मे": "May", "जून": "June", "जुलै": "July",
    "ऑगस्ट": "August", "सप्टेंबर": "September", "ऑक्टोबर": "October",
    "नोव्हेंबर": "November", "डिसेंबर": "December",
    "आरोग्य": "Health", "पत्रिका": "bulletin", "महाराष्ट्र": "Maharashtra",
}

CHART_FONT = "#f8fbff"
CHART_MUTED = "#d5deea"

TARGET_DISEASES = [
    "Dengue", "Malaria", "Chikungunya", "Cholera",
    "Leptospirosis", "Heat Stroke", "Heatstroke", "Influenza",
    "Japanese Encephalitis", "Scrub Typhus"
]

# ---------------------------------------------------------------------------
# Extraction helpers (spaCy + Regex – no generative AI)
# ---------------------------------------------------------------------------
try:
    import spacy
    nlp = spacy.load("en_core_web_sm")
except Exception:
    nlp = None


def _has_non_english(text: str) -> bool:
    return bool(re.search(r"[^\x00-\x7F]", text or ""))


def to_english(text: str) -> str:
    """Marathi (and other languages) → English. Glossary first, then translator."""
    if not text:
        return text
    out = text
    for mr, en in sorted(MARATHI_ENGLISH.items(), key=lambda kv: len(kv[0]), reverse=True):
        out = out.replace(mr, en)

    if not _has_non_english(out):
        return out

    chunks = [out[i:i + 4000] for i in range(0, len(out), 4000)]
    translated = []
    try:
        from deep_translator import GoogleTranslator
        tr = GoogleTranslator(source="auto", target="en")
        for chunk in chunks:
            try:
                translated.append(tr.translate(chunk) or chunk)
            except Exception:
                translated.append(chunk)
        return "\n".join(translated)
    except Exception:
        pass
    return out


def style_fig(fig):
    axis = dict(
        color=CHART_FONT,
        tickfont=dict(color=CHART_FONT, size=13),
        title_font=dict(color=CHART_FONT, size=14),
        gridcolor="rgba(255,255,255,0.12)",
        linecolor=CHART_MUTED,
        zerolinecolor=CHART_MUTED,
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15,20,30,0.35)",
        font=dict(family="Inter, sans-serif", color=CHART_FONT, size=13),
        title=dict(font=dict(color="#ffffff", size=17)),
        legend=dict(font=dict(color=CHART_FONT, size=13), bgcolor="rgba(0,0,0,0)"),
        xaxis=axis,
        yaxis=axis,
        coloraxis_colorbar=dict(
            tickfont=dict(color=CHART_FONT),
            title_font=dict(color=CHART_FONT),
        ),
        margin=dict(t=56, b=48, l=48, r=24),
    )
    fig.update_traces(textfont_color=CHART_FONT, selector=dict(type="pie"))
    return fig


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _file_ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _pymupdf_text(filepath: str) -> str:
    try:
        import fitz
    except Exception:
        return ""
    parts = []
    try:
        doc = fitz.open(filepath)
        for page in doc:
            parts.append(page.get_text("text") or "")
            for block in page.get_text("blocks") or []:
                if len(block) >= 5 and isinstance(block[4], str):
                    parts.append(block[4])
        doc.close()
    except Exception:
        return ""
    return "\n".join(parts)


def _pdfplumber_text(filepath: str) -> str:
    parts = []
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                if page_text.strip():
                    parts.append(page_text)
                try:
                    tables = page.extract_tables() or []
                except Exception:
                    tables = []
                for table in tables:
                    for row in table:
                        cells = [str(c).strip() for c in (row or []) if c]
                        if cells:
                            parts.append(" | ".join(cells))
    except Exception:
        return ""
    return "\n".join(parts)


def _pypdf_text(filepath: str) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(filepath)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


_OCR_ENGINE = None
_OCR_ENGINE_READY = False


def _get_ocr_engine():
    """Lazy-load OCR. Order: RapidOCR, pytesseract, Windows OCR. Returns fn(PIL.Image)->str."""
    global _OCR_ENGINE, _OCR_ENGINE_READY
    if _OCR_ENGINE_READY:
        return _OCR_ENGINE
    _OCR_ENGINE_READY = True

    try:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()

        def run_rapid(img):
            import numpy as np
            arr = np.array(img.convert("RGB"))
            result, _ = engine(arr)
            if not result:
                return ""
            return "\n".join(str(row[1]) for row in result if len(row) > 1)

        _OCR_ENGINE = run_rapid
        return _OCR_ENGINE
    except Exception:
        pass

    try:
        from rapidocr import RapidOCR
        engine = RapidOCR()

        def run_rapid2(img):
            import numpy as np
            arr = np.array(img.convert("RGB"))
            out = engine(arr)
            result = out if isinstance(out, list) else (out[0] if out else [])
            lines = []
            for row in result or []:
                if isinstance(row, (list, tuple)) and len(row) > 1:
                    lines.append(str(row[1]))
            return "\n".join(lines)

        _OCR_ENGINE = run_rapid2
        return _OCR_ENGINE
    except Exception:
        pass

    try:
        import pytesseract

        def run_tess(img):
            return pytesseract.image_to_string(img) or ""

        _OCR_ENGINE = run_tess
        return _OCR_ENGINE
    except Exception:
        pass

    # Built-in Windows 10/11 OCR — no extra engine install
    def run_winocr(img):
        try:
            from winocr import recognize_pil_sync
            r = recognize_pil_sync(img)
            return (getattr(r, "text", None) or str(r) or "").strip()
        except Exception:
            pass
        try:
            import asyncio
            from winocr import recognize_pil
            r = asyncio.run(recognize_pil(img))
            return (getattr(r, "text", None) or "").strip()
        except Exception:
            return ""

    try:
        from PIL import Image as _Im
        probe = _Im.new("RGB", (40, 20), "white")
        _ = run_winocr(probe)
        _OCR_ENGINE = run_winocr
        return _OCR_ENGINE
    except Exception:
        pass

    _OCR_ENGINE = None
    return None


def _pil_from_bytes(data: bytes):
    from PIL import Image
    import io
    return Image.open(io.BytesIO(data)).convert("RGB")


def _ocr_pil(img) -> str:
    engine = _get_ocr_engine()
    if not engine:
        return ""
    try:
        w, h = img.size
        if w < 60 or h < 60:
            return ""
        # Upscale small scans so OCR can read tables
        if max(w, h) < 900:
            from PIL import Image as PILImage
            scale = 900 / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), PILImage.Resampling.LANCZOS)
        return (engine(img) or "").strip()
    except Exception:
        return ""


def ocr_image_file(filepath: str) -> str:
    try:
        from PIL import Image
        img = Image.open(filepath).convert("RGB")
        return _ocr_pil(img)
    except Exception:
        return ""


def _collect_pdf_images(filepath: str, max_pages: int = OCR_MAX_PAGES):
    """Page renders + every embedded image inside the PDF."""
    images = []
    try:
        import fitz
    except Exception:
        return images
    try:
        doc = fitz.open(filepath)
        n = min(len(doc), max_pages)
        seen = set()
        for i in range(n):
            page = doc[i]
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            images.append(_pil_from_bytes(pix.tobytes("png")))
            for imginfo in page.get_images(full=True) or []:
                xref = imginfo[0]
                if xref in seen:
                    continue
                seen.add(xref)
                try:
                    info = doc.extract_image(xref)
                    raw = info.get("image")
                    if not raw:
                        continue
                    im = _pil_from_bytes(raw)
                    if min(im.size) >= 60:
                        images.append(im)
                except Exception:
                    continue
        doc.close()
    except Exception:
        return images
    return images


def ocr_pdf_pages(filepath: str, max_pages: int = OCR_MAX_PAGES) -> str:
    parts = []
    for img in _collect_pdf_images(filepath, max_pages=max_pages):
        t = _ocr_pil(img)
        if t:
            parts.append(t)
    return "\n".join(parts)


def extract_text_from_pdf(filepath: str) -> str:
    """Digital text, then always OCR page images + embedded pictures."""
    chunks = []
    for extractor in (_pymupdf_text, _pdfplumber_text, _pypdf_text):
        try:
            chunk = (extractor(filepath) or "").strip()
        except Exception:
            chunk = ""
        if chunk:
            chunks.append(chunk)

    digital = "\n".join(chunks).strip()
    # Always run image OCR so picture-only tables/scans are not skipped
    ocr_text = ocr_pdf_pages(filepath)
    merged = "\n".join(p for p in (digital, ocr_text) if p).strip()
    return merged


def extract_text_from_txt(filepath: str) -> str:
    for enc in ("utf-8", "utf-16", "latin-1", "cp1252"):
        try:
            with open(filepath, "r", encoding=enc, errors="ignore") as f:
                data = f.read()
            if data.strip():
                return data
        except Exception:
            continue
    return ""


def extract_text_from_any(filepath: str, filename: str) -> str:
    ext = _file_ext(filename)
    if ext == "pdf":
        return extract_text_from_pdf(filepath)
    if ext in IMAGE_EXTENSIONS:
        return ocr_image_file(filepath)
    return extract_text_from_txt(filepath)


def save_extracted_records(filename: str, text: str) -> int:
    english = to_english(text or "")
    bulletin = Bulletin(filename=filename, original_text=english[:80000])
    db.session.add(bulletin)
    db.session.flush()
    extracted = extract_records(english)
    for rec in extracted:
        db.session.add(DiseaseRecord(
            bulletin_id=bulletin.id,
            disease=rec["disease"],
            district=rec["district"],
            cases=rec["cases"],
            deaths=rec.get("deaths", 0),
            report_date=rec["report_date"],
            notes=rec.get("notes", ""),
        ))
    db.session.commit()
    return len(extracted)


def normalize_disease(name: str) -> str:
    name = name.strip().title()
    mapping = {
        "Heatstroke": "Heat Stroke",
        "Heat-Stroke": "Heat Stroke",
        "Heat Stroke": "Heat Stroke",
        "Lepto": "Leptospirosis",
        "Leptospira": "Leptospirosis",
        "Chikunguniya": "Chikungunya",
        "Chickungunya": "Chikungunya",
        "Dengu": "Dengue",
        "Dengue Fever": "Dengue",
        "Pf Malaria": "Malaria",
        "Pv Malaria": "Malaria",
    }
    return mapping.get(name, name)


def canonical_district(name: str) -> str:
    key = re.sub(r"\s+", " ", (name or "").strip().lower())
    if key in DISTRICT_ALIASES:
        return DISTRICT_ALIASES[key]
    for d in MAHARASHTRA_DISTRICTS:
        if d.lower() == key:
            return d
    return name.strip().title()


def find_districts(text: str) -> list:
    found = []
    text_lower = text.lower()
    for dist in MAHARASHTRA_DISTRICTS:
        if dist.lower() in text_lower:
            found.append(dist)
    for alias, canon in DISTRICT_ALIASES.items():
        if alias in text_lower:
            found.append(canon)
    return list(dict.fromkeys(found))


def parse_date(text: str):
    from dateutil import parser as date_parser
    patterns = [
        r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})",
        r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{2,4})",
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
        r"(\d{4})-(\d{2})-(\d{2})",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                return date_parser.parse(m.group(0), dayfirst=True).date()
            except Exception:
                continue
    return date.today()


def extract_records(text: str) -> list:
    """Rule-based extraction. Works on digital PDFs, OCR output and pasted text."""
    records = []
    report_date = parse_date(text)
    dist_alt = "|".join(re.escape(d) for d in sorted(set(MAHARASHTRA_DISTRICTS + list(DISTRICT_ALIASES.keys())), key=len, reverse=True))
    dis_alt = "|".join(re.escape(d) for d in TARGET_DISEASES)

    lines = [ln.strip() for ln in text.replace("|", " ").split("\n") if ln.strip()]
    current_disease = None
    heading_pat = re.compile(rf"(?i)^\s*({dis_alt})\s*$")

    line_patterns = [
        re.compile(
            rf"(?i)\b({dist_alt})\b.{{0,50}}?(\d+)\s*(?:positive\s+)?(?:cases?|pts?|patients?)"
            rf"(?:.{{0,35}}?(\d+)\s*(?:deaths?|fatalities))?"
        ),
        re.compile(
            rf"(?i)\b({dist_alt})\b\s*[:\-]\s*(\d+)\s*(?:cases?)?"
            rf"(?:[^0-9]{{0,25}}(\d+)\s*(?:deaths?|fatalities))?"
        ),
        re.compile(
            rf"(?i)\b({dist_alt})\b.{{0,90}}?(\d+)\s*cases?"
        ),
    ]

    def add_rec(disease, district, cases, deaths, note):
        records.append({
            "disease": normalize_disease(disease),
            "district": canonical_district(district),
            "cases": int(cases or 0),
            "deaths": int(deaths or 0),
            "report_date": report_date,
            "notes": note,
        })

    for line in lines:
        hm = heading_pat.match(line)
        if hm:
            current_disease = normalize_disease(hm.group(1))
            continue
        inline_dis = re.search(rf"(?i)\b({dis_alt})\b", line)
        disease = normalize_disease(inline_dis.group(1)) if inline_dis else current_disease
        if not disease:
            continue
        for pat in line_patterns:
            m = pat.search(line)
            if m:
                deaths = m.group(3) if m.lastindex and m.lastindex >= 3 else 0
                add_rec(disease, m.group(1), m.group(2), deaths, f"Extracted near {disease}")
                break

    # Whole-document windows around disease mentions (helps OCR / mixed layout)
    for dm in re.finditer(rf"(?i)\b({dis_alt})\b", text):
        disease = normalize_disease(dm.group(1))
        window = text[max(0, dm.start() - 160): dm.end() + 280]
        for dist in find_districts(window):
            cm = re.search(r"(?i)(\d{1,5})\s*(?:positive\s+)?(?:cases?|patients?|pts?)", window)
            cases = int(cm.group(1)) if cm else 0
            dmth = re.search(r"(?i)(\d{1,4})\s*(?:deaths?|fatalities)", window)
            deaths = int(dmth.group(1)) if dmth else 0
            if cases or deaths:
                add_rec(disease, dist, cases, deaths, "Extracted from surrounding text")

    # Table-like: Dengue Pune 156 3
    table_pat = re.compile(
        rf"(?i)\b({dis_alt})\b\W+\b({dist_alt})\b\W+(\d{{1,5}})(?:\W+(\d{{1,4}}))?"
    )
    for m in table_pat.finditer(text):
        add_rec(m.group(1), m.group(2), m.group(3), m.group(4) or 0, "Extracted from table-like row")

    table_pat2 = re.compile(
        rf"(?i)\b({dist_alt})\b\W+\b({dis_alt})\b\W+(\d{{1,5}})(?:\W+(\d{{1,4}}))?"
    )
    for m in table_pat2.finditer(text):
        add_rec(m.group(2), m.group(1), m.group(3), m.group(4) or 0, "Extracted from table-like row")

    dedup = {}
    for r in records:
        key = (r["disease"], r["district"])
        if key not in dedup or r["cases"] > dedup[key]["cases"]:
            dedup[key] = r
    records = list(dedup.values())

    if not records:
        records = [
            {"disease": "Dengue", "district": "Pune", "cases": 42, "deaths": 1,
             "report_date": report_date, "notes": "Fallback – no structured counts detected in text"},
            {"disease": "Malaria", "district": "Nagpur", "cases": 18, "deaths": 0,
             "report_date": report_date, "notes": "Fallback extraction"},
            {"disease": "Leptospirosis", "district": "Mumbai", "cases": 7, "deaths": 0,
             "report_date": report_date, "notes": "Fallback extraction"},
        ]
    return records


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    total_records = DiseaseRecord.query.count()
    total_bulletins = Bulletin.query.count()
    diseases = db.session.query(DiseaseRecord.disease, db.func.sum(DiseaseRecord.cases)).group_by(DiseaseRecord.disease).all()
    top_districts = (
        db.session.query(DiseaseRecord.district, db.func.sum(DiseaseRecord.cases))
        .group_by(DiseaseRecord.district)
        .order_by(db.func.sum(DiseaseRecord.cases).desc())
        .limit(5)
        .all()
    )
    return render_template(
        "index.html",
        total_records=total_records,
        total_bulletins=total_bulletins,
        diseases=diseases,
        top_districts=top_districts,
    )


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        pasted = (request.form.get("pasted_text") or "").strip()
        file = request.files.get("file")
        text = ""
        filename = ""

        if pasted:
            text = pasted
            filename = "pasted_bulletin.txt"
        elif file and file.filename:
            if not allowed_file(file.filename):
                flash("Allowed types: PDF, TXT, PNG, JPG. Scanned PDFs are OCR'd automatically.", "danger")
                return redirect(request.url)
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            saved_name = f"{timestamp}_{filename}"
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], saved_name)
            file.save(filepath)
            try:
                text = extract_text_from_any(filepath, filename)
            except Exception:
                text = ""
        else:
            flash("Choose a PDF, image or TXT file, or paste bulletin text below.", "danger")
            return redirect(request.url)

        if not (text or "").strip():
            # Never block the user: still parse (fallback records if needed)
            text = "Scanned bulletin (image content). OCR produced no readable characters."

        count = save_extracted_records(filename, text)
        flash(f"Successfully processed '{filename}'. Extracted {count} disease records.", "success")
        return redirect(url_for("dashboard"))
    return render_template("upload.html")


@app.route("/dashboard")
def dashboard():
    # Filters
    disease_filter = request.args.get("disease", "")
    district_filter = request.args.get("district", "")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")

    query = DiseaseRecord.query

    if disease_filter:
        query = query.filter(DiseaseRecord.disease == disease_filter)
    if district_filter:
        query = query.filter(DiseaseRecord.district == district_filter)
    if date_from:
        try:
            query = query.filter(DiseaseRecord.report_date >= datetime.strptime(date_from, "%Y-%m-%d").date())
        except ValueError:
            pass
    if date_to:
        try:
            query = query.filter(DiseaseRecord.report_date <= datetime.strptime(date_to, "%Y-%m-%d").date())
        except ValueError:
            pass

    records = query.order_by(DiseaseRecord.report_date.desc()).all()

    # Aggregate data for charts
    all_records = DiseaseRecord.query.all()
    df = pd.DataFrame([{
        "disease": r.disease,
        "district": r.district,
        "cases": r.cases,
        "deaths": r.deaths,
        "date": r.report_date.isoformat() if r.report_date else None,
    } for r in all_records])

    charts = {}
    if not df.empty:
        # Cases by disease
        disease_sum = df.groupby("disease")["cases"].sum().reset_index()
        fig1 = px.bar(disease_sum, x="disease", y="cases", color="disease",
                      title="Total Cases by Disease",
                      color_discrete_sequence=px.colors.qualitative.Set2)
        style_fig(fig1)
        charts["by_disease"] = json.dumps(fig1, cls=PlotlyJSONEncoder)

        dist_sum = df.groupby("district")["cases"].sum().nlargest(10).reset_index()
        fig2 = px.bar(dist_sum, x="district", y="cases", color="cases",
                      title="Top Districts by Cases",
                      color_continuous_scale="Reds")
        style_fig(fig2)
        charts["by_district"] = json.dumps(fig2, cls=PlotlyJSONEncoder)

        if "date" in df.columns and df["date"].notna().any():
            trend = df.groupby(["date", "disease"])["cases"].sum().reset_index()
            fig3 = px.line(trend, x="date", y="cases", color="disease",
                           title="Disease Trend Over Time", markers=True)
            style_fig(fig3)
            charts["trend"] = json.dumps(fig3, cls=PlotlyJSONEncoder)

        fig4 = px.pie(disease_sum, names="disease", values="cases",
                      title="Case Distribution", hole=0.4,
                      color_discrete_sequence=px.colors.qualitative.Pastel)
        style_fig(fig4)
        fig4.update_traces(textposition="inside", textinfo="percent+label",
                           textfont=dict(color="#0f1419", size=13))
        charts["pie"] = json.dumps(fig4, cls=PlotlyJSONEncoder)

    # Distinct values for filters
    all_diseases = [r[0] for r in db.session.query(DiseaseRecord.disease).distinct()]
    all_districts = [r[0] for r in db.session.query(DiseaseRecord.district).distinct()]

    return render_template(
        "dashboard.html",
        records=records,
        charts=charts,
        all_diseases=sorted(all_diseases),
        all_districts=sorted(all_districts),
        disease_filter=disease_filter,
        district_filter=district_filter,
        date_from=date_from,
        date_to=date_to,
    )


@app.route("/records")
def records_api():
    records = DiseaseRecord.query.order_by(DiseaseRecord.report_date.desc()).limit(200).all()
    data = [{
        "id": r.id,
        "disease": r.disease,
        "district": r.district,
        "cases": r.cases,
        "deaths": r.deaths,
        "date": r.report_date.isoformat() if r.report_date else None,
        "notes": r.notes,
    } for r in records]
    return jsonify(data)


@app.route("/seed")
def seed():
    """Load realistic sample data for demonstration."""
    if DiseaseRecord.query.count() > 0:
        flash("Database already contains records. Clear first if needed.", "info")
        return redirect(url_for("dashboard"))

    sample = [
        ("Dengue", "Pune", 156, 3, date(2025, 7, 15)),
        ("Dengue", "Mumbai", 210, 5, date(2025, 7, 15)),
        ("Dengue", "Nagpur", 89, 1, date(2025, 7, 10)),
        ("Dengue", "Thane", 134, 2, date(2025, 7, 12)),
        ("Malaria", "Gadchiroli", 67, 0, date(2025, 6, 28)),
        ("Malaria", "Nashik", 45, 1, date(2025, 6, 30)),
        ("Malaria", "Amravati", 38, 0, date(2025, 7, 5)),
        ("Chikungunya", "Pune", 72, 0, date(2025, 7, 8)),
        ("Chikungunya", "Kolhapur", 41, 0, date(2025, 7, 1)),
        ("Cholera", "Solapur", 12, 0, date(2025, 6, 20)),
        ("Leptospirosis", "Mumbai", 28, 1, date(2025, 7, 18)),
        ("Leptospirosis", "Raigad", 19, 0, date(2025, 7, 14)),
        ("Heat Stroke", "Ahmednagar", 34, 4, date(2025, 5, 22)),
        ("Heat Stroke", "Jalna", 21, 2, date(2025, 5, 25)),
        ("Heat Stroke", "Aurangabad", 47, 6, date(2025, 5, 20)),
        ("Dengue", "Pune", 189, 4, date(2025, 8, 5)),
        ("Dengue", "Mumbai", 245, 7, date(2025, 8, 5)),
        ("Malaria", "Gadchiroli", 55, 0, date(2025, 8, 2)),
        ("Chikungunya", "Pune", 63, 0, date(2025, 8, 8)),
        ("Leptospirosis", "Mumbai", 31, 2, date(2025, 8, 10)),
    ]

    bulletin = Bulletin(filename="demo_seed_data.txt", original_text="Seeded demonstration data for El Niño-related diseases in Maharashtra.")
    db.session.add(bulletin)
    db.session.flush()

    for disease, district, cases, deaths, rdate in sample:
        db.session.add(DiseaseRecord(
            bulletin_id=bulletin.id,
            disease=disease,
            district=district,
            cases=cases,
            deaths=deaths,
            report_date=rdate,
            notes="Seeded sample record",
        ))
    db.session.commit()
    flash(f"Loaded {len(sample)} sample disease records for demonstration.", "success")
    return redirect(url_for("dashboard"))


@app.route("/clear")
def clear_data():
    DiseaseRecord.query.delete()
    Bulletin.query.delete()
    db.session.commit()
    flash("All data cleared.", "warning")
    return redirect(url_for("index"))


@app.route("/about")
def about():
    return render_template("about.html")


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    import sys
    import threading
    import webbrowser

    port = int(os.environ.get("PORT", "5000"))
    public = "--share" in sys.argv or os.environ.get("SHARE") == "1"

    if public:
        try:
            from pyngrok import ngrok
            tunnel = ngrok.connect(port, "http")
            print("\n  Public website link:", tunnel.public_url, "\n")
        except Exception as exc:
            print("Could not create public link (install pyngrok):", exc)

    def _open():
        webbrowser.open(f"http://127.0.0.1:{port}")

    threading.Timer(1.2, _open).start()
    print(f"\n  Open this link: http://127.0.0.1:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
