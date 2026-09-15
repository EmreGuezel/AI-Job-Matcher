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
SKILL_WEIGHT = 0.90   # CV'deki yeteneklerin ilanda bulunma oranı (ana sinyal)
REQ_WEIGHT = 0.10     # ilan gereksinimlerinin CV'de bulunma oranı
                      # (düşük: CV'nin "hakkımda" dolgusu bu yönü neredeyse
                      #  sabit yapıyor, bu yüzden ayırt ediciliği zayıf)
CURVE = 0.50          # yumuşak eğri; küçültmek puanları yukarı çeker
MIN_DF = 2            # en az bu kadar ilanda geçen terim "gerçek" sayılır
MATCHED_LIMIT = 10    # analiz metninde gösterilecek eşleşen yetenek sayısı
MISSING_LIMIT = 6     # analiz metninde gösterilecek eksik gereksinim sayısı

# --- Kıdem / başlık / yıl düzeltmeleri -------------------------------------
# Kelime örtüşmesi kıdemi ayırt edemez: bir CS mezununun CV'si ile bir senior
# ilanı neredeyse tüm alan kelimelerini paylaşır. Bu üç çarpan, aşağıdaki
# taban puanı ölçekler (hepsi <= 1.0, yani sadece düşürebilirler).
TITLE_FLOOR = 0.45    # ilan başlığı CV ile hiç örtüşmüyorsa taban çarpan
YEARS_FLOOR = 0.70    # deneyim yılı çok yetersizse taban çarpan
YEARS_STEP = 0.05     # eksik her yıl için çarpan düşüşü

# Başlık örtüşmesinden ÇIKARILAN kelimeler (tanımı _stem'den sonra geliyor).


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
    # --- CV "hakkımda" kalıpları ---
    # Bunlar CV'lerin özet paragrafında ve ders içeriklerinde geçer, yetenek
    # değildir. Listede olmadıklarında her ilanla örtüşüp ("Communications
    # Intern" -> "technical communication") alakasız ilanları yukarı çekiyor.
    "analytical", "analytically", "concept", "concepts", "knowledge",
    "environment", "practice", "practices", "client", "clients", "market",
    "operation", "operations", "communication", "collaborative", "adaptable",
    "motivated", "driven", "efficient", "efficiently", "solve", "problem",
    "problems", "teamwork", "technical", "ability", "field", "area",
}

# Not: "develop", "system", "manage", "support" kasıtlı olarak listede DEĞİL —
# bunlar ilan başlıklarında ve CV'de gerçek anlam taşıyor, IDF'e bırakıldı.
STOPWORDS = {_stem(w) for w in _RAW_STOPWORDS} | _RAW_STOPWORDS

# Başlık örtüşmesinden ÇIKARILAN kelimeler. Kıdem ve istihdam türü kelimeleri
# burada: onların cezasını kıdem çarpanı veriyor. Aksi halde "Entry Level
# Python Developer" başlığı, CV'de "entry" geçmediği için "Python Developer"dan
# DAHA DÜŞÜK puan alıyordu — yani daha alakalı ilan daha kötü sıralanıyordu.
_RAW_TITLE_IGNORE = {
    "senior", "junior", "jr", "sr", "lead", "staff", "principal", "entry",
    "level", "associate", "intern", "internship", "trainee", "graduate",
    "mid", "director", "executive", "head", "chief", "vp", "manager",
    "remote", "hybrid", "onsite", "contract", "temporary", "permanent",
    "urgent", "hiring", "immediate", "full", "part", "time", "new", "open",
}
_TITLE_IGNORE = {_stem(w) for w in _RAW_TITLE_IGNORE} | _RAW_TITLE_IGNORE


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
    idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}
    # Korpusun HİÇBİR ilanında geçmeyen bir terim "önemsiz" değildir — tersine
    # en ayırt edici sinyaldir. Eskiden bu terimler varsayılan 1.0 (en düşük
    # ağırlık) alıyordu ve CV'deki jenerik dolgu kelimelerinin gerisine
    # düşüyordu; "python" gibi gerçek yetenekler böylece eleniyordu.
    max_idf = math.log((1 + n) / 1.0) + 1.0
    return df, idf, max_idf


