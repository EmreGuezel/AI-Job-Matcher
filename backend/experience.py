"""İlan deneyim seviyesi sınıflandırıcı.

JSearch ve Jooble, LinkedIn'in seviye taksonomisini (f_E) desteklemiyor —
JSearch'te yalnızca employment_types=INTERN gerçek bir filtre. Bu yüzden
seviyeyi ilan başlığından (gerekirse açıklamadan) tahmin ediyoruz.

İki kural önemli:

1. Önce BAŞLIK taranır. Açıklama tek başına güvenilmez: "senior mühendislerle
   çalışacaksın" cümlesi bir pazarlama ilanını yanlışlıkla Mid-Senior yapıyor.

2. Açıklama yedeğinde DAHA DAR bir kelime listesi kullanılır. "senior", "lead",
   "experienced" gibi kelimeler açıklamada tesadüfen geçtiği için orada
   hiç aranmaz — sadece başlıkta geçerlidir.
"""

import re

from backend.ai_matcher import _fold

# LinkedIn'in deneyim seviyeleri
LEVELS = ["Internship", "Entry Level", "Associate", "Mid-Senior Level", "Director", "Executive"]

# Kıdem karşılaştırması için sıra (küçük = daha az kıdemli)
LEVEL_RANK = {level: i for i, level in enumerate(LEVELS)}

TR_LABELS = {
    "Internship": "Staj / Internship",
    "Entry Level": "Giriş Seviyesi / Entry Level",
    "Associate": "Associate",
    "Mid-Senior Level": "Mid-Senior Level",
    "Director": "Direktör / Director",
    "Executive": "Üst Yönetim / Executive",
}

UNKNOWN = "Belirsiz"

# Kelime sonundaki "*" = Türkçe ek toleransı ("mudur*" -> müdür, müdürü, müdürlük).
# İngilizce kelimelerde kelime sınırı kullanılır; yoksa "vp" -> "vpn" gibi
# hatalı eşleşmeler olur.
#
# (seviye, başlıkta_arananlar, açıklamada_arananlar)
# Sıra önemli: "Senior Director" -> Director, "Junior Associate" -> Associate
# çıkması için yüksek kıdem önce kontrol edilir.
_PATTERNS = [
    ("Internship",
     ["intern", "internship", "stajyer*", "staj*"],
     ["intern", "internship", "stajyer*", "staj*"]),
    ("Executive",
     ["chief", "ceo", "cto", "cfo", "coo", "cio", "executive", "vice president",
      "vp", "genel mudur*", "yonetim kurulu"],
     ["chief", "ceo", "cto", "cfo", "executive", "vice president", "genel mudur*"]),
    # "baskan*" burada: "Bölüm Başkanı" bir departman yöneticisi = Director.
    # ("Genel Müdür" yukarıda Executive olarak kalıyor — Türkçe'de CEO karşılığı.)
    ("Director",
     ["director", "head of", "mudur*", "direktor*", "baskan*", "bolum baskani", "global head"],
     ["director", "head of", "mudur*", "direktor*", "baskan*"]),
    ("Mid-Senior Level",
     ["senior", "sr", "lead", "kidemli*", "principal", "staff", "team lead",
      "takim lideri", "mid-senior", "experienced"],
     []),  # açıklamada ARANMAZ — çok fazla yanlış eşleşme üretiyor
    ("Associate",
     ["associate"],
     ["associate"]),
    ("Entry Level",
     ["entry", "junior", "jr", "graduate", "new grad", "trainee", "yeni mezun*",
      "baslangic*", "giris seviyesi"],
     ["entry level", "junior", "new grad", "trainee", "yeni mezun*", "giris seviyesi"]),
]


def _compile(keyword):
    """Tek kelimeyi/ifadeyi regex'e çevirir. Sondaki '*' önek eşleşmesi demek."""
    prefix = keyword.endswith("*")
    if prefix:
        keyword = keyword[:-1]
    parts = [re.escape(part) for part in _fold(keyword).split()]
    pattern = r"\b" + r"\s+".join(parts)
    pattern += r"\w*" if prefix else r"\b"
    return re.compile(pattern)


_COMPILED = [
    (level, [_compile(k) for k in title_kws], [_compile(k) for k in desc_kws])
    for level, title_kws, desc_kws in _PATTERNS
]


def classify(title, description=""):
    """İlanı LinkedIn seviyelerinden birine eşler. Emin olamazsa None döner."""
    title_folded = _fold(title or "")

    for level, title_patterns, _ in _COMPILED:
        if any(p.search(title_folded) for p in title_patterns):
            return level

    # Yedek: başlıkta seviye yoksa açıklamaya bak (daha dar kelime listesiyle)
    desc_folded = _fold(description or "")
    if desc_folded:
        for level, _, desc_patterns in _COMPILED:
            if any(p.search(desc_folded) for p in desc_patterns):
                return level

    return None


