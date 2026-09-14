import fitz  # PyMuPDF kütüphanesi fitz adıyla içe aktarılır
import os

def extract_text_from_pdf(pdf_path):
    print(f"'{pdf_path}' okunuyor...")
    try:
        # PDF dosyasını aç
        doc = fitz.open(pdf_path)
        text = ""
        
        # Tüm sayfaları dön ve metinleri birleştir
        for page in doc:
            text += page.get_text()
            
        return text
    except Exception as e:
        return f"Hata oluştu: {str(e)}"

if __name__ == "__main__":
    # Proje ana dizinindeki data klasörünü bulur
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cv_path = os.path.join(BASE_DIR, "data", "Emre_Guzel_cv.pdf")
    
    cv_text = extract_text_from_pdf(cv_path)
    
    print("\n--- CV İÇERİĞİ (İlk 500 Karakter) ---")
    print(cv_text[:500])
    print("-------------------------------------")
    print(f"Toplam {len(cv_text)} karakter başarıyla okundu.")