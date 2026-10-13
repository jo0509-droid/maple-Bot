from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
import os
import json
import time
import random
import asyncio

import psycopg2
import aiohttp

from flask import Flask
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ----------------------------------------
# 타임존 설정 (출석/음성용)
# ----------------------------------------
KST = timezone(timedelta(hours=9))
user_voice_seconds = {}

EXCLUDED_CHANNEL_IDS = [1498085152281067791]
CATEGORY_ID = [1534250108475150438]  # 암시장 카테고리

MAX_SOL_ERDA_PIECES = 30  # 출석/포춘쿠키로 자연스럽게 쌓을 수 있는 솔 에르다 조각 최대치

def init_attendance_db():
    # 출석/채널설정/활동 데이터는 Supabase(Postgres)에 저장합니다.
    # Render는 재배포/재시작마다 로컬 디스크가 초기화되지만, Supabase는 별도 서버라 영향받지 않습니다.
    conn = get_db_connection_plain()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance_users (
            user_id BIGINT PRIMARY KEY,
            last_check TEXT,
            count INTEGER DEFAULT 0,
            last_voice_at TEXT,
            sol_erda_pieces INTEGER DEFAULT 0,
            fortune_date TEXT,
            fortune_count INTEGER DEFAULT 0
        )
        """
    )
    # 기존에 이미 테이블이 있던 경우를 위한 컬럼 추가 (있으면 건너뜀 - 데이터 유지됨)
    cursor.execute("ALTER TABLE attendance_users ADD COLUMN IF NOT EXISTS fortune_date TEXT")
    cursor.execute("ALTER TABLE attendance_users ADD COLUMN IF NOT EXISTS fortune_count INTEGER DEFAULT 0")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            guild_id BIGINT PRIMARY KEY,
            channel_id BIGINT
        )
        """
    )
    # 음성방 활동 레벨/경험치
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_users (
            user_id BIGINT PRIMARY KEY,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            daily_exp INTEGER DEFAULT 0,
            daily_date TEXT
        )
        """
    )
    # 이미 테이블이 만들어져 있는 경우를 위한 컬럼 추가 (있으면 건너뜀 - 데이터 유지됨)
    cursor.execute("ALTER TABLE activity_users ADD COLUMN IF NOT EXISTS daily_exp INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE activity_users ADD COLUMN IF NOT EXISTS daily_date TEXT")
    conn.commit()
    cursor.close()
    conn.close()
    init_maple_db()

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

def get_db_connection_plain():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL 환경 변수가 설정되지 않았습니다. Render 대시보드를 확인하세요.")
    return psycopg2.connect(DATABASE_URL)

# ------------------------------------------
# 디스코드 봇 설정
# ------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.persistent_views_added = False

FORTUNES = [
    "오늘 하루는 왠지 기분 좋은 일로 가득할 거예요! 힘내세요! ✨",
    "당신은 생각보다 훨씬 더 멋지고 대단한 사람입니다. 🍀",
    "오늘 흘린 노력의 결실이 곧 달콤한 보상으로 돌아올 거예요! 🌟",
    "소소하지만 확실한 행복이 오늘 당신을 찾아올 거예요. ☕",
    "주변 사람들에게 당신의 밝은 에너지를 나누어주는 멋진 하루가 될 거예요! ☀️",
    "하고자 하는 모든 일이 순조롭게 풀리는 마법 같은 하루가 될 거예요! 🪄",
    "오늘 당신의 미소는 주변 사람까지 행복하게 만들 거예요. 😊",
    "오늘은 뜻밖의 행운이 당신을 찾아올 거예요! 🍀",
    "당신이 바라던 소원이 하나씩 이루어지기 시작할 거예요. 🌈",
    "오늘 만나는 인연이 당신에게 좋은 기운을 가져다줄 거예요. 🤝",
    "지갑이 두둑해지는 즐거운 소식이 곧 들려올 거예요! 💰",
    "당신의 하루에 행운의 네 잎 클로버가 함께할 거예요. 🍀",
    "오늘은 무엇을 해도 술술 잘 풀리는 날이에요! 🎯",
    "당신의 진심이 오늘 누군가에게 큰 위로가 될 거예요. 💌",
    "작은 용기가 오늘 큰 기회를 열어줄 거예요. 🚪",
    "오늘 하루, 당신의 노력은 절대 헛되지 않을 거예요. 🌱",
    "행복은 멀리 있지 않아요, 오늘 당신 곁에 있어요. 🏡",
    "당신의 하루 끝에는 달콤한 보상이 기다리고 있어요. 🍯",
    "오늘 당신에게 좋은 소식이 날아들 거예요! 🕊️",
    "당신의 긍정적인 마음이 오늘의 행운을 불러올 거예요. 🌻",
    "오늘은 그동안 미뤄왔던 일이 잘 풀리는 날이에요. 📈",
    "당신을 향한 누군가의 응원이 큰 힘이 되어줄 거예요. 📣",
    "오늘 하루, 당신은 빛나는 존재가 될 거예요. ⭐",
    "생각지도 못한 선물 같은 순간이 찾아올 거예요. 🎁",
    "당신의 꿈에 한 걸음 더 가까워지는 하루가 될 거예요. 👣",
    "오늘은 걱정보다 웃음이 더 많은 하루가 될 거예요. 😄",
    "당신의 정성이 오늘 좋은 결과로 이어질 거예요. 🍀",
    "행운의 여신이 오늘 당신 편이에요! 🍀",
    "오늘 하루도 당신은 충분히 잘 해내고 있어요. 💪",
    "당신에게 좋은 인연이 새롭게 시작될 거예요. 🌸",
    "오늘은 마음먹은 대로 다 이루어지는 날이에요. ✅",
    "당신의 하루에 따뜻한 햇살 같은 행운이 비칠 거예요. 🌞",
    "오늘 뜻밖의 칭찬을 듣게 될지도 몰라요. 👏",
    "당신의 지갑에 반가운 소식이 들어올 거예요. 💵",
    "오늘은 당신의 매력이 유독 빛나는 날이에요. ✨",
    "작은 행운들이 모여 큰 행복이 될 거예요. 🧩",
    "오늘 하루, 좋은 기운이 당신을 감싸줄 거예요. 🌀",
    "당신이 심어둔 노력의 씨앗이 곧 싹을 틔울 거예요. 🌾",
    "오늘은 편안하고 여유로운 마음으로 보낼 수 있을 거예요. 🍵",
    "당신의 하루에 행복한 우연이 찾아올 거예요. 🍀",
    "오늘 당신의 선택은 최고의 결과를 가져다줄 거예요. 🏆",
    "당신 곁의 사람들이 오늘 큰 힘이 되어줄 거예요. 🤗",
    "오늘은 유난히 컨디션이 좋고 활기찬 하루가 될 거예요. ⚡",
    "당신에게 뜻깊은 만남이 기다리고 있어요. 🌟",
    "오늘 하루, 행운이 당신의 발걸음마다 함께해요. 👟",
    "당신의 마음속 걱정이 서서히 풀려갈 거예요. 🍃",
    "오늘은 기대하지 않았던 좋은 일이 생길 거예요. 🎉",
    "당신의 하루가 맛있는 음식처럼 풍성해질 거예요. 🍱",
    "오늘 하루는 감사한 일들로 가득할 거예요. 🙏",
    "당신에게 딱 맞는 좋은 기회가 찾아올 거예요. 🔑",
    "오늘은 당신의 진가를 알아봐주는 사람을 만날 거예요. 👀",
    "행운의 순간이 예고 없이 당신을 찾아갈 거예요. 🎲",
    "오늘 하루도 당신은 사랑받을 자격이 충분해요. 💖",
    "당신의 하루에 청량한 바람 같은 행운이 불어올 거예요. 🍃",
    "오늘은 마음의 여유가 큰 행운을 불러올 거예요. 🧘",
    "당신의 재능이 오늘 특별히 빛을 발할 거예요. 💎",
    "오늘 하루, 웃음소리가 끊이지 않을 거예요. 😆",
    "당신에게 오늘은 새로운 시작의 날이 될 거예요. 🌱",
    "오늘은 오래 기다려온 좋은 소식이 도착할 거예요. 📬",
    "당신의 하루가 따뜻한 커피 한 잔처럼 포근할 거예요. ☕",
    "오늘 하루, 당신 주변엔 행운이 가득해요. 🍀",
    "당신이 가진 좋은 기운이 주변에도 전해질 거예요. 🌟",
    "오늘은 예상보다 더 즐거운 하루가 될 거예요. 🎈",
    "당신의 하루에 행복한 반전이 기다리고 있어요. 🔄",
    "오늘은 오랜만에 마음이 가벼워지는 하루가 될 거예요. 🎈",
    "당신에게 든든한 지원군이 나타날 거예요. 🛡️",
    "오늘 하루, 당신의 하루는 반짝반짝 빛날 거예요. ✨",
    "당신의 하루에 예쁜 꽃길이 펼쳐질 거예요. 🌷",
    "오늘은 사소한 일에도 행복을 느끼는 하루가 될 거예요. 🍀",
    "당신을 위한 좋은 타이밍이 곧 찾아올 거예요. ⏰",
    "오늘 하루, 당신의 진심이 통하는 날이 될 거예요. 💬",
    "당신에게 뜻밖의 행운의 편지가 도착할 거예요. ✉️",
    "오늘은 당신이 원하던 답을 얻게 될 거예요. 🗝️",
    "당신의 하루에 산뜻한 활력이 채워질 거예요. 🍋",
    "오늘 하루, 걱정은 내려놓고 행복만 담아가세요. 🎒",
    "당신의 하루가 무지개처럼 다채롭고 즐거울 거예요. 🌈",
    "오늘은 당신의 인복이 유독 빛나는 날이에요. 👫",
    "당신에게 힘이 되는 좋은 말을 듣게 될 거예요. 🗣️",
    "오늘 하루, 당신의 발걸음이 가볍고 경쾌할 거예요. 🚶",
    "당신의 하루에 달콤한 디저트 같은 행운이 더해질 거예요. 🍰",
    "오늘은 미소가 절로 나오는 즐거운 하루가 될 거예요. 😊",
    "당신에게 오늘 좋은 재물운이 따를 거예요. 🪙",
    "오늘 하루, 당신은 누군가에게 행운의 존재가 될 거예요. 🌟",
    "당신의 하루에 봄바람 같은 설렘이 불어올 거예요. 🌸",
    "오늘은 당신의 건강운도 함께 좋아지는 하루예요. 💪",
    "당신에게 반가운 재회가 찾아올 거예요. 🤝",
    "오늘 하루, 당신의 선택 하나하나가 옳을 거예요. ✅",
    "당신의 하루가 별빛처럼 아름답게 빛날 거예요. 🌌",
    "오늘은 당신의 하루에 뜻밖의 즐거움이 더해질 거예요. 🎊",
    "당신에게 오늘은 특별히 운이 좋은 날이에요. 🍀",
    "오늘 하루, 당신의 마음이 평온하고 따뜻할 거예요. 🕊️",
    "당신의 하루에 상쾌한 아침 이슬 같은 행운이 맺힐 거예요. 🌿",
    "오늘은 당신이 흘린 땀방울이 큰 결실로 돌아올 거예요. 🌾",
    "당신에게 오늘 좋은 소식이 연달아 찾아올 거예요. 📰",
    "오늘 하루, 당신의 하루는 유쾌하고 즐거울 거예요. 🎶",
    "당신의 하루에 든든한 행운의 부적이 함께해요. 🧧",
    "오늘은 당신의 하루를 응원하는 사람들이 많아질 거예요. 📣",
    "당신에게 오늘 뜻깊은 배움의 순간이 찾아올 거예요. 📚",
    "오늘 하루, 당신의 마음속 소망이 이루어지고 있어요. 🕯️",
    "당신의 하루가 맑은 하늘처럼 상쾌할 거예요. 🌤️",
    "오늘은 당신에게 예상치 못한 행운이 겹겹이 찾아올 거예요. 🍀",
    "당신의 하루 끝에는 편안한 휴식과 만족이 기다려요. 🛋️",
    "오늘 하루, 당신은 누구보다 반짝이는 존재예요. 🌟",
    "당신의 하루에 향긋한 꽃내음 같은 행운이 스며들 거예요. 🌼",
    "오늘은 당신이 흘려보낸 친절이 배로 돌아올 거예요. 💞",
    "당신에게 오늘은 마음 편히 쉬어가도 좋은 날이에요. 🌙",
    "오늘 하루, 당신의 모든 순간이 축복처럼 빛날 거예요. 🎇"
]

# ------------------------------------------
# UI 클래스들
# ------------------------------------------
# ------------------------------------------
# 판매/거래 관련 View들이 공통으로 쓰는 기본 클래스입니다.
# 버튼 콜백 안에서 예외가 나면 기본적으로는 조용히 무시되고
# 디스코드에는 "적시에 응답하지 않았어요"만 뜨는데, 그러면 원인을
# 알 방법이 없어서 여기서 에러를 잡아 화면에 직접 보여주고 로그에도 남깁니다.
# ------------------------------------------
class ErrorReportingView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        import traceback
        print(f"[판매/거래 View 오류] item={item!r} error={error!r}")
        traceback.print_exc()

        msg = f"❌ 버튼 처리 중 오류가 발생했습니다: `{error}`"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

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
        embed.add_field(name="", value=sale_amount + "억 메소를 억당" + sale_price + "원으로 판매합니다", inline=False)
        embed.add_field(name="", value=sale_comment if sale_comment else "", inline=False)
        embed.set_footer(text="구매를 누르면 바로 채널이 생성되니 주의해주세요!")

        main_view = build_sale_post_view(seller)
        await interaction.response.send_message(embed=embed, view=main_view, ephemeral=False)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        import traceback
        print(f"[판매글 등록 모달 오류] error={error!r}")
        traceback.print_exc()
        msg = f"❌ 판매글 등록 중 오류가 발생했습니다: `{error}`"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

class Saleview(ErrorReportingView):
    def __init__(self, seller: discord.User | discord.Member):
        super().__init__(timeout=None)
        self.seller = seller

    @discord.ui.button(label="나도 등록하기", style=discord.ButtonStyle.green, emoji="📝", custom_id="sale_register_button")
    async def register_sale(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SaleModal()
        await interaction.response.send_modal(modal)

# ------------------------------------------
# 재배포/재시작에도 살아남는 "구매" / "거래 끝내기" 버튼
#
# 이 두 버튼은 메시지마다 판매자(seller)가 다르기 때문에, 그 정보를
# custom_id 안에 함께 저장해둡니다 (예: "buy_button:123456789").
# 그래서 봇이 재시작되어 예전 View 객체가 사라져도, custom_id만 보고
# on_interaction에서 누가 판매자였는지 다시 알아낼 수 있습니다.
# 버튼 자체의 콜백은 아무 것도 하지 않는 빈 함수이고, 실제 처리는
# 전부 아래쪽의 on_interaction 핸들러 하나에서 담당합니다.
# ------------------------------------------
async def _noop_button_callback(interaction: discord.Interaction):
    pass  # 실제 동작은 on_interaction에서 처리합니다.

def build_sale_post_view(seller: discord.User | discord.Member) -> discord.ui.View:
    view = ErrorReportingView(timeout=None)

    buy_button = discord.ui.Button(
        label="구매",
        style=discord.ButtonStyle.green,
        custom_id=f"buy_button:{seller.id}",
    )
    buy_button.callback = _noop_button_callback
    view.add_item(buy_button)

    register_button = discord.ui.Button(
        label="나도 등록하기",
        style=discord.ButtonStyle.green,
        emoji="📝",
        custom_id="sale_register_button",
    )
    async def _register_callback(interaction: discord.Interaction):
        modal = SaleModal()
        await interaction.response.send_modal(modal)
    register_button.callback = _register_callback
    view.add_item(register_button)

    return view

def build_trade_control_view(seller_id: int, original_channel_id: int, original_message_id: int) -> discord.ui.View:
    view = ErrorReportingView(timeout=None)

    end_trade_button = discord.ui.Button(
        label="거래 끝내기",
        style=discord.ButtonStyle.red,
        custom_id=f"end_trade:{seller_id}:{original_channel_id}:{original_message_id}",
    )
    end_trade_button.callback = _noop_button_callback
    view.add_item(end_trade_button)

    return view

async def handle_buy_button(interaction: discord.Interaction, seller_id: int):
    buyer = interaction.user
    guild = interaction.guild

    if buyer.id == seller_id:
        await interaction.response.send_message("자신의 구매글은 구매할 수 없습니다.", ephemeral=True)
        return

    seller = guild.get_member(seller_id)
    if seller is None:
        try:
            seller = await guild.fetch_member(seller_id)
        except discord.NotFound:
            await interaction.response.send_message(
                "판매자 정보를 찾을 수 없습니다. (서버에서 나갔을 수 있어요)", ephemeral=True
            )
            return

    category = guild.get_channel(CATEGORY_ID[0])
    if not isinstance(category, discord.CategoryChannel):
        # 지정된 ID가 더 이상 카테고리가 아니거나(삭제/변경됨) 존재하지 않으면
        # 그냥 카테고리 없이 채널을 생성합니다 (여기서 오류를 내지 않음).
        category = None

    await interaction.response.defer(ephemeral=True)

    original_message = interaction.message
    original_embed = original_message.embeds[0] if original_message.embeds else None
    trading_message = discord.Embed(title="거래중...", description="현재 거래가 진행 중인 게시글 입니다.")
    await original_message.edit(embed=trading_message, view=Saleview(seller=seller))

    try:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            seller: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            buyer: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
        }
        channel_name = f"{buyer.name}님의 구매문의"
        new_channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites, category=category)

        embed = discord.Embed(
            title="메소 구매 문의",
            description=f"{seller.mention}님과 {buyer.mention}님의 구매 문의 채널입니다.",
            color=discord.Color.green(),
        )
        view = build_trade_control_view(seller.id, original_message.channel.id, original_message.id)
        await new_channel.send(embed=embed, view=view)
        await new_channel.send(f"{seller.mention}{buyer.mention}")
        await interaction.followup.send(f"{new_channel.mention} 채널이 생성되었습니다.", ephemeral=True)
    except Exception:
        # 채널 생성 중 뭔가 실패하면, 판매글이 "거래중"에 갇히지 않도록 원래 상태로 되돌립니다.
        try:
            restore_embed = original_embed if original_embed else discord.Embed(title="메소 팔아요", color=discord.Color.green())
            await original_message.edit(embed=restore_embed, view=build_sale_post_view(seller))
        except Exception:
            pass
        raise

async def handle_end_trade_button(interaction: discord.Interaction, seller_id: int, original_channel_id: int, original_message_id: int):
    if interaction.user.id != seller_id:
        await interaction.response.send_message("거래를 종료할 권한이 없습니다.", ephemeral=True)
        return

    await interaction.response.send_message("거래를 종료합니다. 1분 뒤 채널이 삭제 됩니다.", ephemeral=True)

    try:
        original_channel = interaction.guild.get_channel(original_channel_id)
        if original_channel is None:
            original_channel = await bot.fetch_channel(original_channel_id)
        original_message = await original_channel.fetch_message(original_message_id)

        seller = interaction.guild.get_member(seller_id) or interaction.user
        end_embed = discord.Embed(title="판매 완료", description="거래가 종료된 게시글입니다.", color=discord.Color.yellow())
        await original_message.edit(embed=end_embed, view=Saleview(seller=seller))
    except Exception as e:
        print(f"거래 종료 처리 중 원본 게시글 갱신 실패: {e}")

    await asyncio.sleep(60)
    try:
        await interaction.channel.delete()
    except Exception as e:
        print(f"거래 채널 삭제 실패: {e}")

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return

    custom_id = interaction.data.get("custom_id", "") if interaction.data else ""

    try:
        if custom_id.startswith("buy_button:"):
            try:
                seller_id = int(custom_id.split(":", 1)[1])
            except (IndexError, ValueError):
                return
            await handle_buy_button(interaction, seller_id)

        elif custom_id.startswith("end_trade:"):
            parts = custom_id.split(":")
            if len(parts) != 4:
                return
            try:
                seller_id = int(parts[1])
                original_channel_id = int(parts[2])
                original_message_id = int(parts[3])
            except ValueError:
                return
            await handle_end_trade_button(interaction, seller_id, original_channel_id, original_message_id)

        elif custom_id.startswith("verify_approve:") or custom_id.startswith("verify_reject:"):
            try:
                request_id = int(custom_id.split(":", 1)[1])
            except (IndexError, ValueError):
                return
            await handle_verify_decision(interaction, request_id, custom_id.startswith("verify_approve:"))
    except Exception as e:
        import traceback
        print(f"[구매/거래끝내기 처리 오류] custom_id={custom_id} error={e!r}")
        traceback.print_exc()
        msg = f"❌ 처리 중 오류가 발생했습니다: `{e}`"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

# ------------------------------------------
# 이벤트 및 백그라운드 태스크
# ------------------------------------------
@bot.event
async def on_ready():
    init_attendance_db()
    if not check_voice_time.is_running():
        check_voice_time.start()
    start_maple_background_tasks()

    # "나도 등록하기" 버튼이 재배포 후에도 계속 작동하도록 다시 등록합니다.
    # (custom_id가 고정되어 있어야 하고, 이 등록은 봇 프로세스당 한 번만 하면 됩니다)
    if not bot.persistent_views_added:
        bot.add_view(Saleview(seller=None))
        bot.persistent_views_added = True

    print(f'로그인 성공: {bot.user.name}')

    try:
        log_activity_voice_setup()
        if bot.get_channel(LEVELUP_CHANNEL_ID) is None:
            print(f"[활동 EXP] ⚠️ 레벨업 알림 채널({LEVELUP_CHANNEL_ID})을 찾을 수 없습니다.")
    except Exception as e:
        print(f"[활동 EXP] 설정 점검 중 오류: {e}")
    try:
        MY_GUILD = discord.Object(id=1498077956839313559)

        # 1) 먼저 길드 전용으로 명령어를 복사 & 동기화합니다.
        bot.tree.copy_global_to(guild=MY_GUILD)
        synced = await bot.tree.sync(guild=MY_GUILD)
        print(f"Synced {len(synced)} command(s) to specific guild.")

        # 2) 그 다음에 예전에 남아있을 수 있는 전역(global) 명령어를 비웁니다.
        #    (순서가 바뀌면 길드 명령어까지 함께 사라지니 반드시 이 순서를 지켜야 합니다.)
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
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
        conn = get_db_connection_plain()
        cursor = conn.cursor()
        cursor.execute(
            """ 
            INSERT INTO attendance_users (user_id, last_voice_at) VALUES (%s, %s)
            ON CONFLICT(user_id) DO UPDATE SET last_voice_at = excluded.last_voice_at
            """,
            (member.id, now_str)
        )
        conn.commit()
        cursor.close()
        conn.close()

        if member.id not in user_voice_seconds:
            user_voice_seconds[member.id] = 0

# ------------------------------------------
# 음성방 활동 경험치 시스템
#
# - 아래 ACTIVITY_RATES에 "카테고리 ID" 또는 "음성방 ID"를 넣으면 됩니다.
#   · 카테고리 ID: 그 카테고리 안의 모든 음성방에서 경험치를 받습니다.
#   · 음성방 ID: 그 방 하나에서만 경험치를 받습니다. (방 ID가 카테고리보다 우선)
#   · 값은 6분당 EXP입니다.
# - 1레벨 올리는 데 1000EXP, 10레벨마다 경험치통이 500EXP씩 늘어납니다.
#   (1~9레벨: 1000 / 10~19레벨: 1500 / 20~29레벨: 2000 ...)
# ------------------------------------------
ACTIVITY_RATES = {
    1533116567406248006: 10,  # 메이플방 (카테고리)
    1533119021787119907: 10,  # 보스방 (카테고리)
    1533117273311805470: 5,   # 종합게임방 (카테고리, 절반)
    1549737668978413578: 5,   # 내가바로BJ방 (절반)
}
ACTIVITY_BASE_EXP = 1000
ACTIVITY_EXP_STEP = 500

# user_id -> [현재 방의 6분당 EXP, 지금까지 채운 분(0~5)]
# 6분을 꽉 채워야만 지급되고, 음성방을 나가거나 EXP 등급이 다른 방으로 옮기면 처음부터 다시 시작합니다.
voice_exp_pending = {}

def activity_exp_required(level: int) -> int:
    return ACTIVITY_BASE_EXP + ACTIVITY_EXP_STEP * (level // 10)

def get_voice_exp_rate(channel) -> int:
    # 1) 음성방 ID로 직접 지정된 경우  2) 그 방이 속한 카테고리 ID로 지정된 경우
    if channel.id in ACTIVITY_RATES:
        return ACTIVITY_RATES[channel.id]
    category_id = getattr(channel, "category_id", None)
    if category_id is not None and category_id in ACTIVITY_RATES:
        return ACTIVITY_RATES[category_id]
    return 0

def log_activity_voice_setup():
    """봇이 켜질 때 어떤 방이 경험치 대상으로 잡혔는지 로그에 남깁니다. (Render Logs에서 확인)"""
    for cid, rate in ACTIVITY_RATES.items():
        target = None
        for guild in bot.guilds:
            target = guild.get_channel(cid)
            if target:
                break
        if target is None:
            print(f"[활동 EXP] ⚠️ ID {cid} 를 서버에서 찾을 수 없습니다. (ID가 맞는지, 봇이 볼 수 있는 채널인지 확인)")
        elif isinstance(target, discord.CategoryChannel):
            names = ", ".join(vc.name for vc in target.voice_channels) or "(음성방 없음)"
            print(f"[활동 EXP] 카테고리 '{target.name}' → 6분당 {rate}EXP / 적용 음성방: {names}")
        else:
            print(f"[활동 EXP] 채널 '{target.name}' → 6분당 {rate}EXP")

# 레벨업 알림을 보낼 채널 ID (None이면 알림을 보내지 않습니다)
LEVELUP_CHANNEL_ID = 1558779046119219293

# 활동 레벨 10단위(10, 20, 30...)를 달성할 때마다 솔 에르다 조각을 지급합니다.
# 이 조각은 출석 시스템의 sol_erda_pieces(/출석확인, /포춘쿠키와 같은 값)에 합산되며,
# 출석/포춘쿠키와 마찬가지로 MAX_SOL_ERDA_PIECES(30개)를 넘겨 쌓이지 않습니다.
LEVEL_REWARD_INTERVAL = 10
LEVEL_REWARD_PIECES = 5

# 하루(KST 0시 기준)에 음성방 활동으로 얻을 수 있는 최대 경험치
DAILY_ACTIVITY_EXP_LIMIT = 500

def _apply_voice_exp_sync(gains: dict):
    """경험치를 지급하고, 레벨업한 유저 목록을 돌려줍니다.
    각 항목: {"user_id", "level", "milestones", "pieces_gained", "pieces_total"}
    (milestones = 이번에 새로 달성한 10레벨 단위 개수)"""
    level_ups = []
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    conn = get_db_connection_plain()
    try:
        cur = conn.cursor()
        for user_id, gain in gains.items():
            cur.execute(
                "SELECT level, exp, COALESCE(daily_exp, 0), daily_date FROM activity_users WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            level, exp, daily_exp, daily_date = (row[0], row[1], row[2], row[3]) if row else (1, 0, 0, None)

            # 날짜가 바뀌었으면 오늘 획득량을 0부터 다시 셉니다.
            if daily_date != today_str:
                daily_exp = 0

            # 하루 획득 한도(DAILY_ACTIVITY_EXP_LIMIT)를 넘는 만큼은 지급하지 않습니다.
            gain = min(gain, max(0, DAILY_ACTIVITY_EXP_LIMIT - daily_exp))
            if gain <= 0:
                continue

            old_level = level
            exp += gain
            daily_exp += gain
            while exp >= activity_exp_required(level):
                exp -= activity_exp_required(level)
                level += 1
            cur.execute(
                """
                INSERT INTO activity_users (user_id, level, exp, daily_exp, daily_date) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET level = EXCLUDED.level, exp = EXCLUDED.exp,
                    daily_exp = EXCLUDED.daily_exp, daily_date = EXCLUDED.daily_date
                """,
                (user_id, level, exp, daily_exp, today_str),
            )

            if level > old_level:
                milestones = level // LEVEL_REWARD_INTERVAL - old_level // LEVEL_REWARD_INTERVAL
                pieces_gained, pieces_total = 0, None
                if milestones > 0:
                    cur.execute(
                        "SELECT COALESCE(sol_erda_pieces, 0) FROM attendance_users WHERE user_id = %s",
                        (user_id,),
                    )
                    prow = cur.fetchone()
                    current = prow[0] if prow else 0
                    pieces_total = min(current + milestones * LEVEL_REWARD_PIECES, MAX_SOL_ERDA_PIECES)
                    pieces_total = max(pieces_total, current)  # 이미 한도를 넘은 값은 깎지 않음
                    pieces_gained = pieces_total - current
                    if pieces_gained > 0:
                        cur.execute(
                            """
                            INSERT INTO attendance_users (user_id, sol_erda_pieces) VALUES (%s, %s)
                            ON CONFLICT (user_id) DO UPDATE SET sol_erda_pieces = EXCLUDED.sol_erda_pieces
                            """,
                            (user_id, pieces_total),
                        )
                level_ups.append({
                    "user_id": user_id,
                    "level": level,
                    "milestones": milestones,
                    "pieces_gained": pieces_gained,
                    "pieces_total": pieces_total,
                })
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return level_ups

def tick_voice_progress(present: dict) -> dict:
    """1분마다 호출됩니다. present = {user_id: 현재 방의 6분당 EXP}.
    6분을 꽉 채운 유저의 {user_id: 지급 EXP}를 돌려줍니다."""
    gains = {}
    for user_id, rate in present.items():
        state = voice_exp_pending.get(user_id)
        if state is None or state[0] != rate:
            # 처음 감지됐거나 EXP 등급이 다른 방으로 옮긴 경우: 여기서부터 6분을 새로 셉니다.
            voice_exp_pending[user_id] = [rate, 0]
            continue
        state[1] += 1
        if state[1] >= 6:
            gains[user_id] = rate
            state[1] = 0

    # 대상 방을 나간 사람은 진행도 초기화
    for uid in list(voice_exp_pending):
        if uid not in present:
            del voice_exp_pending[uid]
    return gains

async def award_voice_exp():
    present = {}
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            rate = get_voice_exp_rate(vc)
            if rate <= 0:
                continue
            for member in vc.members:
                if member.bot:
                    continue
                present[member.id] = rate

    gains = tick_voice_progress(present)

    if gains:
        level_ups = await asyncio.to_thread(_apply_voice_exp_sync, gains)

        if LEVELUP_CHANNEL_ID and level_ups:
            channel = bot.get_channel(LEVELUP_CHANNEL_ID)
            if channel is None:
                print(f"레벨업 채널을 찾을 수 없습니다: {LEVELUP_CHANNEL_ID}")
            else:
                for lu in level_ups:
                    msg = f"🎉 <@{lu['user_id']}>님이 활동 레벨 **Lv. {lu['level']}**을(를) 달성했습니다!"
                    if lu["milestones"] > 0:
                        if lu["pieces_gained"] > 0:
                            msg += (f"\n💎 10레벨 단위 달성 보상으로 솔 에르다 조각 **{lu['pieces_gained']}개**를 받았습니다! "
                                    f"(보유 {lu['pieces_total']}개)")
                        else:
                            msg += f"\n💎 솔 에르다 조각이 이미 최대치({MAX_SOL_ERDA_PIECES}개)라 보상은 지급되지 않았습니다."
                    try:
                        await channel.send(msg)
                    except Exception as e:
                        print(f"레벨업 알림 전송 실패: {e}")

@tasks.loop(minutes=1)
async def check_voice_time():
    now = datetime.now(KST)
    today_str = now.strftime("%Y-%m-%d")

    # 활동 경험치 지급 (여기서 오류가 나도 출석 체크는 계속 돌아가도록 분리)
    try:
        await award_voice_exp()
    except Exception as e:
        print(f"음성 활동 경험치 지급 오류: {e}")

    if now.hour == 0 and now.minute == 0:
        user_voice_seconds.clear()
        conn = get_db_connection_plain()
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
                            "UPDATE attendance_users SET count = 0 WHERE user_id = %s", (user_id,)
                        )
                    elif count % 30 == 0:
                        new_count = int((count // 30) * 30)
                        cursor.execute(
                            "UPDATE attendance_users SET count = %s WHERE user_id = %s", (new_count, user_id)
                        )
        conn.commit()
        cursor.close()
        conn.close()
        return

    conn = get_db_connection_plain()
    cursor = conn.cursor()
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            for member in vc.members:
                if member.bot:
                    continue
                cursor.execute(
                    "SELECT last_check, count FROM attendance_users WHERE user_id = %s", (member.id,)
                )
                row = cursor.fetchone()
                if row and row[0] == today_str:
                    continue
                current_time = user_voice_seconds.get(member.id, 0) + 60
                user_voice_seconds[member.id] = current_time
                if current_time >= 600:  # 10분(600초) 단축
                    await process_attendance(member, today_str)
    conn.commit()
    cursor.close()
    conn.close()

async def process_attendance(member, today_str):
    conn = get_db_connection_plain()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT last_check, count, COALESCE(sol_erda_pieces, 0) FROM attendance_users WHERE user_id = %s", (member.id,)
    )
    row = cursor.fetchone()

    reward_msg = ""
    if row is None:
        count = 1
        pieces = 5 if count % 7 == 0 else 0
        pieces = min(pieces, MAX_SOL_ERDA_PIECES)
        if pieces > 0:
            reward_msg = "\n🎉 **누적 출석 7일 달성!** 솔 에르다 조각 **5개**가 지급되었습니다!"
        cursor.execute(
            "INSERT INTO attendance_users (user_id, last_check, count, sol_erda_pieces) VALUES (%s, %s, %s, %s)",
            (member.id, today_str, count, pieces),
        )
    else:
        last_check, count, pieces = row
        if last_check != today_str:
            count += 1
            if count % 7 == 0 and pieces < MAX_SOL_ERDA_PIECES:
                pieces = min(pieces + 5, MAX_SOL_ERDA_PIECES)
                reward_msg = "\n🎉 **누적 출석 7일 달성!** 솔 에르다 조각 **5개**가 지급되었습니다!"
            cursor.execute(
                "UPDATE attendance_users SET last_check = %s, count = %s, sol_erda_pieces = %s WHERE user_id = %s",
                (today_str, count, pieces, member.id),
            )
    conn.commit()
    cursor.execute(
        "SELECT channel_id FROM settings WHERE guild_id = %s", (member.guild.id,)
    )
    channel_row = cursor.fetchone()
    cursor.close()
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
    if isinstance(error, app_commands.MissingPermissions):
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
    conn = get_db_connection_plain()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO settings (guild_id, channel_id) VALUES (%s, %s) 
           ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id""",
        (guild_id, channel.id),
    )
    conn.commit()
    cursor.close()
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

    conn = get_db_connection_plain()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT last_check, count, COALESCE(sol_erda_pieces, 0) FROM attendance_users WHERE user_id = %s", (user_id,)
    )
    row = cursor.fetchone()

    if row is None:
        cursor.execute(
            "INSERT INTO attendance_users (user_id, last_check, count, sol_erda_pieces) VALUES (%s, %s, %s, %s)",
            (user_id, today_str, 0, 0),
        )
        conn.commit()
        count = 0
        pieces = 0
    else:
        last_check, count, pieces = row

    cursor.close()
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

