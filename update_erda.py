import sqlite3

# integrated.db 파일 연결
conn = sqlite3.connect("integrated.db")
cursor = conn.cursor()

# sol_erda_pieces 컬럼이 없다면 안전하게 추가
try:
    cursor.execute("ALTER TABLE attendance_users ADD COLUMN sol_erda_pieces INTEGER DEFAULT 0")
    conn.commit()
except sqlite3.OperationalError:
    # 이미 컬럼이 존재하는 경우 무시
    pass

# 모든 유저의 누적 출석일수(count)를 가져옴
cursor.execute("SELECT user_id, count FROM attendance_users")
rows = cursor.fetchall()

for user_id, count in rows:
    if count is None:
        count = 0
    
    # 7일마다 5개씩 계산
    earned_pieces = (count // 7) * 5
    
    # sol_erda_pieces 컬럼에 반영
    cursor.execute(
        "UPDATE attendance_users SET sol_erda_pieces = ? WHERE user_id = ?",
        (earned_pieces, user_id)
    )

conn.commit()
conn.close()
print("모든 유저의 누적 출석 기준 솔 에르다 조각 지급이 완료되었습니다!")