import os
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor

# 1. 로컬 SQLite 연결
sqlite_conn = sqlite3.connect("fishing_game.db")
sqlite_conn.row_factory = sqlite3.Row
sqlite_cur = sqlite_conn.cursor()

sqlite_cur.execute("SELECT * FROM users;")
local_users = sqlite_cur.fetchall()
sqlite_cur.close()
sqlite_conn.close()

print(f"로컬에서 불러온 유저 수: {len(local_users)}명")

# 2. Supabase 연결 (여기에 본인의 Supabase 풀 접속 주소를 직접 입력하세요)
# 예시: "postgres://postgres.xxxx:비밀번호@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres"
DATABASE_URL = "postgresql://postgres.cdmuufovgkxcyaemlqbu:whdudwns159@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"

if not DATABASE_URL:
    raise ValueError("DATABASE_URL이 설정되지 않았습니다.")

pg_conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
pg_cur = pg_conn.cursor()

# 3. Supabase로 데이터 밀어넣기
for user in local_users:
    pg_cur.execute("""
        INSERT INTO users (user_id, level, exp, meso, sol_erda, region, rod, chests_opened)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            level = EXCLUDED.level,
            exp = EXCLUDED.exp,
            meso = EXCLUDED.meso,
            sol_erda = EXCLUDED.sol_erda,
            region = EXCLUDED.region,
            rod = EXCLUDED.rod,
            chests_opened = EXCLUDED.chests_opened;
    """, (
        user['user_id'], user['level'], user['exp'], user['meso'],
        user['sol_erda'], user['region'], user['rod'], user['chests_opened']
    ))

pg_conn.commit()
pg_cur.close()
pg_conn.close()
print("🎉 기존 로컬 데이터가 Supabase로 성공적으로 이전되었습니다!")