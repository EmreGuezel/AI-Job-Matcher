import requests
from google import genai

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"

try:
    response = requests.get(url)
    models = response.json().get('models', [])
    print("Senin Anahtarınla Çalışan Modeller:")
    for m in models:
        if 'generateContent' in m.get('supportedGenerationMethods', []):
            print(m['name'])
except Exception as e:
    print("Bağlantı hatası:", e)