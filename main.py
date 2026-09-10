import asyncio
from datetime import datetime
import os
import random

import psycopg2
from psycopg2.extras import RealDictCursor

from flask import Flask
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands

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
# Supabase DB 세팅
# ----------------------------------------
DATABASE_URL = os.getenv('DATABASE_URL')

def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL 환경 변수가 설정되지 않았습니다. Render 대시보드를 확인하세요.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def _get_user_data_sync(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. 기존 유저 정보 조회 (기존 데이터/랭킹 100% 보존)
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

    # 2. 정보가 없으면 신규 생성 (ON CONFLICT 구문으로 중복 및 초기화 방지)
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

        # 다시 조회하여 확실하게 가져오기
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

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    try:
        MY_GUILD = discord.Object(id=1498077956839313559)
        bot.tree.copy_global_to(guild=MY_GUILD)
        synced = await bot.tree.sync(guild=MY_GUILD)
        print(f"Synced {len(synced)} command(s) to specific guild.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        seconds = int(error.retry_after)
        msg = f"⏳ 미끼를 준비 중입니다... **{seconds}초 후**에 다시 낚시할 수 있습니다!"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    elif isinstance(error, app_commands.MissingPermissions):
        if interaction.response.is_done():
            await interaction.followup.send("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
    else:
        raise error

USER_COOLDOWNS = {}

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

@bot.tree.command(name="레벨랭킹", description="가장 레벨이 높은 모험가 TOP 10을 확인합니다.")
async def level_ranking(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT user_id, level, exp FROM users ORDER BY level DESC, exp DESC LIMIT 10")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        embed = discord.Embed(title="🏆 메이플 낚시왕 레벨 랭킹 TOP 10", color=0xF1C40F)

        if not rows:
            embed.description = "아직 등록된 모험가가 없습니다."
        else:
            rank_text = ""
            medals = ["🥇", "🥈", "🥉"]
            for idx, row in enumerate(rows, start=1):
                u_id, level, exp = row['user_id'], row['level'], row['exp']
                medal = medals[idx - 1] if idx <= 3 else f"**{idx}.**"

                user_obj = bot.get_user(u_id)
                if not user_obj:
                    try:
                        user_obj = await bot.fetch_user(u_id)
                    except:
                        pass
                user_name = user_obj.display_name if user_obj else f"유저({u_id})"

                rank_text += f"{medal} **{user_name}** - Lv. {level} ({exp:,} EXP)\n"

            embed.description = rank_text

        await interaction.followup.send(embed=embed)

    except Exception as e:
        print(f"레벨랭킹 오류: {e}")
        await interaction.followup.send("❌ 랭킹을 불러오는 데 실패했습니다.", ephemeral=True)

@bot.tree.command(name="상자랭킹", description="보물상자를 가장 많이 획득한 모험가 TOP 10을 확인합니다.")
async def chest_ranking(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT user_id, chests_opened FROM users WHERE chests_opened > 0 ORDER BY chests_opened DESC LIMIT 10")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        embed = discord.Embed(title="🎁 행운의 보물상자 랭킹 TOP 10", color=0x9B59B6)

        if not rows:
            embed.description = "아직 보물상자를 낚은 모험가가 없습니다."
        else:
            rank_text = ""
            medals = ["🥇", "🥈", "🥉"]
            for idx, row in enumerate(rows, start=1):
                u_id, chests = row['user_id'], row['chests_opened']
                medal = medals[idx - 1] if idx <= 3 else f"**{idx}.**"

                user_obj = bot.get_user(u_id)
                if not user_obj:
                    try:
                        user_obj = await bot.fetch_user(u_id)
                    except:
                        pass
                user_name = user_obj.display_name if user_obj else f"유저({u_id})"

                rank_text += f"{medal} **{user_name}** - 총 **{chests:,}개** 획득\n"

            embed.description = rank_text

        await interaction.followup.send(embed=embed)

    except Exception as e:
        print(f"상자랭킹 오류: {e}")
        await interaction.followup.send("❌ 상자 랭킹을 불러오는 데 실패했습니다.", ephemeral=True)

@bot.tree.command(name="프로필", description="내 정보 또는 다른 유저의 정보를 확인합니다.")
async def profile(interaction: discord.Interaction, 유저: discord.User = None):
    await interaction.response.defer()
    target_user = 유저 if 유저 else interaction.user
    user_info = await get_user_data(target_user.id)
    max_exp = get_max_exp(user_info["level"])
    exp_str = f"{user_info['exp']:,} / {max_exp:,} EXP" if user_info["level"] < 1000 else "MAX"
    current_rod = user_info["rod"]

    embed = discord.Embed(title=f"🍁 {target_user.name}님의 모험가 정보", color=0xF1C40F)
    embed.add_field(name="레벨", value=f"Lv. {user_info['level']}", inline=True)
    embed.add_field(name="경험치", value=exp_str, inline=True)
    embed.add_field(name="착용 낚시대", value=f"🎣 **{current_rod}**", inline=False)
    embed.add_field(name="현재 낚시터", value=f"📍 {user_info['region']}", inline=False)
    embed.add_field(name="보유 재화", value=f"💰 {user_info['meso']:,} 메소 | 💎 조각: {user_info['sol_erda']:,}개", inline=False)
    embed.add_field(name="획득한 보물상자", value=f"🎁 **{user_info['chests_opened']:,}개**", inline=False)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="조각구매", description="메소를 소모하여 솔 에르다 조각을 구매합니다. (1개당 10만 메소)")
async def buy_erda(interaction: discord.Interaction, 수량: int):
    await interaction.response.defer()
    if 수량 <= 0:
        await interaction.followup.send("❌ 구매 수량은 1개 이상이어야 합니다.", ephemeral=True)
        return

    user_id = interaction.user.id
    user_info = await get_user_data(user_id)

    cost = 수량 * 100000
    if user_info["meso"] < cost:
        await interaction.followup.send(f"❌ 메소가 부족합니다! (필요: {cost:,} 메소)", ephemeral=True)
        return

    user_info["meso"] -= cost
    user_info["sol_erda"] += 수량
    await update_user_data(user_id, user_info)
    
    msg = f"💰 **{cost:,} 메소**를 소모하여 솔 에르다 조각 **{수량}개**를 구매했습니다!"
    await interaction.followup.send(msg)

@bot.tree.command(name="낚시터목록", description="낚시터 목록을 확인합니다.")
async def spot_list(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = discord.Embed(title="🗺️ 메이플 낚시터 안내판", color=0x3498DB)
    for name, info in SPOTS.items():
        embed.add_field(
            name=f"📍 {name}",
            value=f"입장 제한: **Lv. {info['req_lvl']}**\n쿨타임: **{info['cooldown']}초**\n기본 상자확률: **{info['chest_chance']}%**",
            inline=True
        )
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="이동", description="원하는 낚시터로 이동합니다.")
@app_commands.choices(장소=[app_commands.Choice(name=s, value=s) for s in SPOTS.keys()])
async def move_spot(interaction: discord.Interaction, 장소: str):
    await interaction.response.defer()
    user_info = await get_user_data(interaction.user.id)
    spot_info = SPOTS.get(장소)

    if user_info["level"] < spot_info["req_lvl"]:
        await interaction.followup.send(
            f"❌ 레벨이 부족합니다! ({장소} 필요 레벨: Lv. {spot_info['req_lvl']})", ephemeral=True
        )
        return

    user_info["region"] = 장소
    await update_user_data(interaction.user.id, user_info)
    await interaction.followup.send(f"⛵ **[{장소}]**(으)로 이동했습니다!")

@bot.tree.command(name="강화", description="메소를 소모하여 낚시대를 강화합니다.")
async def upgrade_rod(interaction: discord.Interaction):
    await interaction.response.defer()
    user_info = await get_user_data(interaction.user.id)
    current_rod = user_info["rod"]
    rod_info = RODS.get(current_rod)
    next_rod = rod_info["next"]

    if not next_rod:
        await interaction.followup.send("✨ 이미 최고 등급인 [전설의 낚시대]를 보유 중입니다!", ephemeral=True)
        return

    cost = rod_info["cost"]
    if user_info["meso"] < cost:
        await interaction.followup.send(f"❌ 메소가 부족합니다! (필요: {cost:,} 메소)", ephemeral=True)
        return

    user_info["meso"] -= cost
    if random.randint(1, 100) <= rod_info["success_rate"]:
        user_info["rod"] = next_rod
        await update_user_data(interaction.user.id, user_info)
        await interaction.followup.send(f"🎉 낚시대 강화 성공! **[{current_rod}]** ➔ **[{next_rod}]**")
    else:
        await update_user_data(interaction.user.id, user_info)
        await interaction.followup.send(f"💥 강화 실패... **[{current_rod}]** 유지")

# ------------------------------------------
# 관리자 정보수정 명령어 추가
# ------------------------------------------
@bot.tree.command(name="정보수정", description="[관리자용] 특정 유저의 레벨, 메소, 조각 등을 강제로 수정합니다.")
@app_commands.default_permissions(administrator=True)
@app_commands.choices(항목=[
    app_commands.Choice(name="레벨", value="level"),
    app_commands.Choice(name="메소", value="meso"),
    app_commands.Choice(name="솔에르다조각", value="sol_erda"),
    app_commands.Choice(name="경험치", value="exp")
])
async def admin_modify(interaction: discord.Interaction, 유저: discord.User, 항목: str, 수치: int):
    await interaction.response.defer(ephemeral=True)

    try:
        user_info = await get_user_data(유저.id)
        user_info[항목] = 수치
        await update_user_data(유저.id, user_info)

        await interaction.followup.send(f"✅ 성공적으로 **{유저.name}**님의 `{항목}`을(를) `{수치:,}`(으)로 수정했습니다.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 수정 중 오류 발생: {e}", ephemeral=True)

TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)