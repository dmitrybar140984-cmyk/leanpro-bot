"""
LeanProRus Telegram Bot
Функции: расписание постов, приветствие, модерация, команды админа
"""

import os
import json
import logging
from datetime import time, datetime
from pathlib import Path

from telegram import Update, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ChatMemberHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ─── CONFIG ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

BOT_TOKEN  = os.environ["BOT_TOKEN"]
CHANNEL_ID = int(os.environ["CHANNEL_ID"])   # -100xxxxxxxxxx
GROUP_ID   = int(os.environ.get("GROUP_ID", "0"))   # linked discussion group (0 = нет)
ADMIN_IDS  = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]

SCHEDULE_FILE = Path("schedule.json")

# Стоп-слова для модерации (дополни по необходимости)
SPAM_KEYWORDS = [
    "казино", "crypto", "крипта", "биткоин", "заработок без вложений",
    "пассивный доход", "кликни", "перейди по ссылке", "вакансия удалённо"
]

WELCOME_TEXT = (
    "👋 Добро пожаловать, {name}!\n\n"
    "Вы в сообществе <b>LeanPro</b> — экспертов по бережливому производству.\n\n"
    "📌 <b>Правила:</b>\n"
    "• Общение только по теме Lean, 5S, VSM, Кайдзен, Six Sigma\n"
    "• Без спама, рекламы и ссылок от незнакомцев\n"
    "• Уважайте коллег\n\n"
    "🎯 Пройдите бесплатный тест на уровень знаний Lean — leanpro.ru/quiz"
)

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def load_schedule() -> list:
    if SCHEDULE_FILE.exists():
        return json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    return []

def save_schedule(data: list):
    SCHEDULE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def admin_only(func):
    """Декоратор: отклоняет команды не от админа."""
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ Только для администраторов.")
            return
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper

# ─── ADMIN COMMANDS ───────────────────────────────────────────────────────────

@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 <b>LeanProRus Bot активен</b>\n\n"
        "<b>Команды:</b>\n"
        "/post &lt;текст&gt; — опубликовать пост прямо сейчас\n"
        "/daily ЧЧ:ММ &lt;текст&gt; — ежедневный пост в указанное время\n"
        "/once ДД.ММ ЧЧ:ММ &lt;текст&gt; — разовый пост в дату и время\n"
        "/list — список запланированных постов\n"
        "/cancel &lt;id&gt; — отменить запланированный пост\n"
        "/stats — статистика канала\n\n"
        "💡 Посты поддерживают HTML: &lt;b&gt;, &lt;i&gt;, &lt;a href='...'&gt;",
        parse_mode="HTML"
    )