@bot.tree.command(name="출석랭킹", description="누적 출석일수가 가장 많은 모험가 TOP 10을 확인합니다.")
async def attendance_ranking(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        conn = get_db_connection_plain()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, count FROM attendance_users WHERE count > 0 ORDER BY count DESC LIMIT 10"
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        embed = discord.Embed(title="🗓️ 누적 출석 랭킹 TOP 10", color=0x1ABC9C)

        if not rows:
            embed.description = "아직 출석한 모험가가 없습니다."
        else:
            rank_text = ""
            medals = ["🥇", "🥈", "🥉"]
            for idx, (u_id, count) in enumerate(rows, start=1):
                medal = medals[idx - 1] if idx <= 3 else f"**{idx}.**"

                user_obj = bot.get_user(u_id)
                if not user_obj:
                    try:
                        user_obj = await bot.fetch_user(u_id)
                    except:
                        pass
                user_name = user_obj.display_name if user_obj else f"유저({u_id})"

                rank_text += f"{medal} **{user_name}** - 누적 **{count}일**\n"

            embed.description = rank_text

        await interaction.followup.send(embed=embed)

    except Exception as e:
        print(f"출석랭킹 오류: {e}")
        await interaction.followup.send("❌ 출석 랭킹을 불러오는 데 실패했습니다.", ephemeral=True)

@bot.tree.command(name="출석", description="출석 및 솔 에르다 조각 관리 명령어입니다.")
@app_commands.describe(
    action="수행할 작업",
    user="대상이 되는 유저",
    amount="설정할 값 (조각수정=솔 에르다 조각 개수 / 누적출석일수수정=누적 출석일수)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="조각수정", value="조각수정"),
    app_commands.Choice(name="누적출석일수수정", value="누적출석일수수정")
])
@app_commands.checks.has_permissions(administrator=True)
async def attendance_admin(interaction: discord.Interaction, action: str, user: discord.Member, amount: int):
    conn = get_db_connection_plain()
    cursor = conn.cursor()

    cursor.execute("SELECT user_id FROM attendance_users WHERE user_id = %s", (user.id,))
    row = cursor.fetchone()
    today_str = datetime.now(KST).strftime("%Y-%m-%d")

    if action == "조각수정":
        if row:
            cursor.execute("UPDATE attendance_users SET sol_erda_pieces = %s WHERE user_id = %s", (amount, user.id))
        else:
            cursor.execute(
                "INSERT INTO attendance_users (user_id, last_check, count, sol_erda_pieces) VALUES (%s, %s, 0, %s)",
                (user.id, today_str, amount)
            )
        conn.commit()
        cursor.close()
        conn.close()

        await interaction.response.send_message(f"✅ {user.mention}님의 솔 에르다 조각 개수가 **{amount}개**로 수정되었습니다.", ephemeral=True)

    elif action == "누적출석일수수정":
        if row:
            cursor.execute("UPDATE attendance_users SET count = %s WHERE user_id = %s", (amount, user.id))
        else:
            cursor.execute(
                "INSERT INTO attendance_users (user_id, last_check, count, sol_erda_pieces) VALUES (%s, %s, %s, 0)",
                (user.id, today_str, amount)
            )
        conn.commit()
        cursor.close()
        conn.close()

        await interaction.response.send_message(f"✅ {user.mention}님의 누적 출석일수가 **{amount}일**로 수정되었습니다.", ephemeral=True)

