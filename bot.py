import sqlite3
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ================== CONFIG ==================
BOT_TOKEN = "8490669107:AAFef3aUkzjLiDHySMXfwnL82022DqPSpeo"

MAX_MSG_PER_MIN = 5
MUTE_SECONDS = 600  # 10 phút

BLACKLIST = [
    "airdrop", "free money", "dm me",
    "guaranteed profit", "1000%", "scam"
]

WHITELIST_DOMAINS = {
    "x.com", "twitter.com",
    "tradingview.com",
    "t.me", "telegram.me"
}

# ================== HELPER ==================
def bi(vn: str, en: str) -> str:
    return f"🇻🇳 {vn}\n🇬🇧 {en}"

# ================== DATABASE ==================
conn = sqlite3.connect("community.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    score INTEGER DEFAULT 0,
    role TEXT DEFAULT 'Newbie',
    join_time INTEGER
)
""")
conn.commit()

# migration thêm warns
try:
    cur.execute("ALTER TABLE users ADD COLUMN warns INTEGER DEFAULT 0")
    conn.commit()
except sqlite3.OperationalError:
    pass

# ================== MEMORY ==================
msg_log = defaultdict(deque)

# ================== DB UTILS ==================
def add_user(user_id: int):
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, score, role, join_time, warns) "
        "VALUES (?, 0, 'Newbie', ?, 0)",
        (user_id, int(time.time()))
    )
    conn.commit()


def get_user(user_id: int):
    cur.execute("SELECT score, role, warns FROM users WHERE user_id = ?", (user_id,))
    return cur.fetchone()


def update_score(user_id: int, delta: int):
    cur.execute("UPDATE users SET score = score + ? WHERE user_id = ?", (delta, user_id))
    conn.commit()


def add_warn(user_id: int):
    cur.execute("UPDATE users SET warns = warns + 1 WHERE user_id = ?", (user_id,))
    conn.commit()


def promote_if_needed(user_id: int):
    data = get_user(user_id)
    if not data:
        return None
    score, role, _ = data
    if score >= 100 and role == "Newbie":
        cur.execute("UPDATE users SET role='Contributor' WHERE user_id=?", (user_id,))
        conn.commit()
        return "Contributor"
    return None


def domain_allowed(url: str) -> bool:
    try:
        domain = urlparse(url).netloc.replace("www.", "")
        return domain in WHITELIST_DOMAINS
    except:
        return False

# ================== HANDLERS ==================
async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.new_chat_members:
        return

    for m in update.message.new_chat_members:
        add_user(m.id)
        await update.message.reply_text(
            f"👋 *Welcome {m.full_name}*\n\n"
            "🇻🇳\n"
            "• Cộng đồng thảo luận trading & research\n"
            "• Đọc *nội quy đã ghim* trước khi chat\n"
            "• Tôn trọng – không spam – không bán hàng\n\n"
            "🇬🇧\n"
            "• Trading & research discussion community\n"
            "• Read the *pinned rules* before chatting\n"
            "• Be respectful – no spam – no promotion\n\n"
            "📌 *Chất lượng > Số lượng*\n"
            "📌 *Quality > Quantity*",
            parse_mode="Markdown"
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text or not msg.from_user:
        return

    user = msg.from_user
    text = msg.text.lower()
    add_user(user.id)

    # ===== RATE LIMIT (WARN → MUTE → BAN) =====
    now = time.time()
    msg_log[user.id].append(now)
    while msg_log[user.id] and now - msg_log[user.id][0] > 60:
        msg_log[user.id].popleft()

    if len(msg_log[user.id]) >= MAX_MSG_PER_MIN:
        score, role, warns = get_user(user.id)
        member = await msg.chat.get_member(user.id)

        if member.status in ["administrator", "creator"]:
            await msg.reply_text(
                bi(
                    "Admin gửi tin quá nhanh (không áp dụng phạt).",
                    "Admin is sending messages too fast (no action applied)."
                ),
                parse_mode="Markdown"
            )
            msg_log[user.id].clear()
            return

        if warns == 0:
            add_warn(user.id)
            await msg.reply_text(
                bi(
                    "Cảnh báo: Bạn đang gửi tin quá nhanh.",
                    "Warning: You are sending messages too fast."
                ),
                parse_mode="Markdown"
            )
        elif warns == 1:
            add_warn(user.id)
            await msg.chat.restrict_member(
                user.id,
                ChatPermissions(can_send_messages=False),
                until_date=now + MUTE_SECONDS
            )
            await msg.reply_text(
                bi(
                    "Bạn đã bị mute 10 phút vì spam.",
                    "You have been muted for 10 minutes due to spam."
                ),
                parse_mode="Markdown"
            )
        else:
            await msg.chat.ban_member(user.id)
            await msg.reply_text(
                bi(
                    "Bạn đã bị ban do spam nhiều lần.",
                    "You have been banned due to repeated spam."
                ),
                parse_mode="Markdown"
            )

        msg_log[user.id].clear()
        return

    # ===== BLACKLIST =====
    for kw in BLACKLIST:
        if kw in text:
            await msg.delete()
            add_warn(user.id)
            return

    # ===== LINK FILTER =====
    if "http://" in text or "https://" in text:
        urls = [w for w in text.split() if w.startswith("http")]
        for u in urls:
            if not domain_allowed(u):
                await msg.delete()
                await msg.reply_text(
                    bi(
                        "Link này không được phép. Chỉ cho phép domain đáng tin cậy.",
                        "This link is not allowed. Only trusted domains are permitted."
                    ),
                    parse_mode="Markdown"
                )
                return

    # ===== NORMAL MESSAGE =====
    update_score(user.id, 1)
    new_role = promote_if_needed(user.id)
    if new_role:
        await msg.reply_text(
            bi(
                f"Chúc mừng! Bạn đã lên vai trò {new_role}.",
                f"Congrats! You have been promoted to {new_role}."
            ),
            parse_mode="Markdown"
        )

# ================== COMMANDS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Community Ops Bot*\n\n"
        "🇻🇳 Giữ group sạch – chất – không spam\n"
        "🇬🇧 Clean • Quality • No spam\n\n"
        "*Commands*\n"
        "• /faq – Giới thiệu group\n"
        "• /alpha – Alpha là gì?\n"
        "• /signals – Chính sách signals\n"
        "• /glossary – Thuật ngữ trading\n"
        "• /myrole – Trạng thái của bạn\n\n"
        "📌 Đọc nội quy đã ghim | Read pinned rules",
        parse_mode="Markdown"
    )


async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ *FAQ – About this group*\n\n"
        "🇻🇳\n"
        "• Thảo luận trading & market insights\n"
        "• Chia sẻ research, góc nhìn cá nhân\n"
        "• Tôn trọng – không spam – không bán hàng\n\n"
        "🇬🇧\n"
        "• Trading & market discussions\n"
        "• Research and personal insights\n"
        "• Be respectful – no spam – no promotion\n\n"
        "📌 *Chất lượng > Số lượng*\n"
        "📌 *Quality > Quantity*",
        parse_mode="Markdown"
    )


async def alpha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 *Alpha – What does it mean?*\n\n"
        "🇻🇳\n"
        "• Alpha = góc nhìn, nhận định sớm\n"
        "• KHÔNG phải lời khuyên đầu tư\n"
        "• Tự chịu trách nhiệm quyết định\n\n"
        "🇬🇧\n"
        "• Alpha = early insights or perspectives\n"
        "• NOT financial advice\n"
        "• You are responsible for your decisions\n\n"
        "⚠️ *Always DYOR*",
        parse_mode="Markdown"
    )


async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚫 *Signals Policy*\n\n"
        "🇻🇳\n"
        "• Group không public signals\n"
        "• Không bán, không quảng cáo\n"
        "• Chỉ mang tính thảo luận\n\n"
        "🇬🇧\n"
        "• No public signals\n"
        "• No selling or promotion\n"
        "• Discussion purposes only\n\n"
        "❗ Vi phạm có thể bị mute / ban",
        parse_mode="Markdown"
    )


async def glossary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📘 *Trading & Crypto Glossary*\n\n"
        "📊 *Market Basics*\n"
        "• Spot – Giao dịch trực tiếp\n"
        "• Futures – Phái sinh có đòn bẩy\n"
        "• Leverage – Đòn bẩy\n"
        "• Funding Rate – Phí giữ lệnh\n\n"
        "📈 *Price Action*\n"
        "• Support / Resistance – Hỗ trợ / Kháng cự\n"
        "• Breakout / Fake Breakout\n"
        "• BOS – Break of Structure\n"
        "• CHoCH – Change of Character\n\n"
        "💧 *Liquidity & Orderflow*\n"
        "• Liquidity – Thanh khoản\n"
        "• Liquidity Sweep – Quét thanh khoản\n"
        "• Order Block – Vùng tổ chức\n"
        "• FVG – Fair Value Gap\n\n"
        "⚖️ *Risk Management*\n"
        "• R:R – Risk / Reward\n"
        "• SL / TP – Stop Loss / Take Profit\n"
        "• Drawdown – Sụt giảm tài khoản\n\n"
        "🧠 *Psychology*\n"
        "• FOMO – Sợ bỏ lỡ\n"
        "• Overtrade – Giao dịch quá mức\n"
        "• Revenge Trade – Gỡ lỗ cảm tính\n\n"
        "🔗 *On-chain / Crypto*\n"
        "• TVL – Tổng giá trị khóa\n"
        "• FDV – Vốn hóa pha loãng\n"
        "• Whale – Ví lớn\n"
        "• Smart Money – Dòng tiền tổ chức",
        parse_mode="Markdown"
    )


async def myrole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    score, role, warns = get_user(user.id)
    await update.message.reply_text(
        f"👤 *Your Status*\n\n"
        f"🇻🇳 Vai trò: `{role}` | Điểm: `{score}` | Cảnh báo: `{warns}`\n"
        f"🇬🇧 Role: `{role}` | Score: `{score}` | Warnings: `{warns}`\n\n"
        "ℹ️ *Hãy đóng góp tích cực để mở thêm quyền.*\n"
        "ℹ️ *Keep contributing to unlock more permissions.*",
        parse_mode="Markdown"
    )

# ================== MAIN ==================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("faq", faq))
    app.add_handler(CommandHandler("alpha", alpha))
    app.add_handler(CommandHandler("signals", signals))
    app.add_handler(CommandHandler("glossary", glossary))
    app.add_handler(CommandHandler("myrole", myrole))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
    app.add_handler(
        MessageHandler(filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND, handle_message)
    )

    print("🤖 Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
