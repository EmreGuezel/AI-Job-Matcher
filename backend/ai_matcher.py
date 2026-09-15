"""Yerel CV <-> ilan eşleştirme motoru.

Hiç API çağrısı yok, hiç limit yok. TF-IDF tabanlı, saf Python (ek bağımlılık yok).
Gerçek CV ve canlı JSearch ilanlarıyla kalibre edildi: alakasız ilanlar
(nurse/chef/accountant/network) %0-39, alakalı ilanlar %42-69 bandına düşüyor.
"""

import math
import re
from collections import Counter

import fitz  # PyMuPDF


# ---------------------------------------------------------------------------
# AYARLAR — puanlar garip geliyorsa buradan ayarla
# ---------------------------------------------------------------------------
SKILL_WEIGHT = 0.80   # CV'deki yeteneklerin ilanda bulunma oranı (ana sinyal)
REQ_WEIGHT = 0.20     # ilan gereksinimlerinin CV'de bulunma oranı
CURVE = 0.50          # yumuşak eğri; küçültmek puanları yukarı çeker
MIN_DF = 2            # en az bu kadar ilanda geçen terim "gerçek" sayılır
MATCHED_LIMIT = 10    # analiz metninde gösterilecek eşleşen yetenek sayısı
MISSING_LIMIT = 6     # analiz metninde gösterilecek eksik gereksinim sayısı


def extract_text(pdf_path):
    try:
        doc = fitz.open(pdf_path)
        return "".join(page.get_text() for page in doc)
    except Exception as e:
        return f"Hata: {str(e)}"


# ---------------------------------------------------------------------------
# METİN İŞLEME
# ---------------------------------------------------------------------------
# Türkçe'ye özel: "İ".lower() Python'da "i̇" (i + birleşik nokta) üretir ve
# "I" -> "ı" eşleşmesini bozar. Bu yüzden küçültmeden ÖNCE çeviriyoruz.
_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ç": "c", "Ç": "c", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
    "â": "a", "î": "i", "û": "u",
})

# Harf, rakam ve teknoloji adlarındaki + / # işaretleri (c++, c#, f#)
TOKEN_RE = re.compile(r"[a-z0-9+#]+")

# Basit gövde indirgeme: developer / development / developed -> develop
# ("python" veya "aws" gibi kısa kelimeleri bozmamak için uzunluk kontrolü var)
_SUFFIXES = ("ings", "ing", "tion", "ment", "ance", "ence", "ally", "ers", "er", "ed", "es", "s", "ly")


def _fold(text):
    """Türkçe'ye duyarlı küçük harf + aksan sadeleştirme."""
    return text.translate(_TR_MAP).lower()


def _stem(token):
    for _ in range(2):
        for suffix in _SUFFIXES:
            if len(token) > len(suffix) + 3 and token.endswith(suffix):
                token = token[:-len(suffix)]
                break
        else:
            return token
    return token


