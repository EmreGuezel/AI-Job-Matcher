import os
import json
import fitz  # PyMuPDF kütüphanesi
from google import genai

# Gerçek API anahtarını buraya ekle
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def extract_text(pdf_path):
    try:
        doc = fitz.open(pdf_path)
        return "".join(page.get_text() for page in doc)
    except Exception as e:
        return f"Hata: {str(e)}"

def analyze_match(cv_text, job_description):
    prompt = f"""
    Aşağıdaki CV'yi ve İş İlanını inceleyerek adayın uygunluğunu % üzerinden puanla.
    Sadece aşağıdaki gibi geçerli bir JSON formatında yanıt ver, ekstra hiçbir metin veya markdown (```json vb.) ekleme:
    {{"percentage": 85, "analysis": "Güçlü Yönler: ... Geliştirilmesi Gerekenler: ... Tavsiye: ..."}}
    
    CV İÇERİĞİ: {cv_text}
    İŞ İLANI: {job_description}
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-3.6-flash',
            contents=prompt
        )
        
        # Markdown kod bloklarını temizle
        clean_text = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(clean_text)
    except Exception as e:
        return {"percentage": 0, "analysis": f"API Hatası: {str(e)}"}