def filter_by_level(jobs, level):
    """jobs: [{'Seviye': ...}, ...] — verilen seviyeye uyanları döner."""
    if not level or level == "all":
        return jobs
    return [j for j in jobs if j.get("Seviye") == level]


# ---------------------------------------------------------------------------
# CV TARAFI — adayın kendi kıdemi ve deneyim yılı
# ---------------------------------------------------------------------------
# Neden gerekli: kelime örtüşmesi kıdemi ayırt EDEMEZ. Bir bilgisayar
# mühendisliği mezununun CV'si ile bir "Senior Engineer" ilanı neredeyse tüm
# alan kelimelerini (python, backend, database, development) paylaşır. Bu
# yüzden kıdem, kelimelerden çıkarılmaya çalışılmak yerine açık bir sinyal
# olarak ölçülür.

# PDF'ler kelimeler arasına NBSP (U+00A0) koyuyor: "Work\xa0experience".
# _fold() bunu normalleştirmediği için \s tabanlı regex'ler ve boşlukla
# bölme başarısız olur — önce çevrilmeli.
_SPACE_CHARS = {"\xa0": " ", " ": " ", " ": " ", " ": " ", "\t": " "}


def _cv_normalize(text):
    """NBSP'leri normale çevirir, sonra Türkçe duyarlı küçük harfe indirir."""
    if not text:
        return ""
    for bad, good in _SPACE_CHARS.items():
        text = text.replace(bad, good)
    return _fold(text)


# Akademik tuzak: ABD üniversitelerinde bitirme projesi dersinin adı
# "Senior Design Project". Gerçek CV'de birebir "experience in senior design
# projects" geçiyor ve naif bir \bsenior\b taraması bu yeni mezunu
# Mid-Senior yapıyor. Kıdem taramasından ÖNCE bu ifadeler silinir.
_ACADEMIC_SENIOR_RE = re.compile(
    r"\bsenior\s+(?:design\s+|capstone\s+|graduation\s+)?(?:project|thesis|seminar|design)s?\b"
)

# Bölüm başlıkları — iş deneyimi aralığını kesmek için.
# Sıra önemli: en spesifik başlık önce aranır, çünkü çıplak "experience"
# kelimesi eğitim bölümünde de geçebiliyor ("...with experience in senior
# design projects").
_WORK_HEADINGS = (
    "work experience", "work history", "professional experience",
    "employment history", "is deneyimi", "deneyimlerim", "deneyim",
)
_END_HEADINGS = (
    "skills", "technical skills", "yetenekler", "yetkinlikler", "beceriler",
    "education", "egitim", "projects", "projeler", "certifications",
    "sertifikalar", "courses", "kurslar", "languages", "diller",
    "references", "referanslar", "interests", "hobiler", "about me",
    "hakkimda", "summary", "ozet",
)

# Tarih aralığı: "01/07/2024 - 01/08/2024", "03.2020 - 05.2022",
# "Jan 2020 - Mar 2021", "2020 - 2022"
_DATE_TOKEN = (
    r"(?:\d{1,2}[/.]\d{1,2}[/.]\d{2,4}"
    r"|\d{1,2}[/.]\d{4}"
    r"|\d{4}"
    r"|[a-z]{3,9}\.?\s+\d{4})"
)
_RANGE_RE = re.compile(rf"({_DATE_TOKEN})\s*[-–—]\s*({_DATE_TOKEN})")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6,
    "temmuz": 7, "agustos": 8, "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12,
}


def _parse_date(token):
    """Tarih parçasını (yıl, ay) çevirir. Çözülemezse None."""
    token = token.strip().rstrip(".")
    if not token:
        return None

    # "01/07/2024" veya "07.2024" — Türkçe CV'lerde gg/aa/yyyy
    if "/" in token or "." in token:
        parts = re.split(r"[/.]", token)
        if len(parts) == 3:
            a, b, year = parts
            # 17/07/2026 -> gün/ay/yıl. İlk parça 12'den büyükse kesin gün.
            month = int(b) if b.isdigit() else 1
            return (int(year), month if 1 <= month <= 12 else 1)
        if len(parts) == 2:
            month, year = parts
            m = int(month) if month.isdigit() else 1
            return (int(year), m if 1 <= m <= 12 else 1)

    # "Jan 2020" / "Temmuz 2020"
    bits = token.split()
    if len(bits) == 2 and bits[1].isdigit():
        return (int(bits[1]), _MONTHS.get(bits[0][:9], 1))

    # Sadece yıl
    if token.isdigit() and len(token) == 4:
        return (int(token), 1)

    return None


