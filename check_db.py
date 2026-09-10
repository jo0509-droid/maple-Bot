import os
import psycopg2

# Supabase DB 주소 직접 입력
DATABASE_URL = "postgresql://postgres.cdmuufovgkxcyaemlqbu:whdudwns159!@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres"

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

cur.execute("SELECT user_id, meso, exp, level FROM users;")
rows = cur.fetchall()

print("=== Supabase DB 현재 저장 데이터 ===")
for r in rows:
  print(f"User ID: {r[0]} | Meso: {r[1]} | EXP: {r[2]} | Level: {r[3]}")

cur.close()
conn.close()