DAILY_FORTUNE_LIMIT = 1
FORTUNE_CHANNEL_ID = 1559161778313625721  # /포춘쿠키를 사용할 수 있는 채널
FORTUNE_PIECE_CHANCE = 20  # %

@bot.tree.command(name="포춘쿠키", description="오늘의 행운의 포춘쿠키를 뽑고 행복한 문구를 확인합니다. (하루 1회)")
async def fortune_cookie(interaction: discord.Interaction):
    # 지정된 채널에서만 사용 가능 (다른 채널에서는 사용 횟수도 차감하지 않고 안내만 보냅니다)
    if interaction.channel_id != FORTUNE_CHANNEL_ID:
        await interaction.response.send_message(
            f"🥠 포춘쿠키는 <#{FORTUNE_CHANNEL_ID}> 채널에서만 사용할 수 있어요!", ephemeral=True
        )
        return

    user_id = interaction.user.id
    today_str = datetime.now(KST).strftime("%Y-%m-%d")

    conn = get_db_connection_plain()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT fortune_date, COALESCE(fortune_count, 0), COALESCE(sol_erda_pieces, 0) FROM attendance_users WHERE user_id = %s",
        (user_id,)
    )
    row = cursor.fetchone()

    if row is None:
        fortune_date, fortune_count, pieces = None, 0, 0
    else:
        fortune_date, fortune_count, pieces = row

    # 날짜가 바뀌었으면 오늘 사용 횟수를 초기화
    if fortune_date != today_str:
        fortune_count = 0

    if fortune_count >= DAILY_FORTUNE_LIMIT:
        cursor.close()
        conn.close()
        await interaction.response.send_message(
            f"🥠 오늘 포춘쿠키는 이미 **{DAILY_FORTUNE_LIMIT}번** 모두 뽑으셨어요! 내일 다시 찾아와주세요. 🌙",
            ephemeral=True
        )
        return

    fortune_count += 1

    gained_piece = random.randint(1, 100) <= FORTUNE_PIECE_CHANCE and pieces < MAX_SOL_ERDA_PIECES
    if gained_piece:
        pieces = min(pieces + 1, MAX_SOL_ERDA_PIECES)

    if row is None:
        cursor.execute(
            """
            INSERT INTO attendance_users (user_id, fortune_date, fortune_count, sol_erda_pieces)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, today_str, fortune_count, pieces)
        )
    else:
        cursor.execute(
            "UPDATE attendance_users SET fortune_date = %s, fortune_count = %s, sol_erda_pieces = %s WHERE user_id = %s",
            (today_str, fortune_count, pieces, user_id)
        )
    conn.commit()
    cursor.close()
    conn.close()

    selected_fortune = random.choice(FORTUNES)
    embed = discord.Embed(
        title="🥠 오늘의 포춘쿠키",
        description=selected_fortune,
        color=0xF1C40F
    )
    if gained_piece:
        embed.add_field(name="🎉 깜짝 선물!", value="포춘쿠키 속에서 **솔 에르다 조각 1개**를 발견했습니다!", inline=False)
    elif pieces >= MAX_SOL_ERDA_PIECES:
        embed.add_field(name="💎 보유 한도 도달", value=f"솔 에르다 조각은 최대 **{MAX_SOL_ERDA_PIECES}개**까지만 쌓을 수 있어요.", inline=False)
    embed.add_field(name="💎 보유 솔 에르다 조각", value=f"{pieces:,}개", inline=True)
    embed.add_field(name="🥠 오늘 남은 횟수", value=f"{DAILY_FORTUNE_LIMIT - fortune_count}/{DAILY_FORTUNE_LIMIT}회", inline=True)
    embed.set_footer(text=f"요청자: {interaction.user.display_name}", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
    await interaction.response.send_message(embed=embed)

# ------------------------------------------
# 음성방 활동 명령어 (/활동정보, /활동랭킹, /활동정보수정)
# ------------------------------------------
def _get_activity_data_sync(user_id: int):
    conn = get_db_connection_plain()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT level, exp, COALESCE(daily_exp, 0), daily_date FROM activity_users WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            return (1, 0, 0)
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        return (row[0], row[1], row[2] if row[3] == today_str else 0)
    finally:
        conn.close()

def _modify_activity_data_sync(user_id: int, field: str, value: int):
    conn = get_db_connection_plain()
    try:
        cur = conn.cursor()
        cur.execute("SELECT level, exp FROM activity_users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        level, exp = (row[0], row[1]) if row else (1, 0)

        if field == "level":
            level = max(1, value)
        else:
            exp = max(0, value)

        # 경험치가 현재 레벨의 경험치통을 넘지 않도록 맞춰줍니다.
        exp = min(exp, activity_exp_required(level) - 1)

        cur.execute(
            """
            INSERT INTO activity_users (user_id, level, exp) VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET level = EXCLUDED.level, exp = EXCLUDED.exp
            """,
            (user_id, level, exp),
        )
        conn.commit()
        cur.close()
        return level, exp
    finally:
        conn.close()

@bot.tree.command(name="활동정보", description="내 음성방 활동 레벨과 경험치를 확인합니다.")
async def activity_info(interaction: discord.Interaction, 유저: discord.User = None):
    await interaction.response.defer()
    target_user = 유저 if 유저 else interaction.user

    try:
        level, exp, today_exp = await asyncio.to_thread(_get_activity_data_sync, target_user.id)
        max_exp = activity_exp_required(level)

        filled = int(exp / max_exp * 10)
        bar = "🟩" * filled + "⬜" * (10 - filled)

        embed = discord.Embed(title=f"🎙️ {target_user.name}님의 활동 정보", color=0x2ECC71)
        embed.add_field(name="활동 레벨", value=f"Lv. {level}", inline=True)
        embed.add_field(name="경험치", value=f"{exp:,} / {max_exp:,} EXP", inline=True)
        embed.add_field(name="진행도", value=bar, inline=False)
        embed.add_field(name="오늘 획득 경험치", value=f"{today_exp:,} / {DAILY_ACTIVITY_EXP_LIMIT:,} EXP", inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"활동정보 오류: {e}")
        await interaction.followup.send("❌ 활동 정보를 불러오는 데 실패했습니다.", ephemeral=True)

@bot.tree.command(name="활동랭킹", description="음성방 활동 레벨이 가장 높은 멤버 TOP 10을 확인합니다.")
async def activity_ranking(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        def _query():
            conn = get_db_connection_plain()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT user_id, level, exp FROM activity_users "
                    "WHERE level > 1 OR exp > 0 ORDER BY level DESC, exp DESC LIMIT 10"
                )
                rows = cur.fetchall()
                cur.close()
                return rows
            finally:
                conn.close()

        rows = await asyncio.to_thread(_query)

        embed = discord.Embed(title="🎙️ 음성방 활동 랭킹 TOP 10", color=0x2ECC71)

        if not rows:
            embed.description = "아직 활동 기록이 있는 멤버가 없습니다."
        else:
            rank_text = ""
            medals = ["🥇", "🥈", "🥉"]
            for idx, (u_id, level, exp) in enumerate(rows, start=1):
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
        print(f"활동랭킹 오류: {e}")
        await interaction.followup.send("❌ 활동 랭킹을 불러오는 데 실패했습니다.", ephemeral=True)

@bot.tree.command(name="활동정보수정", description="[관리자용] 특정 유저의 활동 레벨, 경험치를 강제로 수정합니다.")
@app_commands.default_permissions(administrator=True)
@app_commands.choices(항목=[
    app_commands.Choice(name="레벨", value="level"),
    app_commands.Choice(name="경험치", value="exp")
])
async def activity_modify(interaction: discord.Interaction, 유저: discord.User, 항목: str, 수치: int):
    await interaction.response.defer(ephemeral=True)

    try:
        level, exp = await asyncio.to_thread(_modify_activity_data_sync, 유저.id, 항목, 수치)
        await interaction.followup.send(
            f"✅ **{유저.name}**님의 활동 정보를 수정했습니다. (현재: Lv. {level} / {exp:,} EXP)",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ 수정 중 오류 발생: {e}", ephemeral=True)

# ------------------------------------------
# ==========================================
# 메이플스토리 오픈 API 기능 (조회 / 길드원 인증 / 공지 알림 / 진단)
# ==========================================
# NEXON_API_KEY 는 Render > Environment 에만 넣으세요. (코드/채팅에 절대 붙여넣지 마세요)
NEXON_API_KEY = os.getenv("NEXON_API_KEY") or os.getenv("NX_OPEN_API_KEY")
NEXON_BASE_URL = "https://open.api.nexon.com"

MAPLE_GUILD_NAME = "유치원"
MAPLE_WORLD_NAME = "스카니아"
VERIFIED_ROLE_NAME = "병아리반"
UNVERIFIED_ROLE_KEYWORD = "미인증"   # 이 글자가 이름에 들어간 역할은 인증 승인 시 자동으로 회수됩니다
VERIFY_REQUEST_CHANNEL_ID = 1559168660789010504   # 인증 신청 받는 채널
VERIFY_ADMIN_CHANNEL_ID = 1559168759896215552     # 운영진 승인 채널
MAPLE_NOTICE_CHANNEL_ID = 1559168922194804807     # 공지 알림 채널

_nexon_session = None
_nexon_semaphore = asyncio.Semaphore(4)
NEXON_MIN_INTERVAL = 0.25   # 모든 넥슨 API 호출 사이 최소 간격(초) → 초당 최대 4회
_nexon_rate_lock = asyncio.Lock()
_nexon_last_call = 0.0
_nexon_cache = {}       # key -> (만료 시각, 데이터)
NEXON_CACHE_TTL = 300   # 초
OCID_CACHE_TTL = 3600


class UserFacingError(Exception):
    """사용자에게 그대로 보여줄 수 있는 안내 메시지용 예외"""


class MapleAPIError(UserFacingError):
    pass


NEXON_ERROR_MESSAGES = {
    "OPENAPI00001": "넥슨 서버 내부 오류입니다. 잠시 후 다시 시도해주세요.",
    "OPENAPI00002": "넥슨 API 접근 권한이 없습니다. (API 키 권한 확인 필요)",
    "OPENAPI00003": "존재하지 않는 캐릭터/길드이거나 식별자가 올바르지 않습니다. 이름을 확인해주세요.",
    "OPENAPI00004": "요청 파라미터가 올바르지 않습니다.",
    "OPENAPI00005": "넥슨 API 키가 올바르지 않습니다. (관리자에게 문의해주세요)",
    "OPENAPI00006": "지원하지 않는 API 경로입니다.",
    "OPENAPI00007": "넥슨 API 요청 한도에 걸렸습니다. 잠시 후 다시 시도해주세요. (계속되면 운영진에게 알려주세요)",
    "OPENAPI00009": "아직 해당 데이터가 준비되지 않았습니다. (매일 오전 2시 이후 전일 데이터 제공)",
    "OPENAPI00010": "게임 점검 중입니다.",
    "OPENAPI00011": "넥슨 API 점검 중입니다.",
}


async def _get_nexon_session():
    global _nexon_session
    if _nexon_session is None or _nexon_session.closed:
        _nexon_session = aiohttp.ClientSession(
            headers={"x-nxopen-api-key": NEXON_API_KEY or ""},
            timeout=aiohttp.ClientTimeout(total=15),
        )
    return _nexon_session


async def nexon_get(path, params=None, ttl=NEXON_CACHE_TTL):
    """넥슨 오픈 API GET. 캐시 + 속도제한 시 재시도. 실패하면 MapleAPIError."""
    if not NEXON_API_KEY:
        raise MapleAPIError("넥슨 API 키(NEXON_API_KEY)가 설정되어 있지 않습니다. 관리자에게 문의해주세요.")

    params = {k: v for k, v in (params or {}).items() if v is not None}
    cache_key = (path, tuple(sorted(params.items())))
    now = time.monotonic()
    hit = _nexon_cache.get(cache_key)
    if hit and hit[0] > now:
        return hit[1]

    global _nexon_last_call
    last_err = None
    for attempt in range(4):
        try:
            async with _nexon_semaphore:
                # 전체 호출 속도를 일정하게 유지 (초당 호출 제한 방지)
                async with _nexon_rate_lock:
                    wait = NEXON_MIN_INTERVAL - (time.monotonic() - _nexon_last_call)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    _nexon_last_call = time.monotonic()
                session = await _get_nexon_session()
                async with session.get(NEXON_BASE_URL + path, params=params) as resp:
                    try:
                        data = await resp.json(content_type=None)
                    except Exception:
                        data = None
                    status = resp.status
        except asyncio.TimeoutError:
            last_err = MapleAPIError("넥슨 서버 응답이 늦습니다. 잠시 후 다시 시도해주세요.")
            await asyncio.sleep(1)
            continue
        except aiohttp.ClientError as e:
            last_err = MapleAPIError(f"넥슨 서버에 연결하지 못했습니다. ({type(e).__name__})")
            await asyncio.sleep(1)
            continue

        if status == 200 and isinstance(data, dict):
            # 일부 응답은 오류를 200으로 주지 않지만, 안전하게 error 키도 확인합니다.
            if "error" not in data:
                if len(_nexon_cache) > 2000:
                    _nexon_cache.clear()
                _nexon_cache[cache_key] = (now + ttl, data)
                return data

        err = (data or {}).get("error", {}) if isinstance(data, dict) else {}
        code = err.get("name") or ""
        msg = NEXON_ERROR_MESSAGES.get(code)
        if code == "OPENAPI00007" or status == 429:
            print(f"[넥슨 API] 요청 제한 응답(429/OPENAPI00007) path={path} attempt={attempt + 1}")
            last_err = MapleAPIError(NEXON_ERROR_MESSAGES["OPENAPI00007"])
            await asyncio.sleep(2 * (attempt + 1))
            continue
        if code in ("OPENAPI00001",) or status >= 500:
            last_err = MapleAPIError(msg or "넥슨 서버 오류입니다. 잠시 후 다시 시도해주세요.")
            await asyncio.sleep(1)
            continue
        raise MapleAPIError(msg or f"넥슨 API 오류 ({status} {code or '알 수 없음'}): {err.get('message', '')}".strip())

    raise last_err or MapleAPIError("넥슨 API 요청에 실패했습니다.")


_ocid_cache = {}


async def get_ocid(character_name):
    name = character_name.strip()
    if not name:
        raise MapleAPIError("캐릭터 닉네임을 입력해주세요.")
    key = name.lower()
    hit = _ocid_cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    try:
        data = await nexon_get("/maplestory/v1/id", {"character_name": name}, ttl=OCID_CACHE_TTL)
    except MapleAPIError as e:
        if "존재하지 않는" in str(e):
            raise MapleAPIError(f"`{name}` 캐릭터를 찾을 수 없습니다. 닉네임을 정확히 입력해주세요. (2023-12-21 이후 접속 기록이 있는 캐릭터만 조회됩니다)")
        raise
    ocid = data.get("ocid")
    if not ocid:
        raise MapleAPIError(f"`{name}` 캐릭터를 찾을 수 없습니다.")
    _ocid_cache[key] = (time.monotonic() + OCID_CACHE_TTL, ocid)
    return ocid


def kst_date_str(days_ago=0):
    return (datetime.now(KST) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


# ------------------------------------------
# 포맷 도우미
# ------------------------------------------
def fmt_num(v):
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return str(v) if v not in (None, "") else "-"


def fmt_korean_big(v):
    """123456789 -> '1억 2,345만 6,789'"""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return str(v) if v not in (None, "") else "-"
    if n < 10000:
        return f"{n:,}"
    eok, rest = divmod(n, 100_000_000)
    man, one = divmod(rest, 10_000)
    parts = []
    if eok:
        parts.append(f"{eok:,}억")
    if man:
        parts.append(f"{man:,}만")
    if one:
        parts.append(f"{one:,}")
    return " ".join(parts)


def safe_text(v, default="-"):
    return str(v) if v not in (None, "") else default


def chunk_lines(lines, limit=1000):
    chunks, cur = [], ""
    for line in lines:
        if len(cur) + len(line) + 1 > limit and cur:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    return chunks


def maple_embed(title, description=None, color=0xF5A623):
    return discord.Embed(title=title, description=description, color=color)


async def run_maple(interaction, builder, ephemeral=False):
    """defer -> builder() (Embed 또는 문자열 반환) -> 결과 전송. 오류는 친절한 메시지로."""
    try:
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
    except Exception:
        pass
    try:
        result = await builder()
        if isinstance(result, discord.Embed):
            await interaction.followup.send(embed=result)
        elif isinstance(result, (list, tuple)):
            await interaction.followup.send(embeds=list(result))
        else:
            await interaction.followup.send(str(result))
    except UserFacingError as e:
        await interaction.followup.send(f"❌ {e}", ephemeral=True)
    except Exception as e:
        import traceback
        print(f"[메이플 명령어 오류] {e!r}")
        traceback.print_exc()
        try:
            await interaction.followup.send(f"❌ 처리 중 오류가 발생했습니다: `{e}`", ephemeral=True)
        except Exception:
            pass


def db_exec(sql, params=(), fetch="none"):
    """동기 DB 실행 도우미 (asyncio.to_thread 로 호출). fetch: none / one / all"""
    conn = get_db_connection_plain()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        result = None
        if fetch == "one":
            result = cur.fetchone()
        elif fetch == "all":
            result = cur.fetchall()
        conn.commit()
        cur.close()
        return result
    finally:
        conn.close()


async def adb(sql, params=(), fetch="none"):
    return await asyncio.to_thread(db_exec, sql, params, fetch)


def init_maple_db():
    conn = get_db_connection_plain()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS maple_verify_requests (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            character_name TEXT NOT NULL,
            ocid TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            decided_by BIGINT,
            decided_at TEXT,
            admin_message_id BIGINT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS maple_verifications (
            user_id BIGINT PRIMARY KEY,
            character_name TEXT NOT NULL,
            ocid TEXT NOT NULL,
            verified_at TEXT,
            approved_by BIGINT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS maple_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()
    cur.close()
    conn.close()
    init_growth_db()


# ------------------------------------------
# 조회 명령어
# ------------------------------------------
@bot.tree.command(name="캐릭터", description="메이플스토리 캐릭터 정보를 조회합니다.")
@app_commands.rename(nickname="닉네임")
@app_commands.describe(nickname="조회할 캐릭터 닉네임")
async def maple_character(interaction: discord.Interaction, nickname: str):
    async def build():
        ocid = await get_ocid(nickname)
        basic = await nexon_get("/maplestory/v1/character/basic", {"ocid": ocid})
        stat = pop = dojang = union = None
        try:
            stat = await nexon_get("/maplestory/v1/character/stat", {"ocid": ocid})
        except MapleAPIError:
            pass
        try:
            pop = await nexon_get("/maplestory/v1/character/popularity", {"ocid": ocid})
        except MapleAPIError:
            pass
        try:
            dojang = await nexon_get("/maplestory/v1/character/dojang", {"ocid": ocid})
        except MapleAPIError:
            pass
        try:
            union = await nexon_get("/maplestory/v1/user/union", {"ocid": ocid})
        except MapleAPIError:
            pass

        e = maple_embed(f"{basic.get('character_name', nickname)}", color=0xF5A623)
        e.description = (
            f"**{safe_text(basic.get('world_name'))}** · {safe_text(basic.get('character_class'))}\n"
            f"Lv.{safe_text(basic.get('character_level'))} ({safe_text(basic.get('character_exp_rate'))}%)"
        )
        if basic.get("character_image"):
            e.set_thumbnail(url=basic["character_image"])
        e.add_field(name="길드", value=safe_text(basic.get("character_guild_name"), "없음"), inline=True)
        if pop:
            e.add_field(name="인기도", value=fmt_num(pop.get("popularity")), inline=True)
        if stat:
            for s in stat.get("final_stat", []) or []:
                if s.get("stat_name") == "전투력":
                    e.add_field(name="전투력", value=fmt_korean_big(s.get("stat_value")), inline=True)
                    break
        if union:
            e.add_field(
                name="유니온",
                value=f"Lv.{fmt_num(union.get('union_level'))} ({safe_text(union.get('union_grade'))})",
                inline=True,
            )
        if dojang and dojang.get("dojang_best_floor") is not None:
            e.add_field(name="무릉도장 최고 층", value=f"{fmt_num(dojang.get('dojang_best_floor'))}층", inline=True)
        e.set_footer(text=f"기준일: {safe_text(basic.get('date'), '최신')} · 넥슨 오픈 API (약 15분 지연)")
        return e

    await run_maple(interaction, build)


@bot.tree.command(name="스탯", description="캐릭터의 주요 스탯을 조회합니다.")
@app_commands.rename(nickname="닉네임")
@app_commands.describe(nickname="조회할 캐릭터 닉네임")
async def maple_stat(interaction: discord.Interaction, nickname: str):
    async def build():
        ocid = await get_ocid(nickname)
        stat = await nexon_get("/maplestory/v1/character/stat", {"ocid": ocid})
        stats = {s.get("stat_name"): s.get("stat_value") for s in stat.get("final_stat", []) or []}
        e = maple_embed(f"{nickname} 스탯")
        order = [
            "전투력", "스탯공격력", "데미지", "보스 몬스터 데미지", "최종 데미지",
            "방어율 무시", "크리티컬 확률", "크리티컬 데미지", "STR", "DEX", "INT", "LUK", "HP", "MP",
            "아케인포스", "어센틱포스", "스타포스",
        ]
        shown = 0
        for name in order:
            if name in stats:
                v = stats[name]
                if name == "전투력":
                    v = fmt_korean_big(v)
                elif name in ("스탯공격력", "STR", "DEX", "INT", "LUK", "HP", "MP", "아케인포스", "어센틱포스", "스타포스"):
                    v = fmt_num(v)
                elif v not in (None, ""):
                    v = f"{v}%"
                e.add_field(name=name, value=safe_text(v), inline=True)
                shown += 1
        if not shown:
            raise MapleAPIError("스탯 정보를 가져오지 못했습니다.")
        e.set_footer(text=f"기준일: {safe_text(stat.get('date'), '최신')} · 넥슨 오픈 API")
        return e

    await run_maple(interaction, build)


@bot.tree.command(name="장비", description="캐릭터가 착용 중인 장비를 조회합니다.")
@app_commands.rename(nickname="닉네임")
@app_commands.describe(nickname="조회할 캐릭터 닉네임")
async def maple_equipment(interaction: discord.Interaction, nickname: str):
    async def build():
        ocid = await get_ocid(nickname)
        data = await nexon_get("/maplestory/v1/character/item-equipment", {"ocid": ocid})
        items = data.get("item_equipment", []) or []
        if not items:
            raise MapleAPIError("착용 중인 장비 정보가 없습니다.")
        lines = []
        for it in items:
            part = safe_text(it.get("item_equipment_slot") or it.get("item_equipment_part"))
            name = safe_text(it.get("item_name"))
            sf = it.get("starforce")
            sf_txt = f" ★{sf}" if sf not in (None, "", "0", 0) else ""
            pot = it.get("potential_option_grade")
            add = it.get("additional_potential_option_grade")
            grade = ""
            if pot or add:
                grade = f" [{pot or '-'} / {add or '-'}]"
            lines.append(f"**{part}** {name}{sf_txt}{grade}")
        e = maple_embed(f"{nickname} 장비")
        e.description = "잠재 표기: [윗잠재 / 에디셔널]"
        for i, chunk in enumerate(chunk_lines(lines, 1000)[:6]):
            e.add_field(name="착용 장비" if i == 0 else "​", value=chunk, inline=False)
        e.set_footer(text=f"기준일: {safe_text(data.get('date'), '최신')} · 현재 프리셋 기준 · 넥슨 오픈 API")
        return e

    await run_maple(interaction, build)


@bot.tree.command(name="심볼", description="캐릭터의 심볼 정보를 조회합니다.")
@app_commands.rename(nickname="닉네임")
@app_commands.describe(nickname="조회할 캐릭터 닉네임")
async def maple_symbol(interaction: discord.Interaction, nickname: str):
    async def build():
        ocid = await get_ocid(nickname)
        data = await nexon_get("/maplestory/v1/character/symbol-equipment", {"ocid": ocid})
        symbols = data.get("symbol", []) or []
        if not symbols:
            raise MapleAPIError("장착 중인 심볼이 없습니다.")
        lines = []
        for s in symbols:
            name = safe_text(s.get("symbol_name")).replace("어센틱심볼 : ", "").replace("아케인심볼 : ", "")
            kind = "어센틱" if "어센틱" in safe_text(s.get("symbol_name"), "") else "아케인"
            lines.append(
                f"[{kind}] **{name}** Lv.{safe_text(s.get('symbol_level'))} "
                f"({safe_text(s.get('symbol_growth_count'))}/{safe_text(s.get('symbol_require_growth_count'))}) "
                f"· 포스 {fmt_num(s.get('symbol_force'))}"
            )
        e = maple_embed(f"{nickname} 심볼")
        for i, chunk in enumerate(chunk_lines(lines, 1000)[:4]):
            e.add_field(name="장착 심볼" if i == 0 else "​", value=chunk, inline=False)
        e.set_footer(text=f"기준일: {safe_text(data.get('date'), '최신')} · 넥슨 오픈 API")
        return e

    await run_maple(interaction, build)


@bot.tree.command(name="유니온", description="캐릭터의 유니온 정보를 조회합니다.")
@app_commands.rename(nickname="닉네임")
@app_commands.describe(nickname="조회할 캐릭터 닉네임")
async def maple_union(interaction: discord.Interaction, nickname: str):
    async def build():
        ocid = await get_ocid(nickname)
        data = await nexon_get("/maplestory/v1/user/union", {"ocid": ocid})
        e = maple_embed(f"{nickname} 유니온")
        e.add_field(name="유니온 레벨", value=fmt_num(data.get("union_level")), inline=True)
        e.add_field(name="등급", value=safe_text(data.get("union_grade")), inline=True)
        if data.get("union_artifact_level") is not None:
            e.add_field(name="아티팩트 레벨", value=fmt_num(data.get("union_artifact_level")), inline=True)
        try:
            raider = await nexon_get("/maplestory/v1/user/union-raider", {"ocid": ocid})
            blocks = raider.get("union_block", []) or []
            if blocks:
                e.add_field(name="배치된 공격대원", value=f"{len(blocks)}명", inline=True)
        except MapleAPIError:
            pass
        e.set_footer(text=f"기준일: {safe_text(data.get('date'), '최신')} · 넥슨 오픈 API")
        return e

    await run_maple(interaction, build)


async def resolve_oguild_id(guild_name, world_name):
    data = await nexon_get(
        "/maplestory/v1/guild/id",
        {"guild_name": guild_name, "world_name": world_name},
        ttl=OCID_CACHE_TTL,
    )
    oguild = data.get("oguild_id")
    if not oguild:
        raise MapleAPIError(f"`{world_name}` 월드에서 `{guild_name}` 길드를 찾을 수 없습니다.")
    return oguild


@bot.tree.command(name="길드", description="길드 정보를 조회합니다. (기본: 우리 길드)")
@app_commands.rename(guild_name="길드이름", world_name="월드")
@app_commands.describe(guild_name="조회할 길드 이름 (비우면 우리 길드)", world_name="월드 이름 (비우면 스카니아)")
async def maple_guild(interaction: discord.Interaction, guild_name: str = None, world_name: str = None):
    async def build():
        gname = (guild_name or MAPLE_GUILD_NAME).strip()
        wname = (world_name or MAPLE_WORLD_NAME).strip()
        oguild = await resolve_oguild_id(gname, wname)
        basic = await nexon_get("/maplestory/v1/guild/basic", {"oguild_id": oguild})
        e = maple_embed(f"{safe_text(basic.get('guild_name'), gname)} ({wname})")
        e.add_field(name="길드 레벨", value=safe_text(basic.get("guild_level")), inline=True)
        e.add_field(name="길드원 수", value=f"{safe_text(basic.get('guild_member_count'))}명", inline=True)
        e.add_field(name="길드 마스터", value=safe_text(basic.get("guild_master_name")), inline=True)
        e.add_field(name="명성치", value=fmt_num(basic.get("guild_fame_point")), inline=True)
        e.add_field(name="길드 포인트", value=fmt_num(basic.get("guild_point")), inline=True)
        skills = basic.get("guild_skill", []) or []
        if skills:
            txt = ", ".join(f"{s.get('skill_name')} Lv.{s.get('skill_level')}" for s in skills[:10])
            e.add_field(name="길드 스킬", value=txt[:1000], inline=False)
        e.set_footer(text=f"기준일: {safe_text(basic.get('date'), '최신')} · 넥슨 오픈 API")
        return e

    await run_maple(interaction, build)


@bot.tree.command(name="길드랭킹", description="우리 길드의 공식 길드 랭킹 순위를 조회합니다.")
@app_commands.rename(rank_type="종류")
@app_commands.describe(rank_type="랭킹 종류")
@app_commands.choices(rank_type=[
    app_commands.Choice(name="주간 명성치", value=0),
    app_commands.Choice(name="플래그 레이스", value=1),
    app_commands.Choice(name="지하 수로", value=2),
])
async def maple_guild_ranking(interaction: discord.Interaction, rank_type: app_commands.Choice[int]):
    async def build():
        data = await nexon_get(
            "/maplestory/v1/ranking/guild",
            {
                "date": kst_date_str(1),
                "world_name": MAPLE_WORLD_NAME,
                "ranking_type": rank_type.value,
                "guild_name": MAPLE_GUILD_NAME,
            },
        )
        rows = data.get("ranking", []) or []
        e = maple_embed(f"{MAPLE_GUILD_NAME} 길드 랭킹 · {rank_type.name}")
        if not rows:
            e.description = "해당 종류의 랭킹에 우리 길드 기록이 없습니다."
        else:
            for r in rows[:5]:
                e.add_field(
                    name=f"{fmt_num(r.get('ranking'))}위 · {safe_text(r.get('guild_name'))}",
                    value=f"길드 Lv.{safe_text(r.get('guild_level'))} · 마스터 {safe_text(r.get('guild_master_name'))}\n"
                          f"점수 {fmt_num(r.get('guild_point'))}",
                    inline=False,
                )
        e.set_footer(text=f"기준일: {kst_date_str(1)} · 개인 수로 점수는 공식 API에서 제공하지 않습니다")
        return e

    await run_maple(interaction, build)


# ------------------------------------------
# 길드원 인증 (메이플핸즈 스크린샷 + 운영진 승인)
# ------------------------------------------
def build_verify_buttons(request_id, disabled=False):
    view = ErrorReportingView(timeout=None)
    approve = discord.ui.Button(
        label="승인", style=discord.ButtonStyle.success,
        custom_id=f"verify_approve:{request_id}", disabled=disabled,
    )
    reject = discord.ui.Button(
        label="거절", style=discord.ButtonStyle.danger,
        custom_id=f"verify_reject:{request_id}", disabled=disabled,
    )
    approve.callback = _noop_button_callback
    reject.callback = _noop_button_callback
    view.add_item(approve)
    view.add_item(reject)
    return view


@bot.tree.command(name="인증", description="길드원 인증을 신청합니다. (메이플핸즈 앱 스크린샷 첨부)")
@app_commands.rename(nickname="닉네임", screenshot="스크린샷")
@app_commands.describe(nickname="인증할 본캐릭터 닉네임", screenshot="메이플핸즈 앱 화면 스크린샷")
async def maple_verify(interaction: discord.Interaction, nickname: str, screenshot: discord.Attachment):
    if interaction.channel_id != VERIFY_REQUEST_CHANNEL_ID:
        await interaction.response.send_message(
            f"❌ 이 명령어는 <#{VERIFY_REQUEST_CHANNEL_ID}> 채널에서만 사용할 수 있어요.", ephemeral=True)
        return
    if not (screenshot.content_type or "").startswith("image/"):
        await interaction.response.send_message("❌ 이미지 파일(스크린샷)을 첨부해주세요.", ephemeral=True)
        return
    if not NEXON_API_KEY:
        await interaction.response.send_message("❌ 아직 넥슨 API 설정이 완료되지 않았어요. 운영진에게 문의해주세요.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        guild = interaction.guild
        member = interaction.user

        role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME) if guild else None
        if role is None:
            raise UserFacingError(f"`{VERIFIED_ROLE_NAME}` 역할을 찾을 수 없습니다. 운영진에게 문의해주세요.")
        if role in getattr(member, "roles", []):
            raise UserFacingError("이미 인증이 완료된 상태입니다. (역할이 부여되어 있어요)")

        # 예전 인증 기록이 있어도 역할이 없으면(역할 삭제/서버 재입장) 다시 인증할 수 있습니다.

        pending = await adb(
            "SELECT id FROM maple_verify_requests WHERE user_id=%s AND status='pending'", (member.id,), "one")
        if pending:
            raise UserFacingError("이미 접수된 인증 신청이 있습니다. 운영진 확인을 기다려주세요.")

        ocid = await get_ocid(nickname)
        basic = await nexon_get("/maplestory/v1/character/basic", {"ocid": ocid}, ttl=60)
        real_name = basic.get("character_name") or nickname
        if basic.get("world_name") != MAPLE_WORLD_NAME:
            raise UserFacingError(f"`{real_name}` 캐릭터는 {MAPLE_WORLD_NAME} 월드가 아닙니다. ({safe_text(basic.get('world_name'))})")
        # 길드 가입 전 신규 인원도 인증할 수 있도록 길드 소속은 요구하지 않습니다. (월드와 실존 캐릭터만 확인)

        dup = await adb("SELECT user_id FROM maple_verifications WHERE ocid=%s", (ocid,), "one")
        if dup and dup[0] != member.id:
            other = guild.get_member(dup[0])
            other_has_role = bool(other and role in other.roles)
            if other_has_role:
                raise UserFacingError("이 캐릭터는 이미 다른 디스코드 계정으로 인증되어 있습니다. 문제가 있다면 운영진에게 문의해주세요.")
            # 이전 계정이 서버를 나갔거나 역할이 없으면 새 계정으로 재인증을 허용합니다.
        dup_pending = await adb(
            "SELECT user_id FROM maple_verify_requests WHERE ocid=%s AND status='pending'", (ocid,), "one")
        if dup_pending:
            raise UserFacingError("이 캐릭터로 접수된 인증 신청이 이미 있습니다.")

        admin_channel = bot.get_channel(VERIFY_ADMIN_CHANNEL_ID)
        if admin_channel is None:
            raise UserFacingError("운영진 승인 채널을 찾을 수 없습니다. 운영진에게 문의해주세요.")

        row = await adb(
            """
            INSERT INTO maple_verify_requests (user_id, character_name, ocid, status, created_at)
            VALUES (%s, %s, %s, 'pending', %s) RETURNING id
            """,
            (member.id, real_name, ocid, datetime.now(KST).isoformat()), "one")
        request_id = row[0]

        # 원본 메시지가 지워져도 남도록 스크린샷을 다시 업로드합니다.
        file = await screenshot.to_file()
        embed = maple_embed("🪪 길드원 인증 신청", color=0x3498DB)
        embed.add_field(name="신청자", value=f"{member.mention} ({member})", inline=False)
        embed.add_field(name="캐릭터", value=f"**{real_name}** · {safe_text(basic.get('character_class'))} Lv.{safe_text(basic.get('character_level'))}", inline=False)
        in_guild = basic.get("character_guild_name") == MAPLE_GUILD_NAME
        embed.add_field(
            name="API 확인",
            value=f"{basic.get('world_name')} · 길드 `{safe_text(basic.get('character_guild_name'), '없음')}` "
                  + ("✅ (우리 길드)" if in_guild else "⚠️ (아직 우리 길드원이 아님)"),
            inline=False)
        embed.set_image(url=f"attachment://{file.filename}")
        embed.set_footer(text=f"요청 번호 #{request_id}")
        try:
            msg = await admin_channel.send(embed=embed, file=file, view=build_verify_buttons(request_id))
        except Exception:
            await adb("DELETE FROM maple_verify_requests WHERE id=%s", (request_id,))
            raise
        await adb("UPDATE maple_verify_requests SET admin_message_id=%s WHERE id=%s", (msg.id, request_id))

        await interaction.followup.send("✅ 인증 신청이 접수되었어요! 운영진이 스크린샷을 확인하면 역할이 부여됩니다.", ephemeral=True)
    except UserFacingError as e:
        await interaction.followup.send(f"❌ {e}", ephemeral=True)
    except Exception as e:
        import traceback
        print(f"[인증 신청 오류] {e!r}")
        traceback.print_exc()
        await interaction.followup.send(f"❌ 처리 중 오류가 발생했습니다: `{e}`", ephemeral=True)


def _is_verify_admin(member):
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and (perms.administrator or perms.manage_roles))


async def handle_verify_decision(interaction: discord.Interaction, request_id: int, approve: bool):
    if not _is_verify_admin(interaction.user):
        await interaction.response.send_message("❌ 운영진만 처리할 수 있어요.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    new_status = "approved" if approve else "rejected"
    row = await adb(
        """
        UPDATE maple_verify_requests SET status=%s, decided_by=%s, decided_at=%s
        WHERE id=%s AND status='pending'
        RETURNING user_id, character_name, ocid
        """,
        (new_status, interaction.user.id, datetime.now(KST).isoformat(), request_id), "one")
    if not row:
        await interaction.followup.send("이미 처리되었거나 존재하지 않는 요청입니다.", ephemeral=True)
        return
    target_id, char_name, ocid = row

    guild = interaction.guild
    member = guild.get_member(target_id) if guild else None
    if member is None and guild:
        try:
            member = await guild.fetch_member(target_id)
        except Exception:
            member = None

    nick_note = ""
    if approve:
        role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
        problem = None
        if member is None:
            problem = "신청자가 서버에 없습니다."
        elif role is None:
            problem = f"`{VERIFIED_ROLE_NAME}` 역할을 찾을 수 없습니다."
        else:
            try:
                await member.add_roles(role, reason=f"메이플 길드원 인증 승인 ({char_name})")
            except discord.Forbidden:
                problem = "봇에게 역할 부여 권한이 없습니다. (봇 역할이 `병아리반`보다 위에 있고 '역할 관리' 권한이 필요해요)"
            except Exception as e:
                problem = f"역할 부여 실패: {e}"
            else:
                # 미인증 역할 회수 (실패해도 승인 자체는 유지)
                try:
                    stale = [r for r in member.roles if UNVERIFIED_ROLE_KEYWORD in r.name and r != role]
                    if stale:
                        await member.remove_roles(*stale, reason=f"메이플 길드원 인증 승인 ({char_name})")
                except Exception as e:
                    print(f"[인증] 미인증 역할 회수 실패: {e}")
                    try:
                        await interaction.followup.send(f"⚠️ 병아리반은 부여했지만 미인증 역할 회수에 실패했어요: {e}\n(봇 역할이 미인증 역할보다 위에 있어야 해요)", ephemeral=True)
                    except Exception:
                        pass
        if problem:
            # 되돌려서 다시 시도할 수 있게 합니다.
            await adb("UPDATE maple_verify_requests SET status='pending', decided_by=NULL, decided_at=NULL WHERE id=%s", (request_id,))
            await interaction.followup.send(f"❌ 승인하지 못했어요: {problem}", ephemeral=True)
            return
        await adb(
            """
            INSERT INTO maple_verifications (user_id, character_name, ocid, verified_at, approved_by)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET character_name=EXCLUDED.character_name,
                ocid=EXCLUDED.ocid, verified_at=EXCLUDED.verified_at, approved_by=EXCLUDED.approved_by
            """,
            (target_id, char_name, ocid, datetime.now(KST).isoformat(), interaction.user.id))
        # 디스코드 서버 닉네임을 캐릭터 닉네임으로 변경 (실패해도 승인은 유지)
        try:
            if member.nick != char_name:
                await member.edit(nick=char_name[:32], reason=f"메이플 길드원 인증 승인 ({char_name})")
        except discord.Forbidden:
            nick_note = f"\n⚠️ 닉네임 변경 권한이 없어요 (서버장이거나 봇보다 높은 역할). `{char_name}` 으로 직접 바꿔주세요."
        except Exception as e:
            nick_note = f"\n⚠️ 닉네임 변경 실패: {e}"
        # 같은 캐릭터로 남아있던 다른 계정의 옛 기록 정리
        await adb("DELETE FROM maple_verifications WHERE ocid=%s AND user_id<>%s", (ocid, target_id))

    # 승인 메시지 갱신 (버튼 비활성화)
    try:
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed:
            embed.color = 0x2ECC71 if approve else 0xE74C3C
            embed.add_field(
                name="처리 결과",
                value=f"{'✅ 승인' if approve else '❌ 거절'} · {interaction.user.mention}",
                inline=False)
        await interaction.message.edit(embed=embed, view=build_verify_buttons(request_id, disabled=True))
    except Exception as e:
        print(f"[인증] 승인 메시지 갱신 실패: {e}")

    # 신청 채널에 결과 알림
    try:
        ch = bot.get_channel(VERIFY_REQUEST_CHANNEL_ID)
        if ch:
            if approve:
                await ch.send(f"🎉 <@{target_id}> 님, `{char_name}` 캐릭터 인증이 완료되어 **{VERIFIED_ROLE_NAME}** 역할이 부여되었어요!")
            else:
                await ch.send(f"<@{target_id}> 님, 인증 신청이 거절되었어요. 스크린샷/닉네임을 확인하고 다시 신청해주세요.")
    except Exception as e:
        print(f"[인증] 결과 알림 실패: {e}")

    await interaction.followup.send("처리 완료!" + nick_note, ephemeral=True)


@bot.tree.command(name="인증해제", description="[운영진] 길드원 인증 정보를 삭제하고 역할을 회수합니다.")
@app_commands.rename(target="대상")
@app_commands.describe(target="인증을 해제할 유저")
@app_commands.default_permissions(manage_roles=True)
async def maple_unverify(interaction: discord.Interaction, target: discord.Member):
    if not _is_verify_admin(interaction.user):
        await interaction.response.send_message("❌ 운영진만 사용할 수 있어요.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    row = await adb("DELETE FROM maple_verifications WHERE user_id=%s RETURNING character_name", (target.id,), "one")
    role = discord.utils.get(interaction.guild.roles, name=VERIFIED_ROLE_NAME)
    removed = False
    if role and role in target.roles:
        try:
            await target.remove_roles(role, reason=f"인증 해제 by {interaction.user}")
            removed = True
        except Exception as e:
            await interaction.followup.send(f"인증 기록은 삭제했지만 역할 회수에 실패했어요: {e}", ephemeral=True)
            return
    if not row and not removed:
        await interaction.followup.send("해당 유저의 인증 기록/역할이 없습니다.", ephemeral=True)
        return
    await interaction.followup.send(
        f"✅ {target.mention} 인증 해제 완료 (기록 {'삭제' if row else '없음'}, 역할 {'회수' if removed else '없음'})", ephemeral=True)


async def fetch_guild_member_names():
    oguild = await resolve_oguild_id(MAPLE_GUILD_NAME, MAPLE_WORLD_NAME)
    basic = await nexon_get("/maplestory/v1/guild/basic", {"oguild_id": oguild}, ttl=120)
    return [n for n in (basic.get("guild_member", []) or [])], basic


@bot.tree.command(name="미인증길드원", description="[운영진] 길드원 중 아직 디스코드 인증을 하지 않은 캐릭터 목록")
@app_commands.default_permissions(manage_roles=True)
async def maple_unverified_members(interaction: discord.Interaction):
    if not _is_verify_admin(interaction.user):
        await interaction.response.send_message("❌ 운영진만 사용할 수 있어요.", ephemeral=True)
        return

    async def build():
        names, basic = await fetch_guild_member_names()
        rows = await adb("SELECT character_name FROM maple_verifications", (), "all")
        verified = {r[0].lower() for r in rows}
        unverified = [n for n in names if n.lower() not in verified]
        e = maple_embed(f"미인증 길드원 ({len(unverified)}/{len(names)}명)")
        if not unverified:
            e.description = "모든 길드원이 인증을 마쳤어요! 🎉"
        else:
            for i, chunk in enumerate(chunk_lines(unverified, 1000)[:6]):
                e.add_field(name="캐릭터" if i == 0 else "​", value=chunk, inline=False)
        e.set_footer(text="인증된 캐릭터 닉네임과 길드원 목록을 대조한 결과입니다. (부캐/닉변은 오차가 있을 수 있어요)")
        return e

    await run_maple(interaction, build, ephemeral=True)


@tasks.loop(time=dt_time(hour=4, minute=0, tzinfo=KST))
async def maple_guild_member_check():
    """매일 새벽 4시: 인증된 유저의 닉네임 변경을 기록에 반영합니다. (탈퇴 알림은 가입/탈퇴 알림에서 처리)"""
    rows = await adb("SELECT user_id, character_name, ocid FROM maple_verifications", (), "all")
    for user_id, char_name, ocid in rows:
        try:
            basic = await nexon_get("/maplestory/v1/character/basic", {"ocid": ocid}, ttl=60)
        except Exception as e:
            print(f"[닉네임 점검] {char_name} 조회 실패: {e}")
            continue
        new_name = basic.get("character_name") or char_name
        if new_name != char_name:
            await adb("UPDATE maple_verifications SET character_name=%s WHERE user_id=%s", (new_name, user_id))
        await asyncio.sleep(0.2)


# ------------------------------------------
# 공지 알림
# ------------------------------------------
NOTICE_SOURCES = [
    ("notice", "/maplestory/v1/notice", "notice", "📢 공지사항"),
    ("update", "/maplestory/v1/notice-update", "update_notice", "🛠️ 업데이트"),
    ("event", "/maplestory/v1/notice-event", "event_notice", "🎉 이벤트"),
]


async def _load_seen(key):
    row = await adb("SELECT value FROM maple_state WHERE key=%s", (f"notice_seen:{key}",), "one")
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


async def _save_seen(key, ids):
    await adb(
        """
        INSERT INTO maple_state (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
        """,
        (f"notice_seen:{key}", json.dumps(ids[-200:])))


def pick_new_notices(items, seen):
    """seen 이 None 이면(첫 실행) 새 글 없음으로 보고 현재 목록만 기록합니다."""
    ids = [str(i.get("notice_id")) for i in items if i.get("notice_id") is not None]
    if seen is None:
        return [], ids
    seen_set = set(seen)
    new_items = [i for i in items if str(i.get("notice_id")) not in seen_set]
    return list(reversed(new_items)), list(dict.fromkeys(seen + ids))


@tasks.loop(minutes=10)
async def maple_notice_check():
    ch = bot.get_channel(MAPLE_NOTICE_CHANNEL_ID)
    for key, path, field, label in NOTICE_SOURCES:
        try:
            data = await nexon_get(path, ttl=60)
            items = data.get(field, []) or []
            seen = await _load_seen(key)
            new_items, new_seen = pick_new_notices(items, seen)
            if ch:
                for it in new_items[:5]:
                    e = maple_embed(f"{label}: {safe_text(it.get('title'))}", color=0xE67E22)
                    if it.get("url"):
                        e.url = it["url"]
                    if it.get("date"):
                        e.set_footer(text=str(it["date"])[:10])
                    if it.get("date_event_start") and it.get("date_event_end"):
                        e.description = f"기간: {str(it['date_event_start'])[:10]} ~ {str(it['date_event_end'])[:10]}"
                    await ch.send(embed=e)
            if new_seen != seen:
                await _save_seen(key, new_seen)
        except Exception as e:
            print(f"[공지 알림:{key}] 오류: {e}")


@maple_notice_check.before_loop
async def _before_maple_notice_check():
    await bot.wait_until_ready()


@maple_guild_member_check.before_loop
async def _before_maple_guild_member_check():
    await bot.wait_until_ready()


# ------------------------------------------
# 진단
# ------------------------------------------
@bot.tree.command(name="메이플진단", description="[운영진] 넥슨 API 연결 상태와 응답 형식을 점검합니다.")
@app_commands.rename(nickname="닉네임")
@app_commands.describe(nickname="점검에 사용할 캐릭터 닉네임 (비우면 캐릭터 항목 생략)")
@app_commands.default_permissions(administrator=True)
async def maple_diagnose(interaction: discord.Interaction, nickname: str = None):
    if not (interaction.user.guild_permissions.administrator):
        await interaction.response.send_message("❌ 관리자만 사용할 수 있어요.", ephemeral=True)
        return

    async def build():
        lines = [f"API 키 설정: {'✅' if NEXON_API_KEY else '❌ (NEXON_API_KEY 없음)'}"]
        role = discord.utils.get(interaction.guild.roles, name=VERIFIED_ROLE_NAME)
        lines.append(f"역할 `{VERIFIED_ROLE_NAME}`: {'✅' if role else '❌ 없음'}")
        me = interaction.guild.me
        if role and me:
            lines.append(f"봇이 역할을 줄 수 있음: {'✅' if (me.guild_permissions.manage_roles and me.top_role > role) else '❌ (역할 순서/권한 확인)'}")
        for label, cid in (("인증 신청", VERIFY_REQUEST_CHANNEL_ID), ("운영진 승인", VERIFY_ADMIN_CHANNEL_ID), ("공지 알림", MAPLE_NOTICE_CHANNEL_ID)):
            lines.append(f"{label} 채널: {'✅' if bot.get_channel(cid) else '❌ 접근 불가'}")

        async def probe(name, path, params, show_keys=True):
            try:
                d = await nexon_get(path, params, ttl=0)
                keys = ", ".join(list(d.keys())[:8]) if show_keys and isinstance(d, dict) else ""
                lines.append(f"✅ {name}" + (f" — 키: {keys}" if keys else ""))
                return d
            except Exception as e:
                lines.append(f"❌ {name} — {e}")
                return None

        if NEXON_API_KEY:
            oguild = None
            d = await probe("guild/id", "/maplestory/v1/guild/id", {"guild_name": MAPLE_GUILD_NAME, "world_name": MAPLE_WORLD_NAME})
            if d:
                oguild = d.get("oguild_id")
            if oguild:
                b = await probe("guild/basic", "/maplestory/v1/guild/basic", {"oguild_id": oguild})
                if b:
                    lines.append(f"   길드원 목록 {len(b.get('guild_member', []) or [])}명 / 보고된 인원 {b.get('guild_member_count')}")
            for rt, rn in ((0, "명성치"), (1, "플래그"), (2, "지하수로")):
                d = await probe(f"ranking/guild type={rt}({rn})", "/maplestory/v1/ranking/guild",
                                {"date": kst_date_str(1), "world_name": MAPLE_WORLD_NAME, "ranking_type": rt, "guild_name": MAPLE_GUILD_NAME}, False)
                if d is not None:
                    rows = d.get("ranking", []) or []
                    lines.append(f"   결과 {len(rows)}건" + (f" · 1건 예: {rows[0].get('guild_name')} / {rows[0].get('ranking')}위" if rows else ""))
            for key, path, field, _ in NOTICE_SOURCES:
                await probe(f"{key} 공지", path, None, False)
            if nickname:
                ocid = None
                try:
                    ocid = await get_ocid(nickname)
                    lines.append("✅ character id")
                except Exception as e:
                    lines.append(f"❌ character id — {e}")
                if ocid:
                    for name, path in (
                        ("character/basic", "/maplestory/v1/character/basic"),
                        ("character/stat", "/maplestory/v1/character/stat"),
                        ("character/dojang", "/maplestory/v1/character/dojang"),
                        ("character/item-equipment", "/maplestory/v1/character/item-equipment"),
                        ("character/symbol-equipment", "/maplestory/v1/character/symbol-equipment"),
                        ("user/union", "/maplestory/v1/user/union"),
                    ):
                        await probe(name, path, {"ocid": ocid})
        e = maple_embed("메이플 API 진단", "\n".join(lines)[:4000], color=0x95A5A6)
        return e

    await run_maple(interaction, build, ephemeral=True)


# ------------------------------------------
# 2단계: 성장 기록 (일일 스냅샷 / 성장 조회 / 주간 성장 랭킹)
# ------------------------------------------
WEEKLY_RANKING_CHANNEL_ID = MAPLE_NOTICE_CHANNEL_ID   # 주간 성장 랭킹을 올릴 채널 (바꾸려면 여기 ID만 수정)
OFFICIAL_PROBABILITY_URL = "https://maplestory.nexon.com/Guide/OtherProbability"


def init_growth_db():
    conn = get_db_connection_plain()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS maple_snapshots (
            user_id BIGINT NOT NULL,
            snap_date TEXT NOT NULL,
            character_name TEXT,
            level INTEGER,
            exp BIGINT,
            combat_power BIGINT,
            union_level INTEGER,
            PRIMARY KEY (user_id, snap_date)
        )
        """
    )
    conn.commit()
    cur.close()
    conn.close()


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


async def take_snapshot_for(user_id, char_name, ocid, snap_date):
    """한 명의 현재 상태를 저장합니다. 성공하면 True."""
    basic = await nexon_get("/maplestory/v1/character/basic", {"ocid": ocid}, ttl=0)
    level = _to_int(basic.get("character_level"))
    exp = _to_int(basic.get("character_exp"))
    name = basic.get("character_name") or char_name
    power = None
    union_level = None
    try:
        stat = await nexon_get("/maplestory/v1/character/stat", {"ocid": ocid}, ttl=0)
        for s in stat.get("final_stat", []) or []:
            if s.get("stat_name") == "전투력":
                power = _to_int(s.get("stat_value"))
                break
    except MapleAPIError:
        pass
    try:
        u = await nexon_get("/maplestory/v1/user/union", {"ocid": ocid}, ttl=0)
        union_level = _to_int(u.get("union_level"))
    except MapleAPIError:
        pass
    if level is None:
        return False
    await adb(
        """
        INSERT INTO maple_snapshots (user_id, snap_date, character_name, level, exp, combat_power, union_level)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, snap_date) DO UPDATE SET character_name=EXCLUDED.character_name,
            level=EXCLUDED.level, exp=EXCLUDED.exp, combat_power=EXCLUDED.combat_power,
            union_level=EXCLUDED.union_level
        """,
        (user_id, snap_date, name, level, exp, power, union_level))
    if name != char_name:
        await adb("UPDATE maple_verifications SET character_name=%s WHERE user_id=%s", (name, user_id))
    return True


async def take_all_snapshots():
    rows = await adb("SELECT user_id, character_name, ocid FROM maple_verifications", (), "all")
    today = kst_date_str(0)
    ok = fail = 0
    for user_id, char_name, ocid in rows:
        try:
            if await take_snapshot_for(user_id, char_name, ocid, today):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            print(f"[성장 기록] {char_name} 저장 실패: {e}")
        await asyncio.sleep(0.2)
    print(f"[성장 기록] {today} 스냅샷 완료: 성공 {ok} / 실패 {fail}")
    return ok, fail


SNAPSHOT_INTERVAL_MINUTES = 60   # 전체 길드원 성장 기록 갱신 주기 (/성장 은 호출 시점에 해당 유저를 즉시 최신화합니다)


@tasks.loop(minutes=SNAPSHOT_INTERVAL_MINUTES)
async def maple_daily_snapshot():
    """주기적으로 인증된 길드원의 레벨/경험치/전투력/유니온을 기록합니다. (같은 날 기록은 최신 값으로 덮어씀)"""
    await take_all_snapshots()


@maple_daily_snapshot.before_loop
async def _before_maple_daily_snapshot():
    await bot.wait_until_ready()


def _delta_text(new, old, big=False):
    if new is None or old is None:
        return "-"
    d = new - old
    f = fmt_korean_big if big else fmt_num
    sign = "+" if d > 0 else ("-" if d < 0 else "±")
    return f"{f(new)} ({sign}{f(abs(d))})"


async def load_window(user_id, days):
    """기간 시작 이후 가장 오래된 기록과 가장 최근 기록을 돌려줍니다."""
    cutoff = kst_date_str(days)
    cols = "snap_date, character_name, level, exp, combat_power, union_level"
    first = await adb(
        f"SELECT {cols} FROM maple_snapshots WHERE user_id=%s AND snap_date>=%s ORDER BY snap_date ASC LIMIT 1",
        (user_id, cutoff), "one")
    last = await adb(
        f"SELECT {cols} FROM maple_snapshots WHERE user_id=%s ORDER BY snap_date DESC LIMIT 1",
        (user_id,), "one")
    return first, last


@bot.tree.command(name="성장", description="인증된 내 캐릭터의 최근 성장(레벨/전투력/유니온 변화)을 봅니다.")
@app_commands.rename(days="기간", target="대상")
@app_commands.describe(days="비교 기간", target="다른 인증 유저 (비우면 나)")
@app_commands.choices(days=[
    app_commands.Choice(name="최근 7일", value=7),
    app_commands.Choice(name="최근 30일", value=30),
])
async def maple_growth(interaction: discord.Interaction, days: app_commands.Choice[int] = None, target: discord.Member = None):
    period = days.value if days else 7
    user = target or interaction.user

    async def build():
        row = await adb("SELECT character_name, ocid FROM maple_verifications WHERE user_id=%s", (user.id,), "one")
        if row:
            # 조회하는 순간 해당 유저의 최신 상태를 먼저 저장합니다 (호출량 최소화 + 실시간에 가깝게)
            try:
                await take_snapshot_for(user.id, row[0], row[1], kst_date_str(0))
            except Exception as ex:
                print(f"[성장] 즉시 갱신 실패: {ex}")
        if not row:
            raise UserFacingError("인증된 캐릭터가 없어요. `/인증`으로 먼저 인증을 받아주세요." if user.id == interaction.user.id
                                  else "해당 유저는 인증된 캐릭터가 없어요.")
        first, last = await load_window(user.id, period)
        if not last:
            raise UserFacingError("아직 성장 기록이 없어요. 잠시 뒤 다시 시도해주세요.")
        if first[0] == last[0]:
            raise UserFacingError(f"비교할 기록이 하루치뿐이에요. (기록 시작일: {last[0]}) 내일부터 날짜별 변화량이 표시돼요.")
        e = maple_embed(f"📈 {last[1]} 성장 기록 ({first[0]} → {last[0]})")
        e.add_field(name="레벨", value=_delta_text(last[2], first[2]), inline=True)
        if last[3] is not None and first[3] is not None and last[2] == first[2]:
            e.add_field(name="경험치 증가량", value=fmt_num(last[3] - first[3]), inline=True)
        e.add_field(name="전투력", value=_delta_text(last[4], first[4], big=True), inline=False)
        e.add_field(name="유니온 레벨", value=_delta_text(last[5], first[5]), inline=True)
        e.set_footer(text="조회 시점 기준 최신 · 넥슨 오픈 API (약 15분 지연)")
        return e

    await run_maple(interaction, build)


@bot.tree.command(name="성장랭킹", description="인증된 길드원들의 성장 랭킹 (기간 내 증가량 기준)")
@app_commands.rename(metric="기준", days="기간")
@app_commands.describe(metric="랭킹 기준", days="비교 기간")
@app_commands.choices(
    metric=[
        app_commands.Choice(name="전투력", value="power"),
        app_commands.Choice(name="레벨", value="level"),
        app_commands.Choice(name="유니온 레벨", value="union"),
    ],
    days=[
        app_commands.Choice(name="최근 7일", value=7),
        app_commands.Choice(name="최근 30일", value=30),
    ],
)
async def maple_growth_ranking(interaction: discord.Interaction, metric: app_commands.Choice[str], days: app_commands.Choice[int] = None):
    period = days.value if days else 7

    async def build():
        return await build_growth_ranking_embed(metric.value, period, top=10)

    await run_maple(interaction, build)


METRIC_LABEL = {"power": "전투력", "level": "레벨", "union": "유니온 레벨"}


async def build_growth_ranking_embed(metric, period, top=10):
    cutoff = kst_date_str(period)
    rows = await adb(
        "SELECT user_id, snap_date, character_name, level, exp, combat_power, union_level "
        "FROM maple_snapshots WHERE snap_date>=%s ORDER BY user_id, snap_date",
        (cutoff,), "all")
    by_user = {}
    for r in rows:
        by_user.setdefault(r[0], []).append(r)
    results = []
    for uid, recs in by_user.items():
        if len(recs) < 2:
            continue
        a, b = recs[0], recs[-1]
        if metric == "power":
            if a[5] is None or b[5] is None:
                continue
            gain, text = b[5] - a[5], f"{fmt_korean_big(b[5])} ({'+' if b[5]-a[5] >= 0 else '-'}{fmt_korean_big(abs(b[5]-a[5]))})"
            key = (gain,)
        elif metric == "level":
            if a[3] is None or b[3] is None:
                continue
            exp_gain = (b[4] or 0) - (a[4] or 0)
            gain = b[3] - a[3]
            text = f"Lv.{b[3]} (+{gain})"
            key = (gain, exp_gain)
        else:
            if a[6] is None or b[6] is None:
                continue
            gain, text = b[6] - a[6], f"{fmt_num(b[6])} ({'+' if b[6]-a[6] >= 0 else '-'}{fmt_num(abs(b[6]-a[6]))})"
            key = (gain,)
        results.append((key, uid, b[2], text, gain))
    results.sort(key=lambda x: x[0], reverse=True)
    e = maple_embed(f"🏆 성장 랭킹 · {METRIC_LABEL[metric]} · 최근 {period}일", color=0xF1C40F)
    shown = [r for r in results if r[4] > 0][:top]
    if not shown:
        e.description = "비교할 수 있는 기록이 아직 부족하거나 증가한 사람이 없어요. (기록은 1시간마다 저장돼요)"
        return e
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (_, uid, name, text, _) in enumerate(shown):
        mark = medals[i] if i < 3 else f"`{i + 1}위`"
        lines.append(f"{mark} **{name}** <@{uid}> — {text}")
    e.description = "\n".join(lines)
    e.set_footer(text=f"인증된 길드원 {len(by_user)}명 중 비교 가능 {len(results)}명")
    return e


@tasks.loop(time=dt_time(hour=9, minute=0, tzinfo=KST))
async def maple_weekly_growth_post():
    """매주 월요일 09:00(KST): 지난 7일 성장 랭킹을 공지 채널에 올립니다."""
    if datetime.now(KST).weekday() != 0:
        return
    ch = bot.get_channel(WEEKLY_RANKING_CHANNEL_ID)
    if not ch:
        return
    try:
        embeds = [await build_growth_ranking_embed("power", 7, top=5),
                  await build_growth_ranking_embed("level", 7, top=5)]
        await ch.send(content="📊 **이번 주 길드 성장 랭킹**", embeds=embeds)
    except Exception as e:
        print(f"[주간 성장 랭킹] 오류: {e}")


@maple_weekly_growth_post.before_loop
async def _before_maple_weekly_growth_post():
    await bot.wait_until_ready()


@bot.tree.command(name="성장기록저장", description="[운영진] 지금 바로 인증된 길드원의 성장 기록을 저장합니다.")
@app_commands.default_permissions(manage_roles=True)
async def maple_snapshot_now(interaction: discord.Interaction):
    if not _is_verify_admin(interaction.user):
        await interaction.response.send_message("❌ 운영진만 사용할 수 있어요.", ephemeral=True)
        return

    async def build():
        ok, fail = await take_all_snapshots()
        return f"✅ 성장 기록 저장 완료 (성공 {ok}명 / 실패 {fail}명)"

    await run_maple(interaction, build, ephemeral=True)


@bot.tree.command(name="확률", description="메이플스토리 공식 확률 정보 페이지 링크를 안내합니다.")
async def maple_probability(interaction: discord.Interaction):
    e = maple_embed("🎲 공식 확률 정보")
    e.description = (
        f"[메이플스토리 공식 확률 정보 페이지]({OFFICIAL_PROBABILITY_URL})\n"
        "큐브·스타포스 등 확률은 업데이트로 자주 바뀌기 때문에, 항상 최신인 공식 페이지에서 확인해주세요."
    )
    await interaction.response.send_message(embed=e)


# ------------------------------------------
# 길드원 가입/탈퇴 알림
# ------------------------------------------
MEMBER_CHANGE_CHANNEL_ID = MAPLE_NOTICE_CHANNEL_ID   # 가입/탈퇴 알림 채널 (바꾸려면 여기 ID만 수정)


def diff_members(old_names, new_names):
    old_map = {n.lower(): n for n in old_names}
    new_map = {n.lower(): n for n in new_names}
    joined = [new_map[k] for k in new_map if k not in old_map]
    left = [old_map[k] for k in old_map if k not in new_map]
    return sorted(joined), sorted(left)


async def alert_verified_left(left_names):
    """길드원 목록에서 빠진 캐릭터 중 인증된 유저가 있고 실제로 길드를 떠났다면 운영진 채널에 알립니다."""
    rows = await adb("SELECT user_id, character_name, ocid FROM maple_verifications", (), "all")
    left_set = {n.lower() for n in left_names}
    alerts = []
    for user_id, char_name, ocid in rows:
        if char_name.lower() not in left_set:
            continue
        try:
            basic = await nexon_get("/maplestory/v1/character/basic", {"ocid": ocid}, ttl=0)
        except Exception:
            continue
        new_name = basic.get("character_name") or char_name
        if new_name != char_name:
            await adb("UPDATE maple_verifications SET character_name=%s WHERE user_id=%s", (new_name, user_id))
        if basic.get("character_guild_name") != MAPLE_GUILD_NAME:
            alerts.append(f"<@{user_id}> · `{new_name}` → 현재 길드: {safe_text(basic.get('character_guild_name'), '없음')}")
    ch = bot.get_channel(VERIFY_ADMIN_CHANNEL_ID)
    if alerts and ch:
        await ch.send("⚠️ **길드를 탈퇴한 것으로 보이는 인증 유저**입니다. 확인 후 필요하면 `/인증해제` 해주세요.\n" + "\n".join(alerts)[:1800])


@tasks.loop(minutes=30)
async def maple_member_change_check():
    """30분마다 길드원 목록을 비교해 가입/탈퇴(닉네임 변경 포함)를 알립니다. 첫 실행은 기록만 합니다."""
    try:
        names, _ = await fetch_guild_member_names()
        if not names:
            return
        row = await adb("SELECT value FROM maple_state WHERE key=%s", ("guild_members",), "one")
        if row:
            old = json.loads(row[0])
            joined, left = diff_members(old, names)
            ch = bot.get_channel(MEMBER_CHANGE_CHANNEL_ID)
            if ch and (joined or left):
                e = maple_embed(f"👥 {MAPLE_GUILD_NAME} 길드원 변동", color=0x1ABC9C)
                if joined:
                    e.add_field(name=f"➕ 가입 ({len(joined)})", value="\n".join(joined)[:1000], inline=False)
                if left:
                    e.add_field(name=f"➖ 탈퇴/닉네임 변경 ({len(left)})", value="\n".join(left)[:1000], inline=False)
                e.set_footer(text=f"현재 길드원 {len(names)}명 · 닉네임을 바꾼 경우 탈퇴+가입으로 함께 표시될 수 있어요")
                await ch.send(embed=e)
            if left:
                await alert_verified_left(left)
        await adb(
            """
            INSERT INTO maple_state (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
            """,
            ("guild_members", json.dumps(names, ensure_ascii=False)))
    except Exception as e:
        print(f"[길드원 변동 알림] 오류: {e}")


@maple_member_change_check.before_loop
async def _before_maple_member_change_check():
    await bot.wait_until_ready()


# ------------------------------------------
# 기존 수동 인증자 등록 (운영진용)
# ------------------------------------------
async def register_verification(user_id, char_name, ocid, admin_id):
    await adb(
        """
        INSERT INTO maple_verifications (user_id, character_name, ocid, verified_at, approved_by)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET character_name=EXCLUDED.character_name,
            ocid=EXCLUDED.ocid, verified_at=EXCLUDED.verified_at, approved_by=EXCLUDED.approved_by
        """,
        (user_id, char_name, ocid, datetime.now(KST).isoformat(), admin_id))


@bot.tree.command(name="수동인증", description="[운영진] 유저를 캐릭터 닉네임으로 인증 기록만 등록합니다. (역할 변경 없음)")
@app_commands.rename(target="대상", nickname="닉네임")
@app_commands.describe(target="인증할 유저", nickname="그 유저의 본캐릭터 닉네임")
@app_commands.default_permissions(manage_roles=True)
async def maple_manual_verify(interaction: discord.Interaction, target: discord.Member, nickname: str):
    if not _is_verify_admin(interaction.user):
        await interaction.response.send_message("❌ 운영진만 사용할 수 있어요.", ephemeral=True)
        return

    async def build():
        ocid = await get_ocid(nickname)
        basic = await nexon_get("/maplestory/v1/character/basic", {"ocid": ocid}, ttl=60)
        real_name = basic.get("character_name") or nickname
        dup = await adb("SELECT user_id FROM maple_verifications WHERE ocid=%s", (ocid,), "one")
        if dup and dup[0] != target.id:
            raise UserFacingError(f"`{real_name}` 캐릭터는 이미 <@{dup[0]}> 님으로 등록되어 있어요. 먼저 `/인증해제`를 해주세요.")
        await register_verification(target.id, real_name, ocid, interaction.user.id)

        notes = []
        guild_name = basic.get("character_guild_name")
        if guild_name != MAPLE_GUILD_NAME:
            notes.append(f"⚠️ 이 캐릭터의 현재 길드는 `{safe_text(guild_name, '없음')}` 입니다")
        return f"✅ {target.mention} → **{real_name}** 인증 등록 완료" + (("\n" + " · ".join(notes)) if notes else "")

    await run_maple(interaction, build, ephemeral=True)


@bot.tree.command(name="일괄인증", description="[운영진] 병아리반 역할이 있는 기존 인증자를 디스코드 닉네임으로 자동 등록합니다.")
@app_commands.rename(execute="실행", scope_role="대상역할")
@app_commands.describe(execute="True로 하면 실제로 등록, 기본(False)은 미리보기만",
                       scope_role="이 역할을 가진 사람만 검사 (비우면 병아리반, @everyone을 고르면 서버 전체)")
@app_commands.default_permissions(manage_roles=True)
async def maple_bulk_verify(interaction: discord.Interaction, execute: bool = False, scope_role: discord.Role = None):
    if not _is_verify_admin(interaction.user):
        await interaction.response.send_message("❌ 운영진만 사용할 수 있어요.", ephemeral=True)
        return

    async def build():
        role = discord.utils.get(interaction.guild.roles, name=VERIFIED_ROLE_NAME)
        if role is None:
            raise UserFacingError(f"`{VERIFIED_ROLE_NAME}` 역할을 찾을 수 없어요.")
        names, _ = await fetch_guild_member_names()
        name_map = {n.lower(): n for n in names}
        rows = await adb("SELECT user_id, ocid FROM maple_verifications", (), "all")
        registered_users = {r[0] for r in rows}
        registered_ocids = {r[1] for r in rows}

        scan_role = scope_role or role
        targets = [m for m in scan_role.members if not m.bot and m.id not in registered_users]
        matched, unmatched, skipped = [], [], []
        for m in targets:
            cand = None
            for c in (m.display_name, m.name, getattr(m, "global_name", None)):
                if c and c.strip().lower() in name_map:
                    cand = name_map[c.strip().lower()]
                    break
            (matched if cand else unmatched).append((m, cand))

        done = 0
        if execute:
            for m, cand in matched:
                try:
                    ocid = await get_ocid(cand)
                    if ocid in registered_ocids:
                        skipped.append((m, f"`{cand}` 는 이미 다른 유저로 등록됨"))
                        continue
                    await register_verification(m.id, cand, ocid, interaction.user.id)
                    registered_ocids.add(ocid)
                    done += 1
                except Exception as e:
                    skipped.append((m, f"`{cand}` 처리 실패: {e}"))
                await asyncio.sleep(0.2)

        e = maple_embed(("✅ 일괄 인증 결과" if execute else "🔍 일괄 인증 미리보기"), color=0x2ECC71 if execute else 0x95A5A6)
        e.description = (
            f"{scan_role.name} 역할 보유 중 미등록: **{len(targets)}명**\n"
            f"닉네임이 길드원 캐릭터와 일치: **{len(matched)}명**"
            + (f" → 등록 완료 **{done}명**" if execute else " (실행하려면 `/일괄인증 실행:True`)")
            + f"\n일치하지 않음: **{len(unmatched)}명**"
        )
        if matched and not execute:
            lines = [f"{m.mention} → {c}" for m, c in matched]
            for i, chunk in enumerate(chunk_lines(lines, 1000)[:3]):
                e.add_field(name="등록 예정" if i == 0 else "​", value=chunk, inline=False)
        if unmatched:
            lines = [f"{m.mention} (`{m.display_name}`)" for m, _ in unmatched]
            for i, chunk in enumerate(chunk_lines(lines, 1000)[:3]):
                e.add_field(name="수동 처리 필요 (`/수동인증`)" if i == 0 else "​", value=chunk, inline=False)
        if skipped:
            lines = [f"{m.mention} — {why}" for m, why in skipped]
            e.add_field(name="건너뜀", value="\n".join(lines)[:1000], inline=False)
        return e

    await run_maple(interaction, build, ephemeral=True)


# ------------------------------------------
# 신규 입장자 인증 안내
# ------------------------------------------
GUILD_ID_FOR_COMMANDS = 1498077956839313559
_verify_cmd_mention_cache = {"value": None}


async def get_verify_command_mention():
    """클릭하면 /인증 입력창이 열리는 명령어 멘션(</인증:ID>)을 만듭니다. 실패하면 일반 텍스트."""
    if _verify_cmd_mention_cache["value"]:
        return _verify_cmd_mention_cache["value"]
    try:
        cmds = await bot.tree.fetch_commands(guild=discord.Object(id=GUILD_ID_FOR_COMMANDS))
        for c in cmds:
            if c.name == "인증":
                _verify_cmd_mention_cache["value"] = c.mention
                return c.mention
    except Exception as e:
        print(f"[인증 안내] 명령어 멘션 조회 실패: {e}")
    return "`/인증`"


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot or member.guild.id != GUILD_ID_FOR_COMMANDS:
        return
    ch = member.guild.get_channel(VERIFY_REQUEST_CHANNEL_ID)
    if ch is None:
        return
    try:
        cmd = await get_verify_command_mention()
        await ch.send(
            f"{member.mention} 님 환영합니다! 🎉\n"
            f"인증 채널에서 {cmd} << 버튼 누른후 길드에 가입하실 본캐 닉네임과 "
            f"메이플핸즈(전체서버), 모든캐릭터 전부다 찍어서 스크린샷 첨부해서 보내주시면 됩니다.",
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
    except Exception as e:
        print(f"[인증 안내] 전송 실패: {e}")


def start_maple_background_tasks():
    """API 키가 있을 때만 메이플 관련 반복 작업을 시작합니다."""
    if not NEXON_API_KEY:
        print("[메이플] NEXON_API_KEY 가 없어 공지 알림/길드원 점검을 시작하지 않습니다.")
        return
    if not maple_notice_check.is_running():
        maple_notice_check.start()
    if not maple_guild_member_check.is_running():
        maple_guild_member_check.start()
    if not maple_member_change_check.is_running():
        maple_member_change_check.start()
    if not maple_daily_snapshot.is_running():
        maple_daily_snapshot.start()
    if not maple_weekly_growth_post.is_running():
        maple_weekly_growth_post.start()


# 봇 실행부
# ------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("디스코드 토큰이 설정되지 않았습니다.")