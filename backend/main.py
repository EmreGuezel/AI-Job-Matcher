from ast import keyword

from fastapi import FastAPI, Form, UploadFile, File
from fastapi.responses import HTMLResponse
import pandas as pd
import os
import shutil
from backend.scraper import scrape_jobs
from backend.ai_matcher import analyze_match, extract_text
from backend.database import get_db_connection

app = FastAPI(title="AI Job Matcher", description="Kişiselleştirilmiş İş Eşleştirme Motoru")

html_arayuz = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Job Matcher</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 p-4 md:p-8 font-sans">
    <div class="max-w-3xl mx-auto space-y-6">
        <h1 class="text-3xl md:text-4xl font-extrabold text-center text-slate-800 tracking-tight">🚀 AI İş Eşleştirme Motoru</h1>
        <p class="text-center text-slate-500 mb-8">CV'ni yükle, yapay zeka sana en uygun ilanları yüzdelik uyum oranıyla sıralasın.</p>

        <!-- Profil Oluşturma Kartı -->
        <div class="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
            <h2 class="text-xl font-bold text-slate-700 mb-4">1. Profilini Oluştur</h2>
            <form id="profileForm" class="space-y-4">
                <div>
                    <label class="block text-sm font-semibold text-slate-600 mb-1">Kullanıcı Adı</label>
                    <input type="text" id="username" placeholder="Örn: emre" class="w-full border border-slate-300 p-2.5 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none transition" required>
                </div>
                <div>
                    <label class="block text-sm font-semibold text-slate-600 mb-1">CV (Sadece PDF)</label>
                    <input type="file" id="cv_file" accept=".pdf" class="w-full border border-slate-300 p-2 rounded-lg text-slate-600 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100 transition" required>
                </div>
                <button type="submit" class="w-full bg-slate-800 text-white font-semibold py-2.5 rounded-lg hover:bg-slate-700 transition">Sisteme Kaydet</button>
            </form>
            <div id="profileResult" class="mt-4 text-emerald-600 font-medium text-center"></div>
        </div>

        <!-- İş Arama Kartı -->
        <div class="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
            <h2 class="text-xl font-bold text-slate-700 mb-4">2. İlanları Analiz Et</h2>
            <form id="matchForm" class="space-y-4">
                <div>
                    <label class="block text-sm font-semibold text-slate-600 mb-1">Kayıtlı Kullanıcı Adın</label>
                    <input type="text" id="match_username" placeholder="Örn: emre" class="w-full border border-slate-300 p-2.5 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none transition" required>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-semibold text-slate-600 mb-1">Aranacak Pozisyon</label>
                        <input type="text" id="keyword" placeholder="Örn: Python Developer" class="w-full border border-slate-300 p-2.5 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none transition" required>
                    </div>
                    <div>
                        <label class="block text-sm font-semibold text-slate-600 mb-1">Konum</label>
                        <input type="text" id="location" value="USA" class="w-full border border-slate-300 p-2.5 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none transition" required>
                    </div>
                </div>
                <button type="submit" class="w-full bg-blue-600 text-white font-semibold py-2.5 rounded-lg hover:bg-blue-700 transition flex justify-center items-center gap-2">
                    <span>Yapay Zeka Analizini Başlat</span>
                </button>
            </form>
            
            <!-- Yükleme Animasyonu -->
            <div id="matchLoading" class="mt-6 hidden flex-col items-center justify-center space-y-3">
                <div class="w-8 h-8 border-4 border-blue-200 border-t-blue-600 rounded-full animate-spin"></div>
                <p class="text-slate-500 font-medium animate-pulse">İlanlar çekiliyor ve Gemini yapay zekası ile analiz ediliyor...</p>
            </div>
            
            <div id="matchResult" class="mt-8 space-y-4"></div>
        </div>
    </div>

    <script>
        document.getElementById('profileForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData();
            formData.append('username', document.getElementById('username').value);
            formData.append('cv_file', document.getElementById('cv_file').files[0]);

            const resultDiv = document.getElementById('profileResult');
            resultDiv.innerText = "Yükleniyor...";
            resultDiv.className = "mt-4 text-blue-600 font-medium text-center";
            
            const res = await fetch('/api/profile', { method: 'POST', body: formData });
            const data = await res.json();
            
            resultDiv.innerText = data.mesaj || data.error;
            resultDiv.className = data.error ? "mt-4 text-red-600 font-medium text-center" : "mt-4 text-emerald-600 font-medium text-center";
        });

        document.getElementById('matchForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const loading = document.getElementById('matchLoading');
            const resultDiv = document.getElementById('matchResult');
            
            loading.classList.remove('hidden');
            loading.classList.add('flex');
            resultDiv.innerHTML = "";

            const formData = new FormData();
            formData.append('username', document.getElementById('match_username').value);
            formData.append('keyword', document.getElementById('keyword').value);
            formData.append('location', document.getElementById('location').value);

            const res = await fetch('/api/match', { method: 'POST', body: formData });
            const data = await res.json();

            loading.classList.add('hidden');
            loading.classList.remove('flex');

            if(data.error) {
                resultDiv.innerHTML = `<div class="p-4 bg-red-50 text-red-700 rounded-lg border border-red-200">${data.error}</div>`;
                return;
            }

            let html = '<h3 class="text-lg font-bold text-slate-700 border-b pb-2">Analiz Sonuçları</h3>';
            data.siralı_ilanlar.forEach(ilan => {
                let badgeColor = ilan.Eslesme_Orani >= 70 ? 'bg-emerald-100 text-emerald-800 border-emerald-200' : 
                                 ilan.Eslesme_Orani >= 40 ? 'bg-amber-100 text-amber-800 border-amber-200' : 
                                 'bg-red-100 text-red-800 border-red-200';
                                 
                html += `
                    <div class="border border-slate-200 p-5 rounded-xl bg-slate-50 hover:bg-white hover:shadow-md transition">
                        <div class="flex flex-col md:flex-row justify-between md:items-center gap-3 mb-3">
                            <h4 class="text-lg font-bold text-slate-800">${ilan.Pozisyon} <span class="text-slate-500 font-medium text-base">@ ${ilan.Sirket}</span></h4>
                            <span class="${badgeColor} border px-3 py-1 rounded-full font-bold text-sm whitespace-nowrap text-center">
                                %${ilan.Eslesme_Orani} Uyum
                            </span>
                        </div>
                        <p class="text-sm text-slate-600 mb-4 leading-relaxed"><strong>AI Değerlendirmesi:</strong> ${ilan.Analiz}</p>
                        <a href="${ilan.Link}" target="_blank" class="inline-block text-blue-600 hover:text-blue-800 font-semibold text-sm underline underline-offset-2">İlan Detayına Git &rarr;</a>
                    </div>
                `;
            });
            resultDiv.innerHTML = html;
        });
    </script>
</body>
</html>
"""

@app.get("/")
def arayuz_sun():
    return HTMLResponse(content=html_arayuz, status_code=200)

@app.post("/api/profile")
def create_profile(
    username: str = Form(...),
    cv_file: UploadFile = File(...)
):
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    
    # BASE_DIR ve csv_path satırlarını silip şunları ekle:
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