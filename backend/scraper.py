"""İlan toplama katmanı — iki kaynak, konuma göre otomatik yönlendirme.

JSearch : ABD / Kanada / İngiltere / BAE / Hindistan. Türkiye kapsamı YOK
          (test edildi: İstanbul, Ankara, İzmir → 0 ilan).
Jooble  : Türkiye kapsamı var ama tr.jooble.org'dan alınmış AYRI bir API
          anahtarı gerektiriyor. Anahtar yoksa Türkiye aramaları anlamlı
          bir hata mesajı döner.

Not: Jooble ücretsiz planı 500 istek/ÖMÜR BOYU (aylık değil) ve açıklama
alanı kırpılmış "snippet" olarak geliyor — bu, yerel TF-IDF eşleştirmesinin
isabetini JSearch'e göre düşürür.
"""

import os

import pandas as pd
import requests

from backend.ai_matcher import _fold
from backend.experience import classify

# Yerel eşleştirme API çağrısı yapmadığı için ilan sayısını rahatça
# artırabiliyoruz — daha fazla ilan = daha isabetli IDF.
JOB_LIMIT = 25

# Açıklamalar ~5.000 karakter geliyor. CSV şişmesin diye kırpıyoruz.
DESCRIPTION_LIMIT = 4000

CSV_PATH = "/tmp/jobs.csv"

JSEARCH_URL = "https://jsearch.p.rapidapi.com/search-v2"
JOOBLE_API_BASE = os.getenv("JOOBLE_API_BASE", "https://tr.jooble.org/api")

# Türkiye'nin 81 ili — bu listedeki bir konum Jooble'a yönlendirilir.
TURKISH_PROVINCES = {
    "Adana", "Adıyaman", "Afyonkarahisar", "Ağrı", "Aksaray", "Amasya", "Ankara",
    "Antalya", "Ardahan", "Artvin", "Aydın", "Balıkesir", "Bartın", "Batman",
    "Bayburt", "Bilecik", "Bingöl", "Bitlis", "Bolu", "Burdur", "Bursa",
    "Çanakkale", "Çankırı", "Çorum", "Denizli", "Diyarbakır", "Düzce", "Edirne",
    "Elazığ", "Erzincan", "Erzurum", "Eskişehir", "Gaziantep", "Giresun",
    "Gümüşhane", "Hakkâri", "Hatay", "Iğdır", "Isparta", "İstanbul", "İzmir",
    "Kahramanmaraş", "Karabük", "Karaman", "Kars", "Kastamonu", "Kayseri",
    "Kırıkkale", "Kırklareli", "Kırşehir", "Kilis", "Kocaeli", "Konya",
    "Kütahya", "Malatya", "Manisa", "Mardin", "Mersin", "Muğla", "Muş",
    "Nevşehir", "Niğde", "Ordu", "Osmaniye", "Rize", "Sakarya", "Samsun",
    "Siirt", "Sinop", "Sivas", "Şanlıurfa", "Şırnak", "Tekirdağ", "Tokat",
    "Trabzon", "Tunceli", "Uşak", "Van", "Yalova", "Yozgat", "Zonguldak",
}

# Serbest metin kutusuna küçük harfle ("ankara") veya Türkçe imlasız
# ("istanbul") yazılsa da doğru kaynağa gidilsin diye katlanmış eşleme.
_TURKISH_FOLDED = {_fold(p): p for p in TURKISH_PROVINCES}


def _row(title, company, city, link, description):
    """Ortak satır şeması + deneyim seviyesi sınıflandırması."""
    title = (title or "Bilinmiyor").strip()
    description = (description or "").strip()
    if not description:
        description = title

    return {
        "Pozisyon": title,
        "Şirket": (company or "Bilinmiyor").strip(),
        "Şehir": (city or "Uzaktan").strip(),
        "Deneyim_Seviyesi": classify(title, description) or "Belirsiz",
        "Link": (link or "Link Bulunamadı").strip(),
        "Aciklama": description[:DESCRIPTION_LIMIT],
    }


def _scrape_jsearch(keyword, location, level):
    headers = {
        "X-RapidAPI-Key": "2ee9113eecmsh332153baca499f6p1fd4bcjsn09813801b13c",
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    params = {"query": f"{keyword} in {location}", "num_pages": "1"}

    # API'nin gerçekten desteklediği TEK seviye filtresi bu.
    if level == "Internship":
        params["employment_types"] = "INTERN"

    response = requests.get(JSEARCH_URL, headers=headers, params=params, timeout=60)
    response.raise_for_status()
    job_list = (response.json().get("data") or {}).get("jobs") or []

    rows = []
    for job in job_list[:JOB_LIMIT]:
        rows.append(_row(
            job.get("job_title"),
            job.get("employer_name"),
            job.get("job_city") or job.get("job_country") or "Uzaktan",
            job.get("job_apply_link"),
            job.get("job_description"),
        ))
    return rows


def _scrape_jooble(keyword, location, level):
    api_key = os.getenv("JOOBLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "JOOBLE_API_KEY tanımlı değil. Türkiye ilanları için "
            "tr.jooble.org/api/about adresinden ücretsiz anahtar alıp "
            "ortam değişkeni olarak eklemelisin."
        )

    response = requests.post(
        f"{JOOBLE_API_BASE}/{api_key}",
        json={"keywords": keyword, "location": location, "page": "1"},
        timeout=60,
    )
    response.raise_for_status()
    job_list = response.json().get("jobs") or []

    rows = []
    for job in job_list[:JOB_LIMIT]:
        rows.append(_row(
            job.get("title"),
            job.get("company"),
            job.get("location") or location,
            job.get("link"),
            job.get("snippet"),
        ))
    return rows


def scrape_jobs(keyword="python developer", location="USA", level="all"):
    """Konuma göre kaynağı seçer, ilanları CSV'ye yazar.

    level: "all" veya experience.LEVELS içindeki bir değer.

    Hata durumunda exception ATAR — eskiden hata metni döndürüp CSV'yi hiç
    güncellemiyordu, bu yüzden çağıran taraf sessizce ÖNCEKİ aramanın
    ilanlarını puanlıyordu.
    """
    # "ankara" -> "Ankara": hem kaynağı doğru seçer hem Jooble'a düzgün
    # yazımlı şehir adı gönderir.
    province = _TURKISH_FOLDED.get(_fold(location or ""))

    if province:
        rows = _scrape_jooble(keyword, province, level)
        source = "Jooble"
    else:
        rows = _scrape_jsearch(keyword, location, level)
        source = "JSearch"

    if not rows:
        raise RuntimeError(f"'{keyword} / {location}' için aktif ilan bulunamadı.")

    pd.DataFrame(rows).to_csv(CSV_PATH, index=False, encoding="utf-8-sig", sep=";")
    return f"{len(rows)} ilan bulundu ({source})"


if __name__ == "__main__":
    for loc in ("USA", "İstanbul"):
        try:
            print(f"{loc}: {scrape_jobs('python developer', loc)}")
        except Exception as exc:
            print(f"{loc}: HATA — {exc}")
