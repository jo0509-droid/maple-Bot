# -*- coding: utf-8 -*-
"""
누적 출석일수가 초기화된 것을 복구하는 스크립트입니다.
integrated.db(백업)의 값과 현재 Supabase에 있는 값 중 "더 큰 쪽"을 유지합니다.
(배포 이후 실제로 출석한 사람이 있어도 그 진행분이 사라지지 않습니다)

사용법은 migrate_to_supabase.py와 동일합니다:
  1) 이 파일을 integrated.db와 같은 폴더에 저장
  2) pip install psycopg2-binary   (필요시)
  3) DATABASE_URL 환경변수 설정 (Render > Environment 탭 값 복사)
     Windows(PowerShell): $env:DATABASE_URL="..."
     macOS/Linux:          export DATABASE_URL="..."
  4) python restore_attendance.py
"""

import os
import sqlite3
import psycopg2

SQLITE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "integrated.db")
DATABASE_URL = os.getenv("DATABASE_URL")


def main():
    if not DATABASE_URL:
        print("❌ DATABASE_URL 환경변수가 설정되어 있지 않습니다.")
        return
    if not os.path.exists(SQLITE_PATH):
        print(f"❌ {SQLITE_PATH} 파일을 찾을 수 없습니다.")
        return

    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_cur = sqlite_conn.cursor()

    pg_conn = psycopg2.connect(DATABASE_URL)
    pg_cur = pg_conn.cursor()

    sqlite_cur.execute(
        "SELECT user_id, last_check, count, last_voice_at, sol_erda_pieces FROM attendance_users"
    )
    rows = sqlite_cur.fetchall()

    updated = 0
    for user_id, last_check, count, last_voice_at, sol_erda_pieces in rows:
        pg_cur.execute(
            """
            INSERT INTO attendance_users (user_id, last_check, count, last_voice_at, sol_erda_pieces)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                count = GREATEST(attendance_users.count, EXCLUDED.count),
                sol_erda_pieces = GREATEST(attendance_users.sol_erda_pieces, EXCLUDED.sol_erda_pieces),
                last_check = GREATEST(
                    COALESCE(attendance_users.last_check, ''),
                    COALESCE(EXCLUDED.last_check, '')
                ),
                last_voice_at = GREATEST(
                    COALESCE(attendance_users.last_voice_at, ''),
                    COALESCE(EXCLUDED.last_voice_at, '')
                )
            """,
            (user_id, last_check, count, last_voice_at, sol_erda_pieces),
        )
        updated += 1

    pg_conn.commit()
    print(f"✅ attendance_users 복구 완료: {updated}명 처리됨 (더 큰 값 기준으로 병합)")

    # settings도 혹시 몰라 함께 복구 (guild_id 충돌 시 새 값 유지)
    sqlite_cur.execute("SELECT guild_id, channel_id FROM settings")
    for guild_id, channel_id in sqlite_cur.fetchall():
        pg_cur.execute(
            """
            INSERT INTO settings (guild_id, channel_id) VALUES (%s, %s)
            ON CONFLICT (guild_id) DO NOTHING
            """,
            (guild_id, channel_id),
        )
    pg_conn.commit()

    sqlite_cur.close()
    sqlite_conn.close()
    pg_cur.close()
    pg_conn.close()

    print("🎉 복구 완료! Supabase Table Editor에서 attendance_users의 count 값을 확인해보세요.")


if __name__ == "__main__":
    main()