def _work_section(normalized):
    """İş deneyimi bölümünü başlıklardan kesip çıkarır.

    Bu olmadan eğitim tarih aralığı (ör. 5 yıllık lisans: 2021-2026) iş
    deneyimi olarak toplanır ve mezun kolayca "5 yıl deneyimli" görünür.
    """
    start = -1
    for heading in _WORK_HEADINGS:          # spesifikten genele doğru
        i = normalized.find(heading)
        if i != -1:
            start = i
            break
    if start == -1:
        return ""

    rest = normalized[start:]
    end = len(rest)
    for heading in _END_HEADINGS:
        i = rest.find(heading, 1)           # 1: başlığın kendisini atla
        if i != -1:
            end = min(end, i)
    return rest[:end]


def estimate_cv_years(cv_text):
    """CV'deki iş deneyimi bölümünden toplam deneyim yılını çıkarır.

    Savunmacı: aralık bulunamazsa 0.0 döner, tahmin uydurmaz.
    """
    section = _work_section(_cv_normalize(cv_text))
    if not section:
        return 0.0

    months = 0
    for match in _RANGE_RE.finditer(section):
        start = _parse_date(match.group(1))
        end = _parse_date(match.group(2))
        if not start or not end:
            continue
        span = (end[0] - start[0]) * 12 + (end[1] - start[1])
        # Negatif veya 40 yıldan uzun tek aralık şüpheli — sayma.
        # (Eğitim aralığına karşı asıl koruma _work_section; bu sadece
        #  bozuk veriye karşı bir akıl kontrolü.)
        if 0 < span <= 480:
            months += span

    return round(min(months, 540) / 12, 1)   # en fazla 45 yıl


# CV'de kıdem işareti arayan kalıplar (başlık tarafındakiyle aynı sözdizimi)
_EXEC_CV = ["chief", "ceo", "cto", "cfo", "coo", "cio", "founder", "owner",
            "vice president", "genel mudur*", "yonetim kurulu"]
_DIRECTOR_CV = ["director", "head of", "direktor*", "mudur*", "baskan*"]
_SENIOR_CV = ["senior", "sr", "lead", "principal", "staff", "kidemli*",
              "takim lideri", "team lead"]
_INTERN_CV = ["intern", "internship", "stajyer*", "staj*"]
_GRAD_CV = ["graduate", "graduated", "bachelor", "b.sc", "bs degree",
            "mezun*", "lisans", "university", "universite*"]

_EXEC_CV_P = [_compile(k) for k in _EXEC_CV]
_DIRECTOR_CV_P = [_compile(k) for k in _DIRECTOR_CV]
_SENIOR_CV_P = [_compile(k) for k in _SENIOR_CV]
_INTERN_CV_P = [_compile(k) for k in _INTERN_CV]
_GRAD_CV_P = [_compile(k) for k in _GRAD_CV]


def estimate_cv_level(cv_text, years=None):
    """Adayın kıdem seviyesini CV'den tahmin eder. Emin olamazsa None.

    Açık unvan işaretleri yoksa deneyim yılına düşer. Savunmacı: hiçbir
    sinyal yoksa None döner — çağıran taraf bu durumda filtre uygulamamalı.
    """
    text = _cv_normalize(cv_text)
    if not text.strip():
        return None

    # Akademik "senior design project" ifadeleri kıdem sanılmasın
    text_no_academic = _ACADEMIC_SENIOR_RE.sub(" ", text)

    if any(p.search(text_no_academic) for p in _EXEC_CV_P):
        return "Executive"
    if any(p.search(text_no_academic) for p in _DIRECTOR_CV_P):
        return "Director"
    if any(p.search(text_no_academic) for p in _SENIOR_CV_P):
        return "Mid-Senior Level"

    is_intern = any(p.search(text) for p in _INTERN_CV_P)
    is_grad = any(p.search(text) for p in _GRAD_CV_P)

    if is_intern:
        # Mezun + staj geçmişi = giriş seviyesi.
        # Hâlâ okuyan (mezuniyet işareti yok) = stajyer.
        return "Entry Level" if is_grad else "Internship"
    if is_grad:
        return "Entry Level"          # mezun ama hiç iş deneyimi yok

    # Unvan işareti yok — yıla bak
    if years is None:
        years = estimate_cv_years(cv_text)
    if years >= 5:
        return "Mid-Senior Level"
    if years >= 2:
        return "Associate"
    if years > 0:
        return "Entry Level"
    return None


def cv_profile(cv_text):
    """(seviye, yıl) ikilisini tek geçişte döner — yıl iki kez hesaplanmasın."""
    years = estimate_cv_years(cv_text)
    return estimate_cv_level(cv_text, years), years
