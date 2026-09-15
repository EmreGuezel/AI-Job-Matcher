import sqlite3

# Vercel'in yazmaya izin verdiği tek geçici klasör
DB_PATH = "/tmp/jobs.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Vercel'de geçici bellek silinirse diye tabloları her bağlantıda kontrol et
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            cv_text TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            job_title TEXT,
            company TEXT,
            match_percentage INTEGER,
            analysis_text TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    conn.commit()
    
    return conn