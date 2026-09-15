"""İlan toplama katmanı — iki kaynak, konuma göre otomatik yönlendirme.

JSearch : Canlı testte YALNIZCA ABD ve Kanada sonuç döndürdü. Almanya,
          Avustralya ve Hollanda 0 ilan; İngiltere ve Hindistan zaman aşımı.
          Türkiye kapsamı HİÇ yok — ülke/şehir seçimi fark etmiyor.
Jooble  : Türkiye (hem il hem ülke geneli). tr.jooble.org'dan alınmış AYRI
          bir API anahtarı gerektiriyor. Anahtar yoksa anlamlı bir hata
          mesajı döner.

Not: Jooble ücretsiz planı 500 istek/ÖMÜR BOYU (aylık değil) ve açıklama
alanı kırpılmış "snippet" olarak geliyor — bu, yerel TF-IDF eşleştirmesinin
isabetini JSearch'e göre düşürür.
"""

import html
import os
import re
import time

import pandas as pd
import requests
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

from backend.ai_matcher import _fold
from backend.experience import classify

# Yerel eşleştirme API çağrısı yapmadığı için ilan sayısını rahatça
# artırabiliyoruz — daha fazla ilan = daha isabetli IDF.
JOB_LIMIT = 25

# Açıklamalar ~5.000 karakter geliyor. CSV şişmesin diye kırpıyoruz.
DESCRIPTION_LIMIT = 4000

# Ağ ayarları.
#
# Ölçülen GERÇEK JSearch gecikmeleri (search-v2, aralarda bekleme ile):
#   5.3 / 18.9 / 41.4 / 53.1 / 55.3 / 60.7 saniye.
# Yani servis 5-61 sn arasında değişiyor ve yavaş uç ~55-61 sn. Bu yüzden:
#
#   * Okuma süresi bu aralığın ÜSTÜNDE olmalı. 45 sn denendi ve YANLIŞTI:
#     55 ve 60 sn süren ama BAŞARILI olan istekleri kesiyordu — kullanıcının
#     gördüğü "ilan bulunamadı" hatalarının bir kısmı buydu, servis hatası
#     değil bizim erken pes etmemizdi.
#   * Tekrar deneme yalnızca HIZLI başarısız olan denemelerde işe yarar;
#     uzun bir denemeden sonra bütçe kalmadığı için zaten yapılmaz
#     (aşağıdaki deadline kontrolü).
#   * Toplam bütçe, sunucu tarafı fonksiyon limitinin altında kalmalı.
#     Vercel'de limit daha düşükse SCRAPE_DEADLINE ile küçültün.
CONNECT_TIMEOUT = 10
READ_TIMEOUT = int(os.getenv("SCRAPE_READ_TIMEOUT", "65"))
REQUEST_DEADLINE = float(os.getenv("SCRAPE_DEADLINE", "70"))
REQUEST_RETRIES = 1
RETRY_BACKOFF = 2

# JSearch boş döndüğünde tekrar denemek için üst süre sınırı. Aynı sorgu
# ölçümde 10 / 0 / 10 ilan döndürdü — yani boş yanıt her zaman "ilan yok"
# demek değil. HIZLI gelen boş yanıt geçici bir aksaklıktır ve tekrar
# denenir; YAVAŞ gelen boş yanıt gerçek kapsam yokluğudur (ör. Almanya
# 45-75 sn sürüp 0 ilan döndürüyor) ve tekrar denemek sadece bekletir.
EMPTY_RETRY_MAX_SECONDS = 20

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

