import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
from datetime import datetime, timedelta, timezone
import asyncio
import sqlite3
import os

KST = timezone(timedelta(hours=9))
now = datetime.now(KST)
user_voice_seconds = {}

intents =  discord.Intents.default()
intents.voice_states = True
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

EXCLUDED_CHANNEL_IDS = [1498085152281067791]
CATEGORY_ID = [1530948235563372707]

def init_db():
    conn = sqlite3.connect("attendance.db")
    cursor = conn.cursor()
    # users 테이블: user_id(기본키), last_check(최근 출석일), count(누적 출석일)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            last_check TEXT,
            count INTEGER DEFAULT 0,
            last_voice_at TEXT
        )
    """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER
        )
    """
    )
    conn.commit()
    conn.close()


@client.event
async def on_ready():
    init_db()
    check_voice_time.start()
    print(f"로그인 성공: {client.user.name}")
    await tree.sync()

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
        embed.add_field(name="",value=sale_amount + "억 메소를 억당" + sale_price + "원으로 판매합니다", inline=False)
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

        # 여기에 거래글 등록 로직 추가 가능


class Saleview(discord.ui.View):
    def __init__(self, seller: discord.User | discord.Member):
        super().__init__(timeout=None)
        self.seller = seller
    @discord.ui.button(label="나도 등록하기", style=discord.ButtonStyle.green, emoji="📝")
    async def register_sale(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SaleModal()
        await interaction.response.send_modal(modal)
        # 여기에 판매글 등록 로직 추가 가능

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
        original_message = (interaction.message)
        trading_message = discord.Embed(title = "거래중...", description = "현재 거래가 진행 중인 게시글 입니다.")
        await original_message.edit(embed=trading_message, view=Saleview(seller=self.seller))
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            self.seller: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            buyer: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
        }
        channel_name = f"{interaction.user.name}님의 구매문의"
        new_channel = await interaction.guild.create_text_channel(name = channel_name,overwrites=overwrites, category=category)
        embed = discord.Embed(title="메소 구매 문의", description=f"{self.seller.mention}님과 {buyer.mention}님의 구매 문의 채널입니다.", color=discord.Color.green())
        view = tradeoverview(seller=self.seller,  original_message=original_message)
        await new_channel.send(embed=embed, view=view)
        await new_channel.send(f"{self.seller.mention}{buyer.mention}")
        await interaction.followup.send(f"{new_channel.mention} 채널이 생성되었습니다.", ephemeral=True)

        