def _title_factor(cv_unique, title):
    """İlan başlığı CV'yle örtüşüyor mu?

    Başlık, ilanın ne iş olduğunu söyleyen en yoğun sinyal. CV'yle hiç
    örtüşmeyen bir başlık ("Nurse", "Game Developer") farklı bir rol demek;
    taban puanı aşağı çekeriz. Kelime ağırlığı kullanılmıyor çünkü başlıklar
    zaten 2-4 kelime.
    """
    title_terms = {stem for stem, _ in _tokenize_pairs(title or "")} - _TITLE_IGNORE
    if not title_terms:
        return 1.0
    overlap = len(title_terms & cv_unique) / len(title_terms)
    return TITLE_FLOOR + (1.0 - TITLE_FLOOR) * overlap


def _level_factor(cv_rank, job_rank):
    """Kıdem farkı çarpanı — ASİMETRİK.

    Senior bir adayın junior ilana bakması alakasızlık değil, o yüzden
    gap <= 0 cezasız. Ters yön (mezun -> senior ilan) ise gerçek bir
    alaka sorunu.
    """
    if cv_rank is None or job_rank is None:
        return 1.0                      # bilinmiyorsa ceza yok
    gap = job_rank - cv_rank
    if gap <= 0:
        return 1.0
    if gap == 1:
        return 0.70
    if gap == 2:
        return 0.40
    return 0.20


_YEARS_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:-|–|to|ile)?\s*(\d{1,2})?\s*\+?\s*"
    r"(?:years?|yrs?|yil|yillar|yillik)"
)


def _required_years(text):
    """İlanın istediği en düşük deneyim yılı. Bulunamazsa None."""
    found = []
    for match in _YEARS_RE.finditer(_fold(text or "")):
        n = int(match.group(1))
        if 0 <= n <= 40:
            found.append(n)
    return min(found) if found else None


def _years_factor(cv_years, description):
    """İlanın istediği yıl, adayın deneyiminden çok fazlaysa puanı düşür.

    Üstten sınırlı (YEARS_FLOOR): giriş seviyesi ilanlar da sık sık
    "2+ yıl" yazar, bu yüzden iyi bir eşleşme tamamen sıfırlanmamalı.
    """
    if cv_years is None:
        return 1.0
    required = _required_years(description)
    if not required:
        return 1.0
    shortfall = required - cv_years
    if shortfall <= 0:
        return 1.0
    return max(YEARS_FLOOR, 1.0 - YEARS_STEP * shortfall)


def _gap_note(gap):
    """Kıdem farkını kullanıcıya söyleyen kısa metin."""
    if gap is None or gap <= 0:
        return ""
    if gap == 1:
        return "Bu ilan bulunduğun seviyenin bir kademe üstünde."
    if gap == 2:
        return "Bu ilan bulunduğun seviyenin iki kademe üstünde."
    return "Bu ilan bulunduğun seviyenin çok üstünde."