# ÜLKE GENELİ Türkiye seçimi de Jooble'a gitmeli. Arayüzdeki ülke listesinde
# "Türkiye" → "Turkey" değeri gönderiliyor ve bu değer bir il adı olmadığı
# için eskiden JSearch'e düşüyordu — JSearch'te ise Türkiye kapsamı HİÇ yok,
# bu yüzden ülke geneli aramalar her seferinde "ilan bulunamadı" veriyordu.
# Jooble "Türkiye" konumunu destekliyor (test: 30 ilan).
_TURKEY_ALIASES = {"turkiye", "turkey", "turkiye cumhuriyeti", "tr"}


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_snippet(raw):
    """Jooble snippet'ını düz metne çevirir.

    Jooble HTML parçaları gönderiyor — <b> vurguları, &nbsp; varlıkları,
    NBSP ve \\r\\n karışımı. Snippet zaten ~300 karakter olduğu için bu
    gürültü eşleştirmeyi ciddi biçimde bozuyor.
    """
    if not raw:
        return ""
    text = html.unescape(raw)            # &nbsp; -> \xa0, &amp; -> &
    text = _HTML_TAG_RE.sub(" ", text)   # <b>, </b>, <br/> ...
    # Kaynakta gerçekten kaybolmuş karakter (U+FFFD) varsa temizle. Not:
    # konsolda görülen "�" genellikle bu DEĞİLDİR — Windows terminalinin
    # Türkçe karakterleri (ü, ı) çizememesidir; veri sağlamdır.
    text = text.replace("�", " ")
    text = text.replace("\xa0", " ")     # NBSP
    text = re.sub(r"\s+", " ", text)
    return text.strip()


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


def _http_error_message(status, source):
    """HTTP durum kodunu kullanıcının anlayacağı bir cümleye çevirir."""
    if status in (401, 403):
        return (f"{source} API anahtarını reddetti ({status}). "
                f"Anahtar geçersiz ya da süresi dolmuş.")
    if status == 429:
        return (f"{source} istek kotası doldu ({status}). "
                f"Ücretsiz plan limiti bitti — bir süre sonra tekrar dene.")
    if status >= 500:
        return (f"{source} şu anda hata veriyor ({status}). "
                f"Bu geçici bir sorun, birazdan tekrar dene.")
    return f"{source} beklenmeyen bir yanıt döndü ({status})."


def _connection_error_message(source, exc):
    """Zaman aşımı ile bağlantı hatası kullanıcı için farklı şeyler söyler.

    İkisi de aynı çağrıda yakalanıyor ama "yanıt vermedi" demek DNS hatası
    için yanlış olurdu.
    """
    if exc is None or isinstance(exc, Timeout):
        # Toplam bütçeyi bildir: tek denemenin okuma süresi bütçe daralınca
        # kısaltılıyor, sabit bir sayı yazmak yanıltıcı olurdu.
        return (f"{source} zamanında yanıt vermedi ({REQUEST_DEADLINE:.0f} sn). "
                f"Servis geçici olarak yavaş olabilir — birazdan tekrar dene.")
    return (f"{source} sunucusuna bağlanılamadı. İnternet bağlantını "
            f"kontrol edip tekrar dene.")


def _request(method, url, source, **kwargs):
    """requests çağrılarını sarmalar: tekrar deneme + anlaşılır hata.

    Eskiden çağrılar çıplak yapılıyordu; zaman aşımında kullanıcı arayüzde
    urllib3'ün ham metnini görüyordu:

        HTTPSConnectionPool(host='jsearch.p.rapidapi.com', port=443):
        Read timed out. (read timeout=60)

    Bu mesaj kullanıcıya hiçbir şey söylemiyor. Artık geçici ağ hatalarında
    bir kez daha denenir, kalıcı hatalarda (401/403/429/5xx) doğrudan
    anlaşılır bir RuntimeError atılır.
    """
    deadline = time.monotonic() + REQUEST_DEADLINE
    last_error = None

    for attempt in range(REQUEST_RETRIES + 1):
        # Bütçe bittiyse yeni deneme başlatma — tekrar deneme toplam süreyi
        # sunucu limitinin üstüne çıkarmasın.
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            break

        try:
            response = method(
                url,
                timeout=(CONNECT_TIMEOUT, min(READ_TIMEOUT, remaining)),
                **kwargs,
            )
        except requests.RequestException as exc:
            last_error = exc
            # Zaman aşımı ve bağlantı sorunları geçici olabilir, bir kez daha
            # denenir. (requests'te SSLError ve ProxyError da ConnectionError
            # alt sınıfı olduğu için onlar da bir kez denenir — zararsız.)
            # Geçersiz URL gibi hatalar bu sınıfa girmez, tekrar denenmez.
            if not isinstance(exc, (Timeout, RequestsConnectionError)):
                raise RuntimeError(_connection_error_message(source, exc)) from exc
            # Tekrar denemek için yeterli bütçe kaldı mı?
            if (attempt < REQUEST_RETRIES
                    and deadline - time.monotonic() > RETRY_BACKOFF + 5):
                time.sleep(RETRY_BACKOFF)
                continue
        else:
            # Yanıt geldi. HTTP hatası kalıcıysa tekrar denemek anlamsız.
            if response.status_code >= 400:
                raise RuntimeError(_http_error_message(response.status_code, source))
            return response

    raise RuntimeError(_connection_error_message(source, last_error)) from last_error


