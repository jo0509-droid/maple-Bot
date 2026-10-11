from datetime import datetime, timedelta, timezone
import os
import random
import asyncio

import psycopg2

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
            exp INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    cursor.close()
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

def _apply_voice_exp_sync(gains: dict):
    """경험치를 지급하고, 레벨업한 유저 목록을 돌려줍니다.
    각 항목: {"user_id", "level", "milestones", "pieces_gained", "pieces_total"}
    (milestones = 이번에 새로 달성한 10레벨 단위 개수)"""
    level_ups = []
    conn = get_db_connection_plain()
    try:
        cur = conn.cursor()
        for user_id, gain in gains.items():
            cur.execute("SELECT level, exp FROM activity_users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            level, exp = (row[0], row[1]) if row else (1, 0)
            old_level = level
            exp += gain
            while exp >= activity_exp_required(level):
                exp -= activity_exp_required(level)
                level += 1
            cur.execute(
                """
                INSERT INTO activity_users (user_id, level, exp) VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET level = EXCLUDED.level, exp = EXCLUDED.exp
                """,
                (user_id, level, exp),
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
FORTUNE_PIECE_CHANCE = 20  # %

@bot.tree.command(name="포춘쿠키", description="오늘의 행운의 포춘쿠키를 뽑고 행복한 문구를 확인합니다. (하루 1회)")
async def fortune_cookie(interaction: discord.Interaction):
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
        cur.execute("SELECT level, exp FROM activity_users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        cur.close()
        return (row[0], row[1]) if row else (1, 0)
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
        level, exp = await asyncio.to_thread(_get_activity_data_sync, target_user.id)
        max_exp = activity_exp_required(level)

        filled = int(exp / max_exp * 10)
        bar = "🟩" * filled + "⬜" * (10 - filled)

        embed = discord.Embed(title=f"🎙️ {target_user.name}님의 활동 정보", color=0x2ECC71)
        embed.add_field(name="활동 레벨", value=f"Lv. {level}", inline=True)
        embed.add_field(name="경험치", value=f"{exp:,} / {max_exp:,} EXP", inline=True)
        embed.add_field(name="진행도", value=bar, inline=False)
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
# 봇 실행부
# ------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("디스코드 토큰이 설정되지 않았습니다.")