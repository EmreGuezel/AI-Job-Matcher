from fastapi import FastAPI, Form, UploadFile, File
import pandas as pd
import os
import shutil
from backend.scraper import scrape_jobs
from backend.ai_matcher import analyze_match, extract_text
from backend.database import get_db_connection

app = FastAPI(title="AI Job Matcher", description="Kişiselleştirilmiş İş Eşleştirme Motoru")

@app.post("/api/profile")
def create_profile(
    username: str = Form(..., description="Kullanıcı adınızı girin"),
    cv_file: UploadFile = File(..., description="CV'nizi PDF olarak yükleyin")
):
    # PDF dosyasını geçici olarak kaydet
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    temp_pdf_path = os.path.join(BASE_DIR, "data", f"{username}_temp.pdf")
    
    with open(temp_pdf_path, "wb") as buffer:
        shutil.copyfileobj(cv_file.file, buffer)
        
    # ai_matcher içindeki hazır fonksiyonunla PDF'i metne çevir
    cv_text = extract_text(temp_pdf_path)
    
    # İşlem bitince geçici PDF'i sil
    if os.path.exists(temp_pdf_path):
        os.remove(temp_pdf_path)
        
    # Sadece metni veritabanına kaydet
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users (username, cv_text) VALUES (?, ?)", (username, cv_text))
    conn.commit()
    conn.close()
    
    return {"status": "Başarılı", "mesaj": f"{username} profili PDF okunarak oluşturuldu."}

@app.post("/api/match")
def match_and_sort(
    username: str = Form(..., description="Profil oluştururken kullandığınız ad"),
    keyword: str = Form(..., description="Aranacak pozisyon (örn: python developer)"),
    location: str = Form("USA", description="Aranacak ülke veya şehir")
):
    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    
    if not user:
        return {"error": "Kullanıcı bulunamadı. Önce profil oluşturun."}
    
    cv_text = user["cv_text"]
    
    scrape_jobs(keyword, location)
    
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(BASE_DIR, "data", "jobs.csv")
    
    if not os.path.exists(csv_path):
        return {"error": "İlanlar çekilemedi veya bulunamadı."}
        
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