@admin_only
async def cmd_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Немедленная публикация: /post <текст>"""
    if not ctx.args:
        await update.message.reply_text("Использование: /post &lt;текст&gt;", parse_mode="HTML")
        return
    text = " ".join(ctx.args)
    await ctx.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="HTML")
    await update.message.reply_text("✅ Пост опубликован!")

@admin_only
async def cmd_daily(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ежедневный пост: /daily ЧЧ:ММ <текст>"""
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "Использование: /daily 09:00 &lt;текст поста&gt;", parse_mode="HTML"
        )
        return
    try:
        h, m = map(int, ctx.args[0].split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text("Неверный формат времени. Используйте ЧЧ:ММ (например 09:00)")
        return

    text = " ".join(ctx.args[1:])
    job_name = f"daily_{h:02d}{m:02d}_{update.effective_user.id}"

    # Удаляем старый job с таким же именем
    for job in ctx.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()

    ctx.job_queue.run_daily(
        callback=_send_post,
        time=time(h, m, tzinfo=None),
        data={"text": text, "type": "daily", "time": f"{h:02d}:{m:02d}"},
        name=job_name,
    )

    # Сохраняем для отображения в /list
    schedule = load_schedule()
    schedule.append({"id": job_name, "type": "daily", "time": f"{h:02d}:{m:02d}", "text": text[:80]})
    save_schedule(schedule)

    await update.message.reply_text(
        f"📅 Ежедневный пост запланирован на <b>{h:02d}:{m:02d}</b>",
        parse_mode="HTML"
    )

@admin_only
async def cmd_once(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Разовый пост: /once ДД.ММ ЧЧ:ММ <текст>"""
    if len(ctx.args) < 3:
        await update.message.reply_text(
            "Использование: /once 15.07 14:30 &lt;текст&gt;", parse_mode="HTML"
        )
        return
    try:
        day, month = map(int, ctx.args[0].split("."))
        h, m      = map(int, ctx.args[1].split(":"))
        year = datetime.now().year
        run_at = datetime(year, month, day, h, m)
        if run_at < datetime.now():
            run_at = run_at.replace(year=year + 1)
    except ValueError:
        await update.message.reply_text("Неверный формат. Пример: /once 15.07 14:30 Текст")
        return

    text = " ".join(ctx.args[2:])
    job_name = f"once_{run_at.strftime('%d%m%H%M')}_{update.effective_user.id}"

    ctx.job_queue.run_once(
        callback=_send_post,
        when=run_at,
        data={"text": text, "type": "once"},
        name=job_name,
    )

    schedule = load_schedule()
    schedule.append({
        "id": job_name, "type": "once",
        "time": run_at.strftime("%d.%m %H:%M"), "text": text[:80]
    })
    save_schedule(schedule)

    await update.message.reply_text(
        f"📅 Пост запланирован на <b>{run_at.strftime('%d.%m.%Y %H:%M')}</b>",
        parse_mode="HTML"
    )

async def _send_post(ctx: ContextTypes.DEFAULT_TYPE):
    """Внутренний callback для отправки поста."""
    text = ctx.job.data["text"]
    await ctx.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="HTML")
    log.info(f"Scheduled post sent: {text[:50]}...")
    # Удаляем разовые из файла
    if ctx.job.data.get("type") == "once":
        schedule = [s for s in load_schedule() if s["id"] != ctx.job.name]
        save_schedule(schedule)

@admin_only
async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Список активных планировщиков."""
    schedule = load_schedule()
    if not schedule:
        await update.message.reply_text("📭 Нет запланированных постов.")
        return
    lines = ["📋 <b>Запланированные посты:</b>\n"]
    for i, s in enumerate(schedule, 1):
        lines.append(
            f"{i}. [{s['type'].upper()}] {s['time']} — {s['text'][:60]}...\n"
            f"   ID: <code>{s['id']}</code>"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

@admin_only
async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отмена поста: /cancel <id>"""
    if not ctx.args:
        await update.message.reply_text("Использование: /cancel &lt;id&gt;", parse_mode="HTML")
        return
    job_name = " ".join(ctx.args)
    removed = 0
    for job in ctx.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()
        removed += 1
    schedule = [s for s in load_schedule() if s["id"] != job_name]
    save_schedule(schedule)
    if removed:
        await update.message.reply_text(f"🗑 Пост <code>{job_name}</code> отменён.", parse_mode="HTML")
    else:
        await update.message.reply_text("Пост не найден. Проверь ID через /list")

@admin_only
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Статистика канала."""
    try:
        chat  = await ctx.bot.get_chat(CHANNEL_ID)
        count = await ctx.bot.get_chat_member_count(CHANNEL_ID)
        await update.message.reply_text(
            f"📊 <b>Статистика канала</b>\n\n"
            f"📢 Название: {chat.title}\n"
            f"👥 Подписчиков: <b>{count:,}</b>\n"
            f"🔗 Username: @{chat.username or '—'}",
            parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

# ─── WELCOME NEW MEMBERS ──────────────────────────────────────────────────────

async def on_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Приветствие новых участников в группе комментариев."""
    result = update.chat_member
    if not result:
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status

    joined = (
        old_status in (ChatMember.LEFT, ChatMember.BANNED)
        and new_status in (ChatMember.MEMBER, ChatMember.RESTRICTED)
    )
    if not joined:
        return

    user = result.new_chat_member.user
    name = user.first_name or "участник"

    await ctx.bot.send_message(
        chat_id=result.chat.id,
        text=WELCOME_TEXT.format(name=name),
        parse_mode="HTML",
    )
    log.info(f"Welcome sent to {user.id} ({name}) in {result.chat.id}")

# ─── MODERATION ───────────────────────────────────────────────────────────────

async def moderate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Модерация комментариев в группе."""
    msg = update.message
    if not msg or not msg.chat or msg.chat.id != GROUP_ID:
        return

    user = msg.from_user
    if not user:
        return

    # Пропускаем администраторов
    try:
        member = await ctx.bot.get_chat_member(msg.chat_id, user.id)
        if member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
            return
    except Exception:
        return

    reason = None

    # Проверка стоп-слов
    if msg.text:
        text_low = msg.text.lower()
        for kw in SPAM_KEYWORDS:
            if kw in text_low:
                reason = f"стоп-слово «{kw}»"
                break

    # Проверка ссылок (запрещаем для не-админов)
    if not reason and msg.entities:
        link_types = {"url", "text_link"}
        for ent in msg.entities:
            if ent.type in link_types:
                reason = "ссылка"
                break

    if not reason:
        return

    try:
        await msg.delete()
        await ctx.bot.ban_chat_member(msg.chat_id, user.id)
        username = f"@{user.username}" if user.username else user.first_name
        await ctx.bot.send_message(
            chat_id=msg.chat_id,
            text=f"🚫 {username} заблокирован за нарушение правил ({reason}).",
        )
        log.info(f"Banned {user.id} in {msg.chat_id} for: {reason}")
    except Exception as e:
        log.error(f"Moderation error: {e}")

# ─── INLINE BUTTON: unban ────────────────────────────────────────────────────

async def cmd_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Разбан: /unban <user_id>"""
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Использование: /unban &lt;user_id&gt;", parse_mode="HTML")
        return
    try:
        uid = int(ctx.args[0])
        await ctx.bot.unban_chat_member(GROUP_ID, uid)
        await update.message.reply_text(f"✅ Пользователь {uid} разбанен.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

# ─── STARTUP: restore daily jobs ─────────────────────────────────────────────

async def post_init(app: Application):
    """Восстанавливаем ежедневные задания после перезапуска."""
    schedule = load_schedule()
    restored = 0
    for s in schedule:
        if s["type"] != "daily":
            continue
        try:
            h, m = map(int, s["time"].split(":"))
            app.job_queue.run_daily(
                callback=_send_post,
                time=time(h, m),
                data={"text": s["text"], "type": "daily", "time": s["time"]},
                name=s["id"],
            )
            restored += 1
        except Exception as e:
            log.warning(f"Could not restore job {s['id']}: {e}")
    log.info(f"Restored {restored} daily jobs from schedule.json")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Admin commands
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_start))
    app.add_handler(CommandHandler("post",   cmd_post))
    app.add_handler(CommandHandler("daily",  cmd_daily))
    app.add_handler(CommandHandler("once",   cmd_once))
    app.add_handler(CommandHandler("list",   cmd_list))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(CommandHandler("unban",  cmd_unban))

    # Welcome new members
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))

    # Moderation (only if GROUP_ID is set)
    if GROUP_ID:
        app.add_handler(
            MessageHandler(filters.Chat(GROUP_ID) & filters.ALL, moderate)
        )

    log.info("Bot started. Listening...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
