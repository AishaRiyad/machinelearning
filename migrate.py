import sqlite3

# الاتصال بقاعدة البيانات
conn = sqlite3.connect("app.db")
cur = conn.cursor()

# فحص إذا العمود موجود
cols = [r[1] for r in cur.execute("PRAGMA table_info(plans)")]
if "advice_json" not in cols:
    cur.execute("ALTER TABLE plans ADD COLUMN advice_json TEXT DEFAULT '[]';")
    print("✅ Added advice_json column to plans table")
else:
    print("ℹ️ advice_json column already exists")

conn.commit()
conn.close()