def _scrape_jsearch(keyword, location, level):
    headers = {
        "X-RapidAPI-Key": "2ee9113eecmsh332153baca499f6p1fd4bcjsn09813801b13c",
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    params = {"query": f"{keyword} in {location}", "num_pages": "1"}

    # API'nin gerçekten desteklediği TEK seviye filtresi bu.
    if level == "Internship":
        params["employment_types"] = "INTERN"

    response = _request(requests.get, JSEARCH_URL, "JSearch", headers=headers, params=params)
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

    response = _request(
        requests.post,
        f"{JOOBLE_API_BASE}/{api_key}",
        "Jooble",
        json={"keywords": keyword, "location": location, "page": "1"},
    )
    job_list = response.json().get("jobs") or []

    rows = []
    for job in job_list[:JOB_LIMIT]:
        rows.append(_row(
            job.get("title"),
            job.get("company"),
            job.get("location") or location,
            job.get("link"),
            _clean_snippet(job.get("snippet")),
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
    folded = _fold(location or "")
    province = _TURKISH_FOLDED.get(folded)

    # Ülke geneli Türkiye ("Türkiye"/"Turkey") de Jooble'a gider. Bir il adı
    # seçildiyse o il, ülke seçildiyse "Türkiye" konum olarak gönderilir.
    start = time.monotonic()
    if province or folded in _TURKEY_ALIASES:
        rows = _scrape_jooble(keyword, province or "Türkiye", level)
        source = "Jooble"
    else:
        rows = _scrape_jsearch(keyword, location, level)
        source = "JSearch"

    # JSearch boş döndüyse ve yanıt HIZLI geldiyse bir kez daha dene —
    # ölçümde aynı sorgu bazen 0 bazen 10 ilan döndürüyor. Yavaş gelen boş
    # yanıtta tekrar denemek yalnızca beklemeyi ikiye katlar, o yüzden
    # yalnızca hızlı boş yanıtlar tazelenir.
    if (not rows and source == "JSearch"
            and time.monotonic() - start < EMPTY_RETRY_MAX_SECONDS):
        try:
            rows = _scrape_jsearch(keyword, location, level)
        except RuntimeError:
            rows = []          # ikinci deneme de başarısız — aşağıda raporlanır

    if not rows:
        # Kapsam gerçeği. Canlı testte: ABD ve Kanada sonuç verdi; Almanya,
        # Avustralya, Hollanda 0 ilan; İngiltere, Hindistan zaman aşımı.
        # Ayrıca JSearch AYNI sorguya bazen 10 bazen 0 ilan döndürüyor —
        # bu yüzden mesaj kesin bir "burada ilan yok" iddiası kurmuyor.
        if source == "JSearch":
            raise RuntimeError(
                f"'{keyword} / {location}' için ilan bulunamadı. JSearch her "
                f"ülkeyi indekslemiyor (testte ABD ve Kanada sonuç verdi) ve "
                f"aynı sorguya bazen boş yanıt döndürüyor — tekrar denemek "
                f"işe yarayabilir. Türkiye aramaları Jooble üzerinden çalışır."
            )
        raise RuntimeError(
            f"'{keyword} / {location}' için ilan bulunamadı. "
            f"Farklı bir anahtar kelime dene."
        )

    pd.DataFrame(rows).to_csv(CSV_PATH, index=False, encoding="utf-8-sig", sep=";")
    return f"{len(rows)} ilan bulundu ({source})"


if __name__ == "__main__":
    for loc in ("USA", "İstanbul"):
        try:
            print(f"{loc}: {scrape_jobs('python developer', loc)}")
        except Exception as exc:
            print(f"{loc}: HATA — {exc}")
