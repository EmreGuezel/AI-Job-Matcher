import requests
import pandas as pd

# Yerel eşleştirme API çağrısı yapmadığı için ilan sayısını rahatça
# artırabiliyoruz — daha fazla ilan = daha isabetli IDF ve daha iyi eşleşme.
JOB_LIMIT = 25

# İlan açıklamaları ~5.000 karakter geliyor. CSV şişmesin diye kırpıyoruz;
# eşleştirme için ilk kısmı fazlasıyla yeterli.
DESCRIPTION_LIMIT = 4000


def scrape_jobs(keyword="python developer", location="USA"):
    url = "https://jsearch.p.rapidapi.com/search-v2"

    querystring = {
        "query": f"{keyword} in {location}",
        "num_pages": "1"
    }

    headers = {
        "X-RapidAPI-Key": "2ee9113eecmsh332153baca499f6p1fd4bcjsn09813801b13c",
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
    }

    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=60)
        response.raise_for_status()
        json_response = response.json()

        # JSON'daki doğru veri yolu: data sözlüğünün içindeki jobs listesi
        data_dict = json_response.get("data", {})
        job_list = data_dict.get("jobs", [])

        jobs_data = []
        for job in job_list[:JOB_LIMIT]:
            exp = job.get("job_required_experience", {})
            exp_level = "Belirtilmemiş"
            if isinstance(exp, dict) and exp.get("required_experience_in_months"):
                months = exp.get("required_experience_in_months")
                exp_level = f"{months // 12} Yıl" if months >= 12 else f"{months} Ay"

            city = job.get("job_city") or job.get("job_country") or "Uzaktan"
            link = job.get("job_apply_link") or "Link Bulunamadı"

            # Eşleştirme motorunun asıl beslendiği alan.
            # Yoksa başlık + şirketle idare et, motor tamamen boş kalmasın.
            description = (job.get("job_description") or "").strip()
            if not description:
                description = f"{job.get('job_title', '')} {job.get('employer_name', '')}"

            jobs_data.append({
                "Pozisyon": job.get("job_title", "Bilinmiyor"),
                "Şirket": job.get("employer_name", "Bilinmiyor"),
                "Şehir": city,
                "Deneyim_Seviyesi": exp_level,
                "Link": link,
                "Aciklama": description[:DESCRIPTION_LIMIT]
            })

        if not jobs_data:
            return f"Uyarı: '{keyword} in {location}' araması için şu an aktif ilan bulunamadı."

        df = pd.DataFrame(jobs_data)

        # Vercel için /tmp yolu
        file_path = "/tmp/jobs.csv"
        df.to_csv(file_path, index=False, encoding='utf-8-sig', sep=';')

        return f"BAŞARILI: {len(jobs_data)} adet gerçek ilan kaydedildi."

    except Exception as e:
        return f"Kazıma hatası: {str(e)}"


if __name__ == "__main__":
    print(scrape_jobs("python developer", "USA"))
