import sqlite3
import os

# 파일 존재 여부 확인
if not os.path.exists("attendance.db"):
    print("❌ attendance.db 파일을 찾을 수 없습니다.")
    exit()

if not os.path.exists("fishing_game.db"):
    print("❌ fishing_game.db 파일을 찾을 수 없습니다. (파일명이 다르면 코드를 수정해주세요)")
    exit()

print("🔄 데이터베이스 통합을 시작합니다...")

# 새로운 통합 데이터베이스 생성
conn = sqlite3.connect("integrated.db")
cursor = conn.cursor()

# 기존 두 DB를 각각 다른 이름(db1, db2)으로 불러오기
cursor.execute("ATTACH DATABASE 'attendance.db' AS db1")
cursor.execute("ATTACH DATABASE 'fishing_game.db' AS db2")

# 1. 출석체크 설정 및 유저 데이터 테이블 생성 (충돌 방지를 위해 이름 변경)
cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings AS 
    SELECT * FROM db1.settings
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS attendance_users AS 
    SELECT * FROM db1.users
""")

# 2. 낚시 게임 유저 데이터 테이블 생성
cursor.execute("""
    CREATE TABLE IF NOT EXISTS fishing_users AS 
    SELECT * FROM db2.users
""")

conn.commit()
conn.close()

print("✨ 성공적으로 'integrated.db' 파일로 통합되었습니다!")