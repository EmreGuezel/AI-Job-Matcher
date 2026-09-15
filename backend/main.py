from fastapi import FastAPI, Form, UploadFile, File, Request
from fastapi.templating import Jinja2Templates
import pandas as pd
import os
import shutil
from backend.scraper import CSV_PATH, scrape_jobs
from backend.ai_matcher import analyze_matches, extract_text
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

    # extract_text hata durumunda exception atmak yerine "Hata: ..." döner.
    # Eskiden bu metin olduğu gibi CV olarak kaydediliyordu ve tüm ilanlar
    # sessizce %0 alıyordu — burada erkenden durduruyoruz.
    if cv_text.startswith("Hata:") or len(cv_text.strip()) < 50:
        return {"error": "CV okunamadı. PDF'in metin içerdiğinden emin ol "
                         "(taranmış/görüntü PDF'ler okunamaz)."}

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
    location: str = Form(...),
    level: str = Form("all")
):
    conn = get_db_connection()
    cursor = conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    if not user:
        return {"error": "Kullanıcı bulunamadı. Lütfen önce yukarıdan profil oluşturun."}

    cv_text = user["cv_text"]

    # Kazıma başarısız olursa burada dur — aksi halde /tmp'de duran ÖNCEKİ
    # aramanın CSV'si sessizce yeniden puanlanırdı.
    try:
        scrape_info = scrape_jobs(keyword, location, level)
    except Exception as e:
        return {"error": str(e)}

    # Yolu scraper'dan al — iki yerde ayrı yazılırsa biri değişince
    # okuma sessizce eski dosyaya bakar.
    csv_path = CSV_PATH

    if not os.path.exists(csv_path):
        return {"error": "İlanlar çekilemedi."}

    # fillna: eksik alanlar NaN gelirse eşleştirme motoru string bekliyor
    df = pd.read_csv(csv_path, sep=';', encoding='utf-8-sig').fillna('')
    jobs = df.to_dict('records')

    # Seviye süzgeci burada uygulanır (kaynak filtreleri güvenilmez olduğu için
    # seviye başlık/açıklamadan tahmin ediliyor — bkz. experience.py)
    total_found = len(jobs)
    if level and level != "all":
        jobs = [j for j in jobs if j.get("Deneyim_Seviyesi") == level]
        if not jobs:
            return {"error": f"{total_found} ilan bulundu ama hiçbiri "
                             f"'{level}' seviyesiyle eşleşmedi. "
                             f"Seviye tahmini ilan başlığına dayanıyor — "
                             f"'Tüm Seviyeler' seçeneğini deneyin."}

    # Tüm ilanlar TEK SEFERDE puanlanır: IDF tüm korpus üzerinden hesaplandığı
    # için ilan başına ayrı çağrı yapılırsa skorlar anlamsızlaşır.
    scored = analyze_matches(cv_text, jobs)

    results = []
    for row, ai_result in zip(jobs, scored):
        match_data = {
            "Sirket": row.get('Şirket', ''),
            "Pozisyon": row.get('Pozisyon', ''),
            "Sehir": row.get('Şehir', ''),
            "Seviye": row.get('Deneyim_Seviyesi', '') or "Belirsiz",
            "Eslesme_Orani": ai_result.get("percentage", 0),
            "Analiz": ai_result.get("analysis", "Analiz yapılamadı"),
            "Link": row.get('Link', '')
        }
        results.append(match_data)

        cursor.execute('''
            INSERT INTO matches (user_id, job_title, company, match_percentage, analysis_text)
            VALUES (?, ?, ?, ?, ?)
        ''', (user["id"], match_data["Pozisyon"], match_data["Sirket"],
              match_data["Eslesme_Orani"], match_data["Analiz"]))

    conn.commit()
    conn.close()

    sorted_results = sorted(results, key=lambda x: x["Eslesme_Orani"], reverse=True)
    return {
        "kullanici": username,
        "siralı_ilanlar": sorted_results,
        "bilgi": scrape_info,
        "toplam_bulunan": total_found,
    }
