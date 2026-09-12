from datetime import datetime, timedelta, timezone
import os
import random
import sqlite3
import asyncio

import psycopg2
from psycopg2.extras import RealDictCursor

from flask import Flask
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ----------------------------------------
# 타임존 및 절대 경로 DB 설정 (통합 출석/게임/설정)
# ----------------------------------------
KST = timezone(timedelta(hours=9))
user_voice_seconds = {}

EXCLUDED_CHANNEL_IDS = [1498085152281067791]
CATEGORY_ID = [1530948235563372707]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "integrated.db")

def init_integrated_db():
    conn = sqlite3.connect(DB_PATH) 
    cursor = conn.cursor()
    
    # 출석 및 유저 설정 통합 테이블
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance_users (
            user_id INTEGER PRIMARY KEY,
            last_check TEXT,
            count INTEGER DEFAULT 0,
            last_voice_at TEXT,
            sol_erda_pieces INTEGER DEFAULT 0,
            birthday TEXT
        )
        """
    )
    
    # 서버별 설정 테이블 (기존 테이블 충돌 방지 및 안전한 기본키 보장)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS settings_new (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER
        )
        """
    )
    cursor.execute("INSERT OR IGNORE INTO settings_new SELECT guild_id, channel_id FROM settings")
    cursor.execute("DROP TABLE IF EXISTS settings")
    cursor.execute("ALTER TABLE settings_new RENAME TO settings")
    
    # 안전성 확보를 위한 컬럼 마이그레이션
    for col_def in [
        ("sol_erda_pieces", "INTEGER DEFAULT 0"),
        ("birthday", "TEXT")
    ]:
        try:
            cursor.execute(f"ALTER TABLE attendance_users ADD COLUMN {col_def[0]} {col_def[1]}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()

# ----------------------------------------
# Render 웹서버 설정
# ----------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

def run():
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

keep_alive()

# ----------------------------------------
# Supabase DB 세팅 (낚시 게임용)
# ----------------------------------------
DATABASE_URL = os.getenv('DATABASE_URL')

def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL 환경 변수가 설정되지 않았습니다. Render 대시보드를 확인하세요.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def _get_user_data_sync(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT level, exp, meso, 
               COALESCE(sol_erda, 0) as sol_erda, 
               COALESCE(region, '리스항구') as region, 
               COALESCE(rod, '나무 낚시대') as rod, 
               COALESCE(chests_opened, 0) as chests_opened 
        FROM users 
        WHERE user_id = %s
        LIMIT 1;
    """, (int(user_id),))
    row = cur.fetchone()

    if not row:
        try:
            cur.execute("""
                INSERT INTO users (user_id, level, exp, meso, sol_erda, region, rod, chests_opened) 
                VALUES (%s, 1, 0, 0, 0, '리스항구', '나무 낚시대', 0) 
                ON CONFLICT (user_id) DO NOTHING;
            """, (int(user_id),))
            conn.commit()
        except Exception:
            conn.rollback()

        cur.execute("""
            SELECT level, exp, meso, 
                   COALESCE(sol_erda, 0) as sol_erda, 
                   COALESCE(region, '리스항구') as region, 
                   COALESCE(rod, '나무 낚시대') as rod, 
                   COALESCE(chests_opened, 0) as chests_opened 
            FROM users 
            WHERE user_id = %s
            LIMIT 1;
        """, (int(user_id),))
        row = cur.fetchone()

    cur.close()
    conn.close()
    return dict(row)

async def get_user_data(user_id):
    return await asyncio.to_thread(_get_user_data_sync, user_id)

def _update_user_data_sync(user_id, data):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users 
        SET level = %s, exp = %s, meso = %s, sol_erda = %s, 
            region = %s, rod = %s, chests_opened = %s, 
            updated_at = CURRENT_TIMESTAMP
        WHERE user_id = %s;
    """, (
        data['level'], data['exp'], data['meso'], data['sol_erda'],
        data['region'], data['rod'], data['chests_opened'], int(user_id)
    ))
    conn.commit()
    cur.close()
    conn.close()

async def update_user_data(user_id, data):
    await asyncio.to_thread(_update_user_data_sync, user_id, data)

# ------------------------------------------
# 디스코드 봇 및 게임 정보 설정
# ------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

EXP_EVENT_MULTIPLIER = 1
MESO_EVENT_MULTIPLIER = 1

RODS = {
    "나무 낚시대": {"next": "실버 낚시대", "cost": 1000, "success_rate": 80, "fail_reduction": 0, "chest_bonus": 0.0},
    "실버 낚시대": {"next": "골드 낚시대", "cost": 10000, "success_rate": 50, "fail_reduction": 2, "chest_bonus": 0.1},
    "골드 낚시대": {"next": "다이아 낚시대", "cost": 100000, "success_rate": 10, "fail_reduction": 5, "chest_bonus": 0.2},
    "다이아 낚시대": {"next": "전설의 낚시대", "cost": 200000, "success_rate": 1, "fail_reduction": 10, "chest_bonus": 0.3},
    "전설의 낚시대": {"next": None, "cost": 500000, "success_rate": 0, "fail_reduction": 20, "chest_bonus": 0.4},
}

TREASURE_EXP = [
    {"amount": 10000, "weight": 50},
    {"amount": 30000, "weight": 30},
    {"amount": 50000, "weight": 15},
    {"amount": 100000, "weight": 5},
]

TREASURE_SOL_ERDA = [
    {"amount": 1, "weight": 50},
    {"amount": 2, "weight": 30},
    {"amount": 3, "weight": 15},
    {"amount": 4, "weight": 4},
    {"amount": 5, "weight": 1},
]

FORTUNES = [
    "오늘 하루는 왠지 기분 좋은 일로 가득할 거예요! 힘내세요! ✨",
    "당신은 생각보다 훨씬 더 멋지고 대단한 사람입니다. 🍀",
    "오늘 흘린 노력의 결실이 곧 달콤한 보상으로 돌아올 거예요! 🌟",
    "소소하지만 확실한 행복이 오늘 당신을 찾아올 거예요. ☕",
    "주변 사람들에게 당신의 밝은 에너지를 나누어주는 멋진 하루가 될 거예요! ☀️",
    "하고자 하는 모든 일이 순조롭게 풀리는 마법 같은 하루가 될 거예요! 🪄",
    "오늘 당신의 미소는 주변 사람까지 행복하게 만들 거예요. 😊"
]

SPOTS = {
    "리스항구": {
        "req_lvl": 1, "fail_chance": 10, "cooldown": 10, "chest_chance": 0.2,
        "fishes": [
            {"name": "👟 날아간 고무신", "exp": 30, "min_meso": 1, "max_meso": 6, "chance": 40},
            {"name": "🐟 리스항구 피라미", "exp": 80, "min_meso": 3, "max_meso": 8, "chance": 35},
            {"name": "🦀 스티어스 게", "exp": 150, "min_meso": 5, "max_meso": 10, "chance": 20},
            {"name": "🐙 대왕 문어", "exp": 350, "min_meso": 7, "max_meso": 15, "chance": 5},
        ]
    },
    "노틸러스": {
        "req_lvl": 201, "fail_chance": 15, "cooldown": 20, "chest_chance": 0.4,
        "fishes": [
            {"name": "🧹 선장의 청소자루", "exp": 200, "min_meso": 9, "max_meso": 12, "chance": 40},
            {"name": "🐠 해적선 날치", "exp": 450, "min_meso": 13, "max_meso": 16, "chance": 35},
            {"name": "🦑 카이린의 문어 요리", "exp": 900, "min_meso": 17, "max_meso": 20, "chance": 20},
            {"name": "🐋 노틸러스 수호고래", "exp": 2200, "min_meso": 21, "max_meso": 25, "chance": 5},
        ]
    },
    "아쿠아리움": {
        "req_lvl": 501, "fail_chance": 20, "cooldown": 30, "chest_chance": 0.6,
        "fishes": [
            {"name": "🌿 바다이끼 뭉치", "exp": 600, "min_meso": 26, "max_meso": 30, "chance": 40},
            {"name": "🐡 망둥어", "exp": 1300, "min_meso": 31, "max_meso": 35, "chance": 35},
            {"name": "🦈 샤크", "exp": 3000, "min_meso": 36, "max_meso": 40, "chance": 20},
            {"name": "🐋 심해의 피아누스", "exp": 7000, "min_meso": 41, "max_meso": 50, "chance": 5},
        ]
    },
    "에스페라": {
        "req_lvl": 601, "fail_chance": 25, "cooldown": 40, "chest_chance": 0.8,
        "fishes": [
            {"name": "💧 시작의 바다 결정", "exp": 1500, "min_meso": 51, "max_meso": 60, "chance": 40},
            {"name": "🪼 아르카나 집게벌레", "exp": 3500, "min_meso": 61, "max_meso": 70, "chance": 35},
            {"name": "🐟 집행자 날개고기", "exp": 7500, "min_meso": 71, "max_meso": 80, "chance": 20},
            {"name": "✨ 빛의 거울 고래", "exp": 18000, "min_meso": 81, "max_meso": 100, "chance": 5},
        ]
    },
    "셀라스": {
        "req_lvl": 801, "fail_chance": 30, "cooldown": 50, "chest_chance": 1.0,
        "fishes": [
            {"name": "🪸 별빛 심해 해초", "exp": 3000, "min_meso": 101, "max_meso": 120, "chance": 40},
            {"name": "🦑 잠기는 심해 오징어", "exp": 7000, "min_meso": 121, "max_meso": 140, "chance": 35},
            {"name": "🐋 별이 잠긴 고래", "exp": 15000, "min_meso": 141, "max_meso": 160, "chance": 20},
            {"name": "🌟 신기루의 심해룡", "exp": 38000, "min_meso": 161, "max_meso": 200, "chance": 5},
        ]
    },
    "검은바다": {
        "req_lvl": 1000, "fail_chance": 35, "cooldown": 60, "chest_chance": 1.2,
        "fishes": [
            {"name": "🍷 찬란한 연회의 파편", "exp": 6000, "min_meso": 201, "max_meso": 250, "chance": 40},
            {"name": "🕯️ 칠흑의 촛대 조각", "exp": 14000, "min_meso": 251, "max_meso": 300, "chance": 35},
            {"name": "👑 찬란한 주신 해마", "exp": 30000, "min_meso": 301, "max_meso": 400, "chance": 20},
            {"name": "🌌 태초의 창세신룡", "exp": 75000, "min_meso": 401, "max_meso": 500, "chance": 5},
        ]
    },
}

def get_max_exp(level):
    if level <= 200: return 1000
    elif level <= 500: return 15000
    elif level <= 600: return 40000
    elif level <= 800: return 80000
    elif level <= 999: return 250000
    else: return 999999999999

# ------------------------------------------
# UI 클래스들
# ------------------------------------------
class SaleModal(discord.ui.Modal, title="판매글 등록"):
    sale_amount = discord.ui.TextInput(label="판매 수량을 입력해주세요", placeholder="10억 메소는 10", required=True)
    sale_price = discord.ui.TextInput(label="1억당 가격을 입력해주세요", placeholder="1600원은 1600, 1500원은 1500으로 입력해주세요", required=True)
    sale_comment = discord.ui.TextInput(label="추가로 하실 말씀을 적어주세요", required=False, style=discord.TextStyle.paragraph)
    
    async def on_submit(self, interaction: discord.Interaction):
        seller = interaction.user
        sale_amount = self.sale_amount.value
        sale_price = self.sale_price.value
        sale_comment = self.sale_comment.value

        embed = discord.Embed(title="메소 팔아요", color=discord.Color.green())
        embed.add_field(name="", value=f"{sale_amount}억 메소를 억당 {sale_price}원으로 판매합니다", inline=False)
        embed.add_field(name="", value=sale_comment if sale_comment else "", inline=False)
        embed.set_footer(text="구매를 누르면 바로 채널이 생성되니 주의해주세요!")
        main_view = buybutton(seller=seller)
        other_view = Saleview(seller=seller)
        for child in other_view.children:
            main_view.add_item(child)

        await interaction.response.send_message(embed=embed, view=main_view, ephemeral=False)

class tradeoverview(discord.ui.View):
    def __init__(self, seller: discord.User | discord.Member, original_message: discord.Message):
        super().__init__(timeout=None)
        self.seller = seller
        self.original_message = original_message

    @discord.ui.button(label="거래 끝내기", style=discord.ButtonStyle.red)
    async def end_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user == self.seller:
            end_embed = discord.Embed(title="판매 완료", description="거래가 종료된 게시글입니다.", color=discord.Color.yellow())
            await self.original_message.edit(embed=end_embed, view=Saleview(seller=self.seller))
            await interaction.response.send_message("거래를 종료합니다. 1분 뒤 채널이 삭제 됩니다.", ephemeral=True)
            await asyncio.sleep(60)
            await interaction.channel.delete()
        else:
            await interaction.response.send_message("거래를 종료할 권한이 없습니다.", ephemeral=True)
            return

class Saleview(discord.ui.View):
    def __init__(self, seller: discord.User | discord.Member):
        super().__init__(timeout=None)
        self.seller = seller

    @discord.ui.button(label="나도 등록하기", style=discord.ButtonStyle.green, emoji="📝")
    async def register_sale(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SaleModal()
        await interaction.response.send_modal(modal)

class buybutton(discord.ui.View):
    def __init__(self, seller: discord.User | discord.Member):
        super().__init__(timeout=None)
        self.seller = seller

    @discord.ui.button(label="구매", style=discord.ButtonStyle.green)
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        buyer = interaction.user
        guild = interaction.guild
        if buyer == self.seller:
            await interaction.response.send_message("자신의 구매글은 구매할 수 없습니다.", ephemeral=True)
            return
        category = interaction.guild.get_channel(CATEGORY_ID[0])
        await interaction.response.defer(ephemeral=True)
        original_message = interaction.message
        trading_message = discord.Embed(title="거래중...", description="현재 거래가 진행 중인 게시글 입니다.")
        await original_message.edit(embed=trading_message, view=Saleview(seller=self.seller))
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            self.seller: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            buyer: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
        }
        channel_name = f"{interaction.user.name}님의 구매문의"
        new_channel = await interaction.guild.create_text_channel(name=channel_name, overwrites=overwrites, category=category)
        embed = discord.Embed(title="메소 구매 문의", description=f"{self.seller.mention}님과 {buyer.mention}님의 구매 문의 채널입니다.", color=discord.Color.green())
        view = tradeoverview(seller=self.seller, original_message=original_message)
        await new_channel.send(embed=embed, view=view)
        await new_channel.send(f"{self.seller.mention}{buyer.mention}")
        await interaction.followup.send(f"{new_channel.mention} 채널이 생성되었습니다.", ephemeral=True)

# ------------------------------------------
# 이벤트 및 백그라운드 태스크
# ------------------------------------------
@bot.event
async def on_ready():
    init_integrated_db()
    if not check_voice_time.is_running():
        check_voice_time.start()
    print(f'로그인 성공: {bot.user.name}')
    try:
        MY_GUILD = discord.Object(id=1498077956839313559)
        bot.tree.copy_global_to(guild=MY_GUILD)
        synced = await bot.tree.sync(guild=MY_GUILD)
        print(f"Synced {len(synced)} command(s) to specific guild.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    if after.channel and after.channel.id in EXCLUDED_CHANNEL_IDS:
        return
    if before.channel is None and after.channel is not None: 
        now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """ 
            INSERT INTO attendance_users (user_id, last_voice_at) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_voice_at = excluded.last_voice_at
            """,
            (member.id, now_str)
        )
        conn.commit()
        conn.close()

        if member.id not in user_voice_seconds:
            user_voice_seconds[member.id] = 0

async def check_and_send_birthdays(today_md):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM attendance_users WHERE birthday = ?", (today_md,))
    birthday_users = cursor.fetchall()
    
    cursor.execute("SELECT guild_id, channel_id FROM settings WHERE channel_id IS NOT NULL")
    settings_rows = cursor.fetchall()
    conn.close()

    if not birthday_users or not settings_rows:
        return

    for guild_id, channel_id in settings_rows:
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
        channel = guild.get_channel(channel_id)
        if not channel:
            continue

        for (user_id,) in birthday_users:
            member = guild.get_member(user_id)
            if member:
                embed = discord.Embed(
                    title="🎉 생일을 축하합니다! 🎂",
                    description=f"오늘은 {member.mention}님의 생일입니다! 모두 따뜻한 축하를 보내주세요! 🥳",
                    color=0xFF69B4
                )
                await channel.send(embed=embed)

@tasks.loop(minutes=1)
async def check_voice_time():
    now = datetime.now(KST)
    today_str = now.strftime("%Y-%m-%d")
    if now.hour == 0 and now.minute == 0:
        today_md = now.strftime("%m-%d")
        await check_and_send_birthdays(today_md)

        user_voice_seconds.clear()
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, last_voice_at, count FROM attendance_users WHERE count > 0"
        )
        rows = cursor.fetchall()
        for user_id, last_voice_at_str, count in rows:
            if last_voice_at_str:
                last_voice_dt = datetime.strptime(last_voice_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
                hours_diff = (now - last_voice_dt).total_seconds() / 3600
                if hours_diff >= 72:
                    if count % 30 != 0:
                        cursor.execute(
                            "UPDATE attendance_users SET count = 0 WHERE user_id = ?", (user_id,)
                        )
                    elif count % 30 == 0:
                        new_count = int((count // 30) * 30)
                        cursor.execute(
                            "UPDATE attendance_users SET count = ? WHERE user_id = ?", (new_count, user_id)
                        )
        conn.commit()
        conn.close()
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            for member in vc.members:
                if member.bot:
                    continue
                cursor.execute(
                    "SELECT last_check, count FROM attendance_users WHERE user_id = ?", (member.id,)
                )
                row = cursor.fetchone()
                if row and row[0] == today_str:
                    continue
                current_time = user_voice_seconds.get(member.id, 0) + 60
                user_voice_seconds[member.id] = current_time
                if current_time >= 600:
                    await process_attendance(member, today_str)
    conn.commit()
    conn.close()

async def process_attendance(member, today_str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT last_check, count, COALESCE(sol_erda_pieces, 0) FROM attendance_users WHERE user_id = ?", (member.id,)
    )
    row = cursor.fetchone()

    reward_msg = ""
    if row is None:
        count = 1
        pieces = 5 if count % 7 == 0 else 0
        if pieces > 0:
            reward_msg = "\n🎉 **누적 출석 7일 달성!** 솔 에르다 조각 **5개**가 지급되었습니다!"
        cursor.execute(
            "INSERT INTO attendance_users (user_id, last_check, count, sol_erda_pieces) VALUES (?, ?, ?, ?)",
            (member.id, today_str, count, pieces),
        )
    else:
        last_check, count, pieces = row
        if last_check != today_str:
            count += 1
            if count % 7 == 0:
                pieces += 5
                reward_msg = "\n🎉 **누적 출석 7일 달성!** 솔 에르다 조각 **5개**가 지급되었습니다!"
            cursor.execute(
                "UPDATE attendance_users SET last_check = ?, count = ?, sol_erda_pieces = ? WHERE user_id = ?",
                (today_str, count, pieces, member.id),
            )
    conn.commit()
    cursor.execute(
        "SELECT channel_id FROM settings WHERE guild_id = ?", (member.guild.id,)
    )
    channel_row = cursor.fetchone()
    conn.close()

    if not channel_row or not channel_row[0]:
        return
    channel = member.guild.get_channel(channel_row[0])
    if not channel:
        return

    remainder = count % 7
    days_left = 7 - remainder if remainder != 0 else 0
    if days_left == 0:
        days_left = 7

    base_desc = (
        f"🗓️ 오늘 날짜: `{today_str}`\n"
        f"📊 누적 출석 일수: **{count}일**\n"
        f"💎 보유 솔 에르다 조각: **{pieces}개**\n"
        f"⏳ 다음 보상까지: 앞으로 **{days_left}일** 남았습니다."
    )
    if reward_msg:
        base_desc += f"\n{reward_msg}"

    if count == 1: 
        await channel.send(
            f"🐣 {member.mention}님 천 리 길도 한 걸음부터입니다!\n" + base_desc
        )
    elif count % 15 == 0 and count < 30:
        await channel.send(
            f"🐣 {member.mention}님 시작이 반입니다! 축하드립니다!\n" + base_desc
        )
    elif count % 30 == 0 and count != 0:
        await channel.send(
            f"🐣 {member.mention}님 수고하셨습니다! 대단합니다!\n" + base_desc
        )
    else:
        await channel.send(
            f"✅ {member.mention}님 오늘 출석이 완료되었습니다!\n" + base_desc
        )

# ------------------------------------------
# 통합 에러 핸들러
# ------------------------------------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        seconds = int(error.retry_after)
        msg = f"⏳ 미끼를 준비 중입니다... **{seconds}초 후**에 다시 낚시할 수 있습니다!"
    elif isinstance(error, app_commands.MissingPermissions):
        msg = "❌ 이 명령어를 실행할 관리자 권한이 부족합니다."
    else:
        msg = f"❌ 명령어 실행 중 오류가 발생했습니다: {error}"

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)

# ------------------------------------------
# 명령어 모음
# ------------------------------------------
@bot.tree.command(name="출석채널지정", description="출석을 기록할 채널을 지정합니다.")
@app_commands.checks.has_permissions(administrator=True)
async def set_attendance_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = interaction.guild.id
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO settings (guild_id, channel_id) VALUES (?, ?) 
           ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id""",
        (guild_id, channel.id),
    )
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 출석 채널이 {channel.mention}로 성공적으로 설정되었습니다.", ephemeral=True)

@bot.tree.command(name="판매글등록", description="판매글을 등록합니다.")
@app_commands.checks.has_permissions(administrator=True)
async def register_sale_post(interaction: discord.Interaction):
    embed = discord.Embed(title="판매글 등록", description="판매글을 등록합니다.", color=discord.Color.green())
    embed.add_field(name="판매글 등록하기", value="판매글등록을 하기 위해서 아래 버튼을 눌러주세요", inline=False)
    await interaction.response.send_message(embed=embed, view=Saleview(seller=interaction.user), ephemeral=False)

@bot.tree.command(name="출석확인", description="누적 출석 일수와 솔 에르다 조각 정보를 확인합니다.")
async def check_attendance(interaction: discord.Interaction):
    user_id = interaction.user.id
    today_str = datetime.now(KST).strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT last_check, count, COALESCE(sol_erda_pieces, 0) FROM attendance_users WHERE user_id = ?", (user_id,)
    )
    row = cursor.fetchone()

    if row is None:
        cursor.execute(
            "INSERT INTO attendance_users (user_id, last_check, count, sol_erda_pieces) VALUES (?, ?, ?, ?)",
            (user_id, today_str, 0, 0),
        )
        conn.commit()
        count = 0
        pieces = 0
    else:
        last_check, count, pieces = row

    conn.close()

    remainder = count % 7
    days_left = 7 - remainder if remainder != 0 else 0
    if days_left == 0:
        days_left = 7

    await interaction.response.send_message(
        f"✅ {interaction.user.mention}님의 출석 및 조각 정보입니다.\n"
        f"🗓️ 오늘 날짜: `{today_str}`\n"
        f"📊 누적 출석 일수: **{count}일**\n"
        f"💎 보유 솔 에르다 조각: **{pieces}개**\n"
        f"⏳ 다음 보상까지: 앞으로 **{days_left}일** 남았습니다."
    )

@bot.tree.command(name="출석수정", description="관리자 권한으로 특정 유저의 누적 출석 일수나 솔 에르다 조각 개수를 강제로 수정합니다.")
@app_commands.describe(
    user="대상이 되는 유저",
    days="변경할 누적 출석 일수",
    pieces="변경할 솔 에르다 조각 개수"
)
@app_commands.checks.has_permissions(administrator=True)
async def modify_attendance(interaction: discord.Interaction, user: discord.Member, days: int, pieces: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT user_id FROM attendance_users WHERE user_id = ?", (user.id,))
    row = cursor.fetchone()
    
    if row:
        cursor.execute("UPDATE attendance_users SET count = ?, sol_erda_pieces = ? WHERE user_id = ?", (days, pieces, user.id))
    else:
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        cursor.execute("INSERT INTO attendance_users (user_id, last_check, count, sol_erda_pieces) VALUES (?, ?, ?, ?)", (user.id, today_str, days, pieces))
        
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ {user.mention}님의 누적 출석 일수가 **{days}일**, 솔 에르다 조각이 **{pieces}개**로 강제 수정되었습니다.", ephemeral=True)

@bot.tree.command(name="생일등록", description="본인의 생일을 등록합니다. (형식: MM-DD)")
@app_commands.describe(날짜="월-일 형식으로 입력하세요 (예: 12-25)")
async def register_birthday(interaction: discord.Interaction, 날짜: str):
    if len(날짜) != 5 or 날짜[2] != '-':
        await interaction.response.send_message("❌ 형식에 맞지 않습니다. `MM-DD` 형식으로 입력해주세요 (예: `12-25`).", ephemeral=True)
        return

    user_id = interaction.user.id
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT user_id FROM attendance_users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if row is None:
        cursor.execute(
            "INSERT INTO attendance_users (user_id, count, sol_erda_pieces, birthday) VALUES (?, 0, 0, ?)",
            (user_id, 날짜)
        )
    else:
        cursor.execute(
            "UPDATE attendance_users SET birthday = ? WHERE user_id = ?",
            (날짜, user_id)
        )
        
    conn.commit()
    conn.close()
    
    await interaction.response.send_message(f"✅ 생일이 `{날짜}`로 성공적으로 등록되었습니다!", ephemeral=True)

USER_COOLDOWNS = {}

@bot.tree.command(name="포춘쿠키", description="오늘의 행운의 포춘쿠키를 뽑고 행복한 문구를 확인합니다.")
async def fortune_cookie(interaction: discord.Interaction):
    selected_fortune = random.choice(FORTUNES)
    embed = discord.Embed(
        title="🥠 오늘의 포춘쿠키", 
        description=selected_fortune, 
        color=0xF1C40F
    )
    embed.set_footer(text=f"요청자: {interaction.user.display_name}", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="낚시", description="현재 낚시터에서 낚시를 진행합니다.")
async def fish(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        user_id = interaction.user.id
        now = datetime.now()

        user_info = await get_user_data(user_id)
        region = user_info["region"]
        spot_info = SPOTS.get(region, SPOTS["리스항구"])
        cooldown_seconds = spot_info.get("cooldown", 10)

        if user_id in USER_COOLDOWNS:
            elapsed = (now - USER_COOLDOWNS[user_id]).total_seconds()
            if elapsed < cooldown_seconds:
                remaining = int(cooldown_seconds - elapsed)
                await interaction.followup.send(
                    f"⏳ 미끼를 준비 중입니다... **{remaining}초 후**에 다시 낚시할 수 있습니다!", 
                    ephemeral=True
                )
                return

        USER_COOLDOWNS[user_id] = now

        rod_name = user_info["rod"]
        rod_info = RODS.get(rod_name, RODS["나무 낚시대"])

        fail_chance = max(0, spot_info["fail_chance"] - rod_info["fail_reduction"])

        if random.randint(1, 100) <= fail_chance:
            embed = discord.Embed(
                title="🌊 찌가 힘없이 흘러갑니다...",
                description=f"**[{region}]**에서 낚시에 실패했습니다! (실패율 {fail_chance}%)",
                color=0x95A5A6
            )
            await interaction.followup.send(embed=embed)
            return

        chest_chance = spot_info["chest_chance"] + rod_info["chest_bonus"]
        if random.uniform(0, 100) <= chest_chance:
            user_info["chests_opened"] += 1
            
            if random.choice([True, False]):
                reward_exp = random.choices(
                    [t["amount"] for t in TREASURE_EXP],
                    weights=[t["weight"] for t in TREASURE_EXP]
                )[0]
                user_info["exp"] += reward_exp
                reward_text = f"✨ **{reward_exp:,} EXP**"
            else:
                reward_erda = random.choices(
                    [t["amount"] for t in TREASURE_SOL_ERDA],
                    weights=[t["weight"] for t in TREASURE_SOL_ERDA]
                )[0]
                user_info["sol_erda"] += reward_erda
                reward_text = f"💎 **솔 에르다 조각 {reward_erda}개**"

            leveled_up = False
            max_exp = get_max_exp(user_info["level"])
            while user_info["exp"] >= max_exp and user_info["level"] < 1000:
                user_info["exp"] -= max_exp
                user_info["level"] += 1
                max_exp = get_max_exp(user_info["level"])
                leveled_up = True

            await update_user_data(user_id, user_info)

            embed = discord.Embed(title="🎁 보물상자를 낚았습니다!", color=0x9B59B6)
            embed.add_field(name="상자 보상", value=reward_text, inline=False)
            if leveled_up:
                embed.add_field(name="🎉 레벨 업!", value=f"**Lv. {user_info['level']}** 달성!", inline=False)
            await interaction.followup.send(embed=embed)
            return

        fishes = spot_info["fishes"]
        fish_picked = random.choices(fishes, weights=[f["chance"] for f in fishes])[0]

        gained_exp = fish_picked["exp"] * EXP_EVENT_MULTIPLIER
        gained_meso = random.randint(fish_picked["min_meso"], fish_picked["max_meso"]) * MESO_EVENT_MULTIPLIER

        user_info["exp"] += gained_exp
        user_info["meso"] += gained_meso

        leveled_up = False
        max_exp = get_max_exp(user_info["level"])
        while user_info["exp"] >= max_exp and user_info["level"] < 1000:
            user_info["exp"] -= max_exp
            user_info["level"] += 1
            max_exp = get_max_exp(user_info["level"])
            leveled_up = True

        await update_user_data(user_id, user_info)

        embed = discord.Embed(title=f"🐟 낚시 성공! - [{fish_picked['name']}]", color=0x2ECC71)
        embed.add_field(name="획득 경험치", value=f"✨ **+{gained_exp:,} EXP**", inline=True)
        embed.add_field(name="획득 메소", value=f"💰 **+{gained_meso:,} 메소**", inline=True)
        
        if leveled_up:
            embed.add_field(name="🎉 LEVEL UP!", value=f"축하합니다! **Lv. {user_info['level']}**에 도달했습니다!", inline=False)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"❌ 낚시 중 오류가 발생했습니다:\n```{e}```")

# 추가 낚시 관련 보조 명령어들 (정보, 낚시터이동, 낚시대강화 등)
@bot.tree.command(name="내정보", description="현재 캐릭터의 레벨, 경험치, 메소, 장비 정보를 확인합니다.")
async def my_info(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        user_info = await get_user_data(interaction.user.id)
        max_exp = get_max_exp(user_info["level"])
        embed = discord.Embed(title=f"📊 {interaction.user.display_name}님의 캐릭터 정보", color=0x3498DB)
        embed.add_field(name="레벨", value=f"Lv. {user_info['level']}", inline=True)
        embed.add_field(name="경험치", value=f"{user_info['exp']:,} / {max_exp:,}", inline=True)
        embed.add_field(name="메소", value=f"{user_info['meso']:,} 메소", inline=True)
        embed.add_field(name="솔 에르다", value=f"{user_info['sol_erda']} 개", inline=True)
        embed.add_field(name="현재 지역", value=user_info["region"], inline=True)
        embed.add_field(name="보유 낚시대", value=user_info["rod"], inline=True)
        embed.set_footer(text=f"상자 오픈 횟수: {user_info['chests_opened']}회")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ 정보를 불러오는 중 오류가 발생했습니다:\n```{e}```", ephemeral=True)

@bot.tree.command(name="낚시터이동", description="원하는 낚시터로 이동합니다.")
@app_commands.describe(지역="이동할 낚시터 이름을 입력하세요")
async def move_spot(interaction: discord.Interaction, 지역: str):
    await interaction.response.defer(ephemeral=True)
    if 지역 not in SPOTS:
        await interaction.followup.send(f"❌ 존재하지 않는 낚시터입니다. 가능한 지역: {', '.join(SPOTS.keys())}", ephemeral=True)
        return
    user_info = await get_user_data(interaction.user.id)
    req_lvl = SPOTS[지역]["req_lvl"]
    if user_info["level"] < req_lvl:
        await interaction.followup.send(f"❌ 레벨이 부족하여 **{지역}**(입장 제한: Lv. {req_lvl})에 갈 수 없습니다.", ephemeral=True)
        return
    user_info["region"] = 지역
    await update_user_data(interaction.user.id, user_info)
    await interaction.followup.send(f"✅ 성공적으로 **{지역}** 낚시터로 이동했습니다!", ephemeral=True)

# ------------------------------------------
# 봇 실행부 (토큰 처리)
# ------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("디스코드 토큰이 설정되지 않았습니다.")