# Ham kelimeler. Aşağıda _stem'den geçirilip normalize ediliyor, böylece
# "management"/"managing"/"manages" gibi tüm çekimleri tek girdiyle yakalanıyor.
_RAW_STOPWORDS = {
    # --- Türkçe ---
    "ve", "ile", "icin", "bir", "bu", "da", "de", "ki", "mi", "mu",
    "ne", "cok", "daha", "en", "gibi", "kadar", "sonra", "once", "ama",
    "fakat", "ancak", "veya", "ya", "hem", "her", "hic", "tum", "butun",
    "olan", "olarak", "oldugu", "ise", "eger", "gore", "uzere", "arasinda",
    "icinde", "uzerinde", "olacak", "oldugundan", "sahip", "ilgili",
    "yapmak", "yapilan", "eden", "edilmesi", "gerekli", "gerekiyor",
    "istiyoruz", "ariyoruz", "ekip", "ekibimiz", "sirket", "sirketimiz",
    "pozisyon", "ilan", "is", "gorev", "sorumluluk", "tecrube", "deneyim",
    "yil", "yillik", "aday", "adaylar", "tercihen", "bilgi", "bilgisi",
    "sahibi", "olmak", "yapabilecek", "konusunda", "alaninda", "dahil", "ayrica",
    # --- İngilizce ---
    "the", "and", "for", "with", "you", "your", "will", "our", "are", "we",
    "be", "to", "of", "in", "an", "is", "as", "on", "that", "this", "or",
    "at", "by", "from", "have", "has", "had", "their", "they", "it", "its",
    "job", "work", "working", "team", "teams", "role", "company", "about",
    "who", "what", "when", "where", "which", "more", "most", "other",
    "experience", "experienced", "years", "year", "ability", "able",
    "skills", "skill", "including", "include", "includes", "new",
    "using", "use", "used", "must", "should", "would", "can", "may",
    "required", "require", "requirements", "preferred", "plus", "strong",
    "good", "great", "excellent", "looking", "join", "help", "well",
    "all", "any", "not", "but", "if", "so", "up", "out", "into", "over",
    "per", "within", "across", "also", "such", "than", "then", "there",
    "these", "those", "was", "were", "been", "being", "do", "does", "did",
    "how", "why", "each", "both", "through", "between", "during", "under",
    "position", "responsibilities", "qualifications", "candidate",
    "candidates", "applicants", "apply", "employment", "full", "part",
    "time", "salary", "benefits", "opportunity", "opportunities",
    # --- CV başlığı / iletişim bilgisi gürültüsü ---
    # PDF'ten gelen "Date of birth | Gender | Mobile phone | Email" satırları
    # aksi halde "yetenek" gibi görünüp analiz metnini kirletiyor.
    "date", "birth", "gender", "male", "female", "nationality", "phone",
    "mobile", "email", "mail", "address", "home", "website", "github",
    "linkedin", "gmail", "http", "https", "www", "com", "org", "net",
    "turkiye", "turkish", "english", "cv", "resume", "curriculum",
    # --- Jenerik dolgu (bunlar yetenek değil, her ilanda geçer) ---
    "approach", "think", "result", "multiple", "quick", "short", "next",
    "professional", "various", "provide", "ensure", "maintain", "assist",
    "perform", "etc",
}

# Not: "develop", "system", "manage", "support" kasıtlı olarak listede DEĞİL —
# bunlar ilan başlıklarında ve CV'de gerçek anlam taşıyor, IDF'e bırakıldı.
STOPWORDS = {_stem(w) for w in _RAW_STOPWORDS} | _RAW_STOPWORDS


def _tokenize_pairs(text):
    """(gövde, orijinal_kelime) çiftleri döner — gösterim için orijinali saklıyoruz."""
    pairs = []
    for token in TOKEN_RE.findall(_fold(text)):
        if len(token) < 2 or token.isdigit():
            continue
        stem = _stem(token)
        if stem in STOPWORDS or token in STOPWORDS:
            continue
        pairs.append((stem, token))
    return pairs


def _tokenize(text):
    return [stem for stem, _ in _tokenize_pairs(text)]


def _display_map(pairs_list):
    """gövde -> gösterilecek gerçek kelime. En sık geçen, eşitlikte en kısa olan."""
    counts = {}
    for pairs in pairs_list:
        for stem, original in pairs:
            key = (stem, original)
            counts[key] = counts.get(key, 0) + 1
    best = {}
    for (stem, original), n in sorted(counts.items(), key=lambda kv: (-kv[1], len(kv[0][1]))):
        best.setdefault(stem, original)
    return best


def _idf_table(docs):
    """IDF'i SADECE ilan korpusu üzerinden hesaplar.

    CV'yi dahil etmek zararlı: CV'deki kişisel gürültü hiçbir ilanda
    geçmediği için en yüksek IDF'i alıp puanı bozuyor.
    """
    n = len(docs)
    df = Counter()
    for tokens in docs:
        df.update(set(tokens))
    return df, {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}


def _describe(matched, gaps, score):
    """Yüzdenin yanına hangi yeteneklerin eşleştiğini/eksik olduğunu yazar."""
    parts = []
    if matched:
        parts.append("Eşleşen yetenekler: " + ", ".join(matched[:MATCHED_LIMIT]))
    else:
        parts.append("Eşleşen yetenek bulunamadı")

    if gaps:
        parts.append("İlanın istediği, CV'de görünmeyen: " + ", ".join(gaps[:MISSING_LIMIT]))

    if score >= 65:
        parts.append("Güçlü eşleşme — başvurmaya değer.")
    elif score >= 40:
        parts.append("Kısmi eşleşme — CV'yi bu ilana göre uyarlaman faydalı olur.")
    else:
        parts.append("Zayıf eşleşme — bu ilan profilinle örtüşmüyor.")

    return " | ".join(parts)


# ---------------------------------------------------------------------------
# ANA FONKSİYON
# ---------------------------------------------------------------------------
def analyze_matches(cv_text, jobs):
    """CV'yi ilan listesiyle yerel olarak karşılaştırır. Hiç API çağrısı yok.

    jobs: [{"Pozisyon": ..., "Aciklama": ...}, ...]
    Dönen: aynı sırayla [{"percentage": int, "analysis": str}, ...]

    Tüm ilanlar TEK SEFERDE verilmeli — IDF tüm korpus üzerinden hesaplanıyor,
    tek tek çağrılırsa IDF anlamsızlaşır.
    """
    jobs = list(jobs)
    if not jobs:
        return []

    cv_pairs = _tokenize_pairs(cv_text or "")
    if not cv_pairs:
        return [{"percentage": 0, "analysis": "CV metni okunamadı veya boş."} for _ in jobs]

    # Şirket adı kasıtlı olarak dışarıda: her ilanda benzersiz olduğu için
    # yüksek IDF alıp "eksik gereksinim" gibi görünerek puanı haksızca düşürüyor.
    job_pairs = [
        _tokenize_pairs(f"{j.get('Pozisyon', '')} {j.get('Aciklama', '')}")
        for j in jobs
    ]
    job_terms = [[stem for stem, _ in pairs] for pairs in job_pairs]

    df, idf = _idf_table(job_terms)
    display = _display_map([cv_pairs] + job_pairs)

    cv_unique = {stem for stem, _ in cv_pairs}

    # CV'deki hangi terimler "gerçek" yetenek? En az MIN_DF ilanda geçenler.
    # Kişisel gürültü (e-posta, adres) hiçbir ilanda geçmez, böylece elenir.
    # Az ilanlı aramalarda (df>=2 boş kalırsa) gevşet.
    skills = [t for t in cv_unique if df.get(t, 0) >= MIN_DF]
    if not skills:
        skills = [t for t in cv_unique if df.get(t, 0) >= 1]
    total_skill_weight = sum(idf.get(t, 1.0) for t in skills) or 1.0

    results = []
    for terms in job_terms:
        job_set = set(terms)

        # Yön 1: CV'deki yeteneklerin ne kadarı bu ilanda var?
        skill_hit = sum(idf.get(t, 1.0) for t in skills if t in job_set) / total_skill_weight

        # Yön 2: ilanın (jenerik olmayan) gereksinimlerinin ne kadarı CV'de var?
        req_terms = [t for t in job_set if df.get(t, 0) >= MIN_DF] or list(job_set)
        req_total = sum(idf.get(t, 1.0) for t in req_terms) or 1.0
        req_hit = sum(idf.get(t, 1.0) for t in req_terms if t in cv_unique) / req_total

        raw = SKILL_WEIGHT * skill_hit + REQ_WEIGHT * req_hit
        score = max(0, min(100, round(100 * (raw ** CURVE))))

        name = lambda t: display.get(t, t)  # noqa: E731
        matched = [name(t) for t in sorted((t for t in skills if t in job_set),
                                           key=lambda t: -idf.get(t, 1.0))]
        gaps = [name(t) for t in sorted((t for t in req_terms if t not in cv_unique),
                                        key=lambda t: -idf.get(t, 1.0))]
        results.append({"percentage": score, "analysis": _describe(matched, gaps, score)})

    return results
