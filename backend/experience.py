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