@tree.command(name = "출석채널지정", description = "출석을 기록할 채널을 지정합니다.")
@app_commands.checks.has_permissions(administrator=True)
async def set_attendance_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = interaction.guild.id
    conn = sqlite3.connect("attendance.db")
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO settings (guild_id, channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id""",
        (guild_id, channel.id),
    )
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"출석 채널이 {channel.mention}로 설정되었습니다.")
@set_attendance_channel.error
async def set_attendance_channel_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("관리자 권한이 필요합니다.", ephemeral=True)
    else:
        await interaction.response.send_message("오류가 발생했습니다.", ephemeral=True)

@tree.command(name = "판매글등록", description = "판매글을 등록합니다.")
@app_commands.checks.has_permissions(administrator=True)
async def register_sale_post(interaction: discord.Interaction):
    embed = discord.Embed(title="판매글 등록", description="판매글을 등록합니다.", color=discord.Color.green())
    embed.add_field(name="판매글 등록하기", value="판매글등록을 하기 위해서 아래 버튼을 눌러주세요", inline=False)
    
    await interaction.response.send_message(embed=embed, view=Saleview(seller=interaction.user), ephemeral=False)


@tree.command(name = "출석확인", description = "누적 출석 일수를 확인합니다.")
async def check_attendance(interaction: discord.Interaction):
    user_id = interaction.user.id
    today_str = datetime.now(KST).strftime("%Y-%m-%d")

    conn = sqlite3.connect("attendance.db")
    cursor = conn.cursor()

    cursor.execute(
        "SELECT last_check, count FROM users WHERE user_id = ?", (user_id,)
    )
    row = cursor.fetchone()

    if row is None:
        cursor.execute(
            "INSERT INTO users (user_id, last_check, count) VALUES (?, ?, ?)",
            (user_id, today_str, 0),
        )
        await interaction.response.send_message(
                    f"🐣 아직 출석을 하지 않았습니다, {interaction.user.mention}님\n"
                    f"🗓️ 오늘 날짜: `{today_str}`\n"
                    f"📊 누적 출석 일수: **0일**"
                )
    else:
        last_check, count = row
        await interaction.response.send_message(
                    f"✅ {interaction.user.mention}님 현재까지의 출석 일수입니다.\n"
                    f"🗓️ 오늘 날짜: `{today_str}`\n"
                    f"📊 누적 출석 일수: **{count}일**"
                )
    conn.commit()
    conn.close()



@client.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    if after.channel and after.channel.id in EXCLUDED_CHANNEL_IDS:
        return
    if before.channel is None and after.channel is not None: 
        now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect("attendance.db")
        cursor = conn.cursor()
        cursor.execute(
            """ 
            INSERT INTO users (user_id, last_voice_at) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_voice_at = excluded.last_voice_at
            """,
            (member.id, now_str)
        )
        conn.commit()
        conn.close()

        if member.id not in user_voice_seconds:
            user_voice_seconds[member.id] = 0



@tasks.loop(minutes=1)
async def check_voice_time():
    now = datetime.now(KST)
    today_str = now.strftime("%Y-%m-%d")
    if now.hour == 0 and  now.minute == 0:
        user_voice_seconds.clear()
        conn = sqlite3.connect("attendance.db")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, last_voice_at, count FROM users WHERE count > 0"
        )
        rows = cursor.fetchall()
        for user_id, last_voice_at_str, count in rows:
            if last_voice_at_str:
                last_voice_dt = datetime.strptime(last_voice_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
                hours_diff =  (now - last_voice_dt).total_seconds() / 3600
                if hours_diff >= 72:
                    if count % 30 != 0:
                        cursor.execute(
                            "UPDATE users SET count = 0 WHERE user_id = ?", (user_id,)
                        )
                    elif count % 30 == 0:
                        new_count = int((count // 30) * 30)
                        cursor.execute(
                            "UPDATE users SET count = ? WHERE user_id = ?", (new_count, user_id)
                        )
        conn.commit()
        conn.close()
        return

    conn = sqlite3.connect("attendance.db")
    cursor = conn.cursor()
    for guild in client.guilds:
        for vc in guild.voice_channels:
            for member in vc.members:
                if member.bot:
                    continue
                cursor.execute(
                    "SELECT last_check, count FROM users WHERE user_id = ?", (member.id,)
                )
                row = cursor.fetchone()
                if row and row[0] == today_str:
                    continue
                current_time = user_voice_seconds.get(member.id, 0) + 60
                user_voice_seconds[member.id] = current_time
                if current_time >= 3600:
                    await process_attendance(member, today_str)
    conn.commit()
    conn.close()



async def process_attendance(member, today_str):
    conn = sqlite3.connect("attendance.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT last_check, count FROM users WHERE user_id = ?", (member.id,)
    )
    row = cursor.fetchone()

    if row is None:
        cursor.execute(
            "INSERT INTO users (user_id, last_check, count) VALUES (?, ?, ?)",
            (member.id, today_str, 1),
        )
        count = 1
    else:
        last_check, count = row
        if last_check != today_str:
            count += 1
            cursor.execute(
                "UPDATE users SET last_check = ?, count = ? WHERE user_id = ?",
                (today_str, count, member.id),
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
    if count ==  1: 
            await channel.send(
                f"🐣 {member.mention}님 천 리 길도 한 걸음부터입니다!\n"
                f"🗓️ 오늘 날짜: `{today_str}`\n"
                f"📊 누적 출석 일수: **{count}일**"
            )
    elif count % 15 == 0 and count < 30:
            await channel.send(
                f"🐣 {member.mention}님 시작이 반입니다! 축하드립니다!\n"
                f"🗓️ 오늘 날짜: `{today_str}`\n"
                f"📊 누적 출석 일수: **{count}일**"
            )
    elif count % 30 == 0 and count != 0:
            await channel.send(
                f"🐣 {member.mention}님 수고하셨습니다! 대단합니다!\n"
                f"🗓️ 오늘 날짜: `{today_str}`\n"
                f"📊 누적 출석 일수: **{count}일**"
            )
    else:
        await channel.send(
            f"✅ {member.mention}님 오늘 출석이 완료되었습니다!\n"
            f"🗓️ 오늘 날짜: `{today_str}`\n"
            f"📊 누적 출석 일수: **{count}일**"
        )



import os

TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    client.run(TOKEN)
else:
    print("디스코드 토큰이 설정되지 않았습니다.")