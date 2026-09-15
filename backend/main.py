from fastapi import FastAPI, Form, UploadFile, File, Request
from fastapi.templating import Jinja2Templates
import pandas as pd
import os
import shutil
from backend.scraper import scrape_jobs
from backend.ai_matcher import analyze_match, extract_text
from backend.database import get_db_connection

app = FastAPI(title="AI Job Matcher", description="Kişiselleştirilmiş İş Eşleştirme Motoru")

# 1. Şablon (Template) klasörünün yolunu belirle
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates_path = os.path.join(BASE_DIR, "templates")
templates = Jinja2Templates(directory=templates_path)

# 2. Ana sayfayı index.html'den sun
@app.get("/")
def arayuz_sun(request: Request):
    return templates.TemplateResponse(request, "index.html")

@app.post("/api/profile")
def create_profile(
    username: str = Form(...),
    cv_file: UploadFile = File(...)
):
    temp_pdf_path = f"/tmp/{username}_temp.pdf"

    with open(temp_pdf_path, "wb") as buffer:
        shutil.copyfileobj(cv_file.file, buffer)

    cv_text = extract_text(temp_pdf_path)

    if os.path.exists(temp_pdf_path):
        os.remove(temp_pdf_path)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users (username, cv_text) VALUES (?, ?)", (username, cv_text))
    conn.commit()
    conn.close()

    return {"status": "Başarılı", "mesaj": f"Tebrikler! {username} profili oluşturuldu."}

@app.post("/api/match")
def match_and_sort(
    username: str = Form(...),
    keyword: str = Form(...),
    location: str = Form(...)
):
    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    if not user:
        return {"error": "Kullanıcı bulunamadı. Lütfen önce yukarıdan profil oluşturun."}

    cv_text = user["cv_text"]
    scrape_jobs(keyword, location)

    csv_path = "/tmp/jobs.csv"

    if not os.path.exists(csv_path):
        return {"error": "İlanlar çekilemedi."}

    df = pd.read_csv(csv_path, sep=';', encoding='utf-8-sig')

    results = []
    for index, row in df.iterrows():
        job_desc = f"Pozisyon: {row['Pozisyon']}, Şirket: {row['Şirket']}, Deneyim: {row['Deneyim_Seviyesi']}"
        ai_result = analyze_match(cv_text, job_desc)

        match_data = {
            "Sirket": row['Şirket'],
            "Pozisyon": row['Pozisyon'],
            "Eslesme_Orani": ai_result.get("percentage", 0),
            "Analiz": ai_result.get("analysis", "Analiz yapılamadı"),
            "Link": row['Link']
        }
        results.append(match_data)

        cursor.execute('''
            INSERT INTO matches (user_id, job_title, company, match_percentage, analysis_text)
            VALUES (?, ?, ?, ?, ?)
        ''', (user["id"], row['Pozisyon'], row['Şirket'], match_data["Eslesme_Orani"], match_data["Analiz"]))

    conn.commit()
    conn.close()

    sorted_results = sorted(results, key=lambda x: x["Eslesme_Orani"], reverse=True)
    return {"kullanici": username, "siralı_ilanlar": sorted_results}
