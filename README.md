# El Niño-Aware Health Bulletin Parser

Interactive Flask web application for the S.Y. Mini Project (Group 9).

**Dept. of AI & DS · K.J. Somaiya Institute of Technology**  
Isha Chudasma (21) · Prathamesh Chavan (19) · Prachi Dhamecha (29)  
Guide: Prof. Pravin Patil

---

## Features

- Upload Maharashtra health bulletins (PDF, TXT, or photo)
- Digital PDFs: PyMuPDF + pdfplumber text/tables
- **Scanned PDFs / images: OCR (RapidOCR)**
- Extract disease, district, cases, deaths, date (spaCy + regex, no generative AI)
- SQLite storage + interactive Plotly dashboard

Target diseases: Dengue, Malaria, Chikungunya, Cholera, Leptospirosis, Heat Stroke.

---

## Quick Start (Windows)

```powershell
cd elnino_health_parser
pip install -r requirements.txt
python -m spacy download en_core_web_sm
py app.py
```

Open http://127.0.0.1:5000

1. **Load Demo Data** on Home, or
2. Upload `sample_bulletin.txt`, or
3. Upload a PDF (scanned files are OCR'd; may take up to a minute).

---

## Tech Stack

| Layer        | Tools                                      |
|--------------|--------------------------------------------|
| Backend      | Python 3, Flask, SQLAlchemy, SQLite        |
| Extraction   | PyMuPDF, pdfplumber, RapidOCR, spaCy, Regex|
| Visualisation| Plotly                                     |
| Frontend     | HTML5, CSS3, Vanilla JS                    |