def _describe(matched, gaps, score, gap_note=""):
    """Yüzdenin yanına hangi yeteneklerin eşleştiğini/eksik olduğunu yazar."""
    parts = []
    if gap_note:
        parts.append(gap_note)

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
def analyze_matches(cv_text, jobs, cv_level=None, cv_years=None):
    """CV'yi ilan listesiyle yerel olarak karşılaştırır. Hiç API çağrısı yok.

    jobs: [{"Pozisyon": ..., "Aciklama": ..., "Deneyim_Seviyesi": ...}, ...]
    cv_level / cv_years: experience.cv_profile() çıktısı (opsiyonel).
        Verilmezse kıdem ve yıl düzeltmeleri uygulanmaz.
    Dönen: aynı sırayla
        [{"percentage": int, "analysis": str, "level_gap": int|None}, ...]

    Tüm ilanlar TEK SEFERDE verilmeli — IDF tüm korpus üzerinden hesaplanıyor,
    tek tek çağrılırsa IDF anlamsızlaşır.
    """
    jobs = list(jobs)
    if not jobs:
        return []

    # Tembel import: experience modülü bu modülden _fold alıyor, modül
    # seviyesinde import edersek döngüsel import olur.
    from backend.experience import LEVEL_RANK

    cv_rank = LEVEL_RANK.get(cv_level) if cv_level else None

    cv_pairs = _tokenize_pairs(cv_text or "")
    if not cv_pairs:
        return [{"percentage": 0, "analysis": "CV metni okunamadı veya boş.",
                 "level_gap": None} for _ in jobs]

    # Şirket adı kasıtlı olarak dışarıda: her ilanda benzersiz olduğu için
    # yüksek IDF alıp "eksik gereksinim" gibi görünerek puanı haksızca düşürüyor.
    job_pairs = [
        _tokenize_pairs(f"{j.get('Pozisyon', '')} {j.get('Aciklama', '')}")
        for j in jobs
    ]
    job_terms = [[stem for stem, _ in pairs] for pairs in job_pairs]

    df, idf, max_idf = _idf_table(job_terms)
    display = _display_map([cv_pairs] + job_pairs)

    cv_unique = {stem for stem, _ in cv_pairs}
    cv_tf = Counter(stem for stem, _ in cv_pairs)

    def idf_of(term):
        return idf.get(term, max_idf)

    # CV'deki hangi terimler "gerçek" yetenek? En az BİR ilanda geçenler.
    # Kişisel gürültü (e-posta, adres) hiçbir ilanda geçmez, böylece elenir.
    # Eskiden MIN_DF=2 isteniyordu; kısa Jooble snippet'larıyla bu, CV'nin
    # gerçek yeteneklerini (python, react) tamamen eliyor ve geriye sadece
    # "concepts", "knowledge" gibi dolgu kelimeleri kalıyordu.
    skills = [t for t in cv_unique if df.get(t, 0) >= 1] or list(cv_unique)

    # CV içi sıklıkla ağırlıkla: CV'sinde "python"u 3 kez yazan aday onu
    # gerçekten önemsiyordur; bir kez geçen "account" değil.
    skill_w = {t: idf_of(t) * cv_tf.get(t, 1) for t in skills}
    total_skill_weight = sum(skill_w.values()) or 1.0

    results = []
    for job, terms in zip(jobs, job_terms):
        job_set = set(terms)

        # Yön 1: CV'deki yeteneklerin ne kadarı bu ilanda var?
        skill_hit = sum(skill_w[t] for t in skills if t in job_set) / total_skill_weight

        # Yön 2: ilanın (jenerik olmayan) gereksinimlerinin ne kadarı CV'de var?
        req_terms = [t for t in job_set if df.get(t, 0) >= MIN_DF] or list(job_set)
        req_total = sum(idf_of(t) for t in req_terms) or 1.0
        req_hit = sum(idf_of(t) for t in req_terms if t in cv_unique) / req_total

        raw = SKILL_WEIGHT * skill_hit + REQ_WEIGHT * req_hit
        base = 100 * (raw ** CURVE)

        # Kıdem farkı: "Belirsiz" ilan veya bilinmeyen CV seviyesi -> None
        job_rank = LEVEL_RANK.get(job.get("Deneyim_Seviyesi"))
        gap = None if (cv_rank is None or job_rank is None) else job_rank - cv_rank

        # Yıl cezası, yalnızca seviye sınıflandırıcısı bu ilanı TAM olarak
        # adayın seviyesine koyduğunda atlanır (gap == 0). İki sinyal aynı
        # şeyi ölçüyor; sınıflandırıcı net konuştuğunda, uzun açıklamalarda
        # sıkça rastgele geçen "5+ yıl" ifadesi onu ezmemeli. Aksi halde
        # başlığı "Entry Level" olan bir ilan, şablon açıklaması yüzünden
        # gerçekten giriş seviyesi ilanların altına düşüyordu.
        years_factor = 1.0 if gap == 0 else _years_factor(
            cv_years, job.get("Aciklama", "")
        )

        # Üç çarpan da <= 1.0; taban puanı sadece aşağı çekebilirler.
        factor = (
            _title_factor(cv_unique, job.get("Pozisyon", ""))
            * _level_factor(cv_rank, job_rank)
            * years_factor
        )
        score = max(0, min(100, round(base * factor)))

        name = lambda t: display.get(t, t)  # noqa: E731
        matched = [name(t) for t in sorted((t for t in skills if t in job_set),
                                           key=lambda t: -skill_w[t])]
        gaps = [name(t) for t in sorted((t for t in req_terms if t not in cv_unique),
                                        key=lambda t: -idf_of(t))]
        results.append({
            "percentage": score,
            "analysis": _describe(matched, gaps, score, _gap_note(gap)),
            "level_gap": gap,
        })

    return results
