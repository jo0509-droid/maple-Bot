# -*- coding: utf-8 -*-
"""
integrated.db (SQLite)의 attendance_users, settings 데이터를
Supabase(Postgres)로 한 번만 옮기는 스크립트입니다.

사용법 (VS Code 터미널에서):
  1) 이 파일을 main.py와 같은 폴더에 저장하세요. (integrated.db도 같은 폴더에 있어야 합니다)
  2) 필요한 패키지 설치 (이미 설치되어 있다면 생략 가능):
       pip install psycopg2-binary
  3) Render 대시보드 > Environment 탭에서 DATABASE_URL 값을 복사해서,
     터미널에 아래처럼 환경변수로 설정합니다.

     Windows (PowerShell):
       $env:DATABASE_URL="여기에_Supabase_연결_문자열_붙여넣기"

     macOS / Linux:
       export DATABASE_URL="여기에_Supabase_연결_문자열_붙여넣기"

  4) 스크립트 실행:
       python migrate_to_supabase.py

  5) 실행 결과 로그를 확인하고, 문제 없으면 그걸로 끝입니다.
     (이 스크립트는 여러 번 실행해도 안전합니다 - 이미 있는 데이터는 건너뜁니다)
"""

import os
import sqlite3
import psycopg2

SQLITE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "integrated.db")
DATABASE_URL = os.getenv("postgresql://postgres.cdmuufovgkxcyaemlqbu:whdudwns159@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres")


def main():
    if not DATABASE_URL:
        print("❌ DATABASE_URL 환경변수가 설정되어 있지 않습니다. 위 안내대로 먼저 설정해주세요.")
        return

    if not os.path.exists(SQLITE_PATH):
        print(f"❌ {SQLITE_PATH} 파일을 찾을 수 없습니다. integrated.db를 이 스크립트와 같은 폴더에 두세요.")
        return

    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_cur = sqlite_conn.cursor()

    pg_conn = psycopg2.connect(DATABASE_URL)
    pg_cur = pg_conn.cursor()

    # 1) 대상 테이블이 없으면 새로 만듭니다 (main.py의 스키마와 동일)
    pg_cur.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance_users (
            user_id BIGINT PRIMARY KEY,
            last_check TEXT,
            count INTEGER DEFAULT 0,
            last_voice_at TEXT,
            sol_erda_pieces INTEGER DEFAULT 0
        )
        """
    )
    pg_cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            guild_id BIGINT PRIMARY KEY,
            channel_id BIGINT
        )
        """
    )
    pg_conn.commit()

    # 2) attendance_users 이전
    sqlite_cur.execute(
        "SELECT user_id, last_check, count, last_voice_at, sol_erda_pieces FROM attendance_users"
    )
    rows = sqlite_cur.fetchall()

    inserted, skipped = 0, 0
    for user_id, last_check, count, last_voice_at, sol_erda_pieces in rows:
        pg_cur.execute(
            """
            INSERT INTO attendance_users (user_id, last_check, count, last_voice_at, sol_erda_pieces)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id, last_check, count, last_voice_at, sol_erda_pieces),
        )
        if pg_cur.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    pg_conn.commit()
    print(f"✅ attendance_users: {inserted}명 새로 추가, {skipped}명은 이미 있어서 건너뜀 (총 {len(rows)}명)")

    # 3) settings 이전
    sqlite_cur.execute("SELECT guild_id, channel_id FROM settings")
    settings_rows = sqlite_cur.fetchall()

    s_inserted, s_skipped = 0, 0
    for guild_id, channel_id in settings_rows:
        pg_cur.execute(
            """
            INSERT INTO settings (guild_id, channel_id)
            VALUES (%s, %s)
            ON CONFLICT (guild_id) DO NOTHING
            """,
            (guild_id, channel_id),
        )
        if pg_cur.rowcount > 0:
            s_inserted += 1
        else:
            s_skipped += 1

    pg_conn.commit()
    print(f"✅ settings: {s_inserted}건 새로 추가, {s_skipped}건은 이미 있어서 건너뜀 (총 {len(settings_rows)}건)")

    sqlite_cur.close()
    sqlite_conn.close()
    pg_cur.close()
    pg_conn.close()

    print("\n🎉 마이그레이션 완료! Supabase 대시보드의 Table Editor에서 attendance_users, settings 테이블을 확인해보세요.")


if __name__ == "__main__":
    main()
