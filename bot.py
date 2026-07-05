"""
LeanProRus Telegram Bot
Функции: расписание постов, приветствие, модерация, команды админа
"""

import os
import json
import random
import logging
import threading
from datetime import time, datetime
from pathlib import Path

from groq import Groq
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

# Fallback values from cfg.py when Railway Variables don't load
try:
    import cfg as _cfg
except ImportError:
    _cfg = None

def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name, "") or getattr(_cfg, name, default) or default).strip()

BOT_TOKEN        = _env("BOT_TOKEN")
CHANNEL_ID       = int(_env("CHANNEL_ID", "0"))
GROUP_ID         = int(_env("GROUP_ID", "0"))
ADMIN_IDS        = [int(x) for x in _env("ADMIN_IDS").split(",") if x.strip()]
GROQ_KEY         = _env("GROQ_API_KEY")
AUTO_POST_TIME   = _env("AUTO_POST_TIME", "09:00")

if not BOT_TOKEN:
    log.error("❌ BOT_TOKEN не задан ни в env, ни в cfg.py!")
    raise SystemExit(1)

log.info(f"✅ BOT_TOKEN загружен ({BOT_TOKEN[:10]}...)")
log.info(f"CHANNEL_ID={CHANNEL_ID} | GROUP_ID={GROUP_ID} | ADMIN_IDS={ADMIN_IDS}")

# Переменные для сервера оплаты
PAY_YK_SECRET    = _env("YOOKASSA_SECRET")
PAY_YK_SHOP_ID   = _env("YOOKASSA_SHOP_ID")
PAY_GMAIL_USER   = _env("GMAIL_USER")
PAY_GMAIL_PASS   = _env("GMAIL_PASS")
PAY_YA_API_KEY   = _env("YANDEX_API_KEY")
PAY_YA_FOLDER_ID = _env("YANDEX_FOLDER_ID")
log.info(f"Payment vars: YK={'set' if PAY_YK_SECRET else 'MISSING'} Gmail={'set' if PAY_GMAIL_USER else 'MISSING'} YaTTS={'set' if PAY_YA_API_KEY else 'MISSING'}")

log.info(f"AI auto-post: {'включён' if GROQ_KEY else 'выключен (нет GROQ_API_KEY)'} в {AUTO_POST_TIME}")

SCHEDULE_FILE = Path("schedule.json")

# Темы для ротации AI-постов
LEAN_TOPICS = [
    ("5S", "Система 5S: Сортировка, Систематизация, Уборка, Стандартизация, Совершенствование"),
    ("VSM", "Картирование потока создания ценности (VSM): как видеть и устранять потери"),
    ("Кайдзен", "Кайдзен: философия непрерывных малых улучшений"),
    ("Канбан", "Канбан: визуальное управление потоком и WIP-лимиты"),
    ("Такт-тайм", "Такт-тайм: ритм производства в соответствии со спросом клиента"),
    ("8 видов потерь", "8 видов муда: перепроизводство, ожидание, транспортировка, лишние движения, дефекты, запасы, излишняя обработка, неиспользованный потенциал"),
    ("SMED", "SMED: быстрая переналадка оборудования за минуты вместо часов"),
    ("TPM", "TPM: всеобщее обслуживание оборудования и роль операторов"),
    ("Poka-Yoke", "Poka-Yoke: защита от ошибок и создание надёжных процессов"),
    ("Стандартизация", "Стандартизированная работа: основа стабильности и базис для улучшений"),
    ("Heijunka", "Heijunka: выравнивание производства по объёму и номенклатуре"),
    ("Андон", "Андон и Jidoka: система остановки при отклонении и встроенное качество"),
    ("Узкое место", "Теория ограничений: как найти и устранить узкое место в потоке"),
    ("OEE", "OEE (Общая эффективность оборудования): как измерить и повысить"),
    ("A3", "A3-отчёт: структурированное решение проблем на одном листе"),
]

TOPIC_FILE = Path("last_topic.json")

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
    "🎯 Пройдите бесплатный тест на уровень знаний Lean — leanprorus.ru/quiz"
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
    ai_status = "✅ включён" if GROQ_KEY else "❌ нет GROQ_API_KEY"
    await update.message.reply_text(
        "🤖 <b>LeanProRus Bot активен</b>\n\n"
        "<b>AI-публикации:</b>\n"
        f"/aipost — опубликовать AI-пост прямо сейчас\n"
        f"/aipost &lt;тема&gt; — пост на произвольную тему\n"
        f"Авто-пост ежедневно в {AUTO_POST_TIME}: {ai_status}\n\n"
        "<b>Ручные посты:</b>\n"
        "/post &lt;текст&gt; — опубликовать текст прямо сейчас\n"
        "/daily ЧЧ:ММ &lt;текст&gt; — ежедневный пост в указанное время\n"
        "/once ДД.ММ ЧЧ:ММ &lt;текст&gt; — разовый пост в дату и время\n"
        "/list — список запланированных постов\n"
        "/cancel &lt;id&gt; — отменить пост\n"
        "/stats — статистика канала\n\n"
        "<b>Коды доступа:</b>\n"
        "/codes — список всех выданных кодов\n"
        "/restore &lt;json&gt; — восстановить коды вручную\n\n"
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

# ─── AI CONTENT GENERATION ───────────────────────────────────────────────────

def _next_topic() -> tuple[str, str]:
    """Возвращает следующую тему по кругу."""
    data = {}
    if TOPIC_FILE.exists():
        data = json.loads(TOPIC_FILE.read_text(encoding="utf-8"))
    idx = (data.get("idx", -1) + 1) % len(LEAN_TOPICS)
    TOPIC_FILE.write_text(json.dumps({"idx": idx}), encoding="utf-8")
    return LEAN_TOPICS[idx]

async def generate_ai_post(topic_name: str, topic_desc: str) -> str:
    """Генерирует пост через Groq API (бесплатно)."""
    client = Groq(api_key=GROQ_KEY)
    prompt = (
        f"Напиши пост для Telegram-канала о бережливом производстве.\n"
        f"Тема: {topic_desc}\n\n"
        f"Требования:\n"
        f"- Длина: 180–250 слов\n"
        f"- Язык: русский, профессиональный но живой\n"
        f"- Начни с цепляющего заголовка с эмодзи\n"
        f"- Дай 2–3 конкретных практических совета или примера\n"
        f"- Используй эмодзи для структуры, но не перебарщивай\n"
        f"- Без хэштегов\n"
        f"- Только текст поста, без вводных слов типа «Вот пост:»"
    )
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.choices[0].message.content.strip()
    footer = '\n\n🔗 Курсы, симуляторы и инструменты Lean: <a href="https://leanprorus.ru">leanprorus.ru</a>'
    return text + footer

async def auto_ai_post(ctx: ContextTypes.DEFAULT_TYPE):
    """Ежедневный авто-пост с AI-контентом."""
    if not GROQ_KEY:
        log.warning("ANTHROPIC_API_KEY не задан — авто-пост пропущен")
        return
    try:
        topic_name, topic_desc = _next_topic()
        log.info(f"Генерирую пост на тему: {topic_name}")
        text = await generate_ai_post(topic_name, topic_desc)
        await ctx.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="HTML")
        log.info(f"Авто-пост опубликован: {topic_name}")
    except Exception as e:
        log.error(f"Ошибка авто-поста: {e}")

@admin_only
async def cmd_aipost(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ручной запуск AI-поста: /aipost [тема]"""
    if not GROQ_KEY:
        await update.message.reply_text("❌ GROQ_API_KEY не задан в Variables")
        return
    await update.message.reply_text("⏳ Генерирую пост...")
    try:
        if ctx.args:
            topic_name = " ".join(ctx.args)
            topic_desc = topic_name
        else:
            topic_name, topic_desc = _next_topic()
        text = await generate_ai_post(topic_name, topic_desc)
        await ctx.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="HTML")
        await update.message.reply_text(f"✅ Пост опубликован! Тема: {topic_name}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

# ─── TELEGRAM BACKUP / RESTORE ───────────────────────────────────────────────

BACKUP_MARKER = "🔐LEANPRO_BACKUP\n"

async def _restore_codes_if_needed(bot):
    """On startup, restore codes.json from Telegram pinned backup if the file is empty."""
    import payment_server
    codes = payment_server.load_codes()
    total = sum(len(v) for v in codes.values())
    if total > 0:
        log.info(f"codes.json OK — {total} entries, no restore needed")
        return
    if not ADMIN_IDS:
        return
    admin_id = ADMIN_IDS[0]
    log.info("codes.json is empty — attempting Telegram backup restore...")
    try:
        chat = await bot.get_chat(admin_id)
        pinned = getattr(chat, "pinned_message", None)
        if not pinned or not getattr(pinned, "text", None):
            log.warning("No pinned backup message found in admin chat")
            await bot.send_message(
                admin_id,
                "⚠️ <b>Коды доступа сброшены после редеплоя!</b>\n"
                "Автовосстановление не удалось — нет закреплённой резервной копии.\n"
                "Используйте /restore &lt;json&gt; для ручного восстановления.",
                parse_mode="HTML",
            )
            return
        text = pinned.text
        if not text.startswith(BACKUP_MARKER):
            log.warning("Pinned message is not a LeanPro backup")
            return
        restored = json.loads(text[len(BACKUP_MARKER):])
        payment_server._write_codes_file(restored)
        total = sum(len(v) for v in restored.values())
        log.info(f"✅ Restored {total} access codes from Telegram backup!")
        await bot.send_message(
            admin_id,
            f"✅ <b>Коды автоматически восстановлены после редеплоя!</b>\n"
            f"📊 Записей: <b>{total}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        log.error(f"Telegram restore error: {e}")

@admin_only
async def cmd_codes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Список всех кодов доступа: /codes"""
    import payment_server
    codes = payment_server.load_codes()
    if not codes:
        await update.message.reply_text("📭 Кодов доступа нет.")
        return
    total = sum(len(v) for v in codes.values())
    lines = [f"🔑 <b>Коды доступа ({total}):</b>\n"]
    for course_id, emails in codes.items():
        cname = payment_server.COURSE_NAMES.get(course_id, course_id)
        lines.append(f"\n📚 <b>{cname}:</b>")
        for email, code in emails.items():
            lines.append(f"  {email} → <code>{code}</code>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

@admin_only
async def cmd_restore(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Восстановить коды из JSON: /restore <json>"""
    import payment_server
    if not ctx.args:
        await update.message.reply_text(
            "Использование: /restore &lt;json&gt;\n\n"
            "Пример: <code>/restore {\"lean-intro\":{\"user@mail.ru\":\"CODE-123\"}}</code>\n\n"
            "Текущие коды: /codes",
            parse_mode="HTML",
        )
        return
    try:
        json_str = " ".join(ctx.args)
        restored = json.loads(json_str)
        payment_server._write_codes_file(restored)
        total = sum(len(v) for v in restored.values())
        await update.message.reply_text(
            f"✅ Восстановлено <b>{total}</b> кодов доступа.",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка разбора JSON: {e}")

# ─── STARTUP: restore daily jobs ─────────────────────────────────────────────

async def post_init(app: Application):
    """Восстанавливаем ежедневные задания после перезапуска."""

    # Автовосстановление кодов из Telegram-бэкапа
    await _restore_codes_if_needed(app.bot)

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

    # Авто-пост с AI каждый день в AUTO_POST_TIME
    if GROQ_KEY:
        try:
            h, m = map(int, AUTO_POST_TIME.split(":"))
            app.job_queue.run_daily(
                callback=auto_ai_post,
                time=time(h, m),
                name="auto_ai_post",
            )
            log.info(f"✅ AI авто-пост запланирован на {AUTO_POST_TIME} ежедневно")
        except Exception as e:
            log.error(f"Ошибка планировщика авто-поста: {e}")

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
    app.add_handler(CommandHandler("unban",   cmd_unban))
    app.add_handler(CommandHandler("aipost",  cmd_aipost))
    app.add_handler(CommandHandler("codes",   cmd_codes))
    app.add_handler(CommandHandler("restore", cmd_restore))

    # Welcome new members
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))

    # Moderation (only if GROUP_ID is set)
    if GROUP_ID:
        app.add_handler(
            MessageHandler(filters.Chat(GROUP_ID) & filters.ALL, moderate)
        )

    # Запускаем сервер оплаты в отдельном потоке
    import payment_server
    payment_server.YOOKASSA_SECRET  = PAY_YK_SECRET
    payment_server.YOOKASSA_SHOP_ID = PAY_YK_SHOP_ID
    payment_server.GMAIL_USER       = PAY_GMAIL_USER
    payment_server.GMAIL_PASS       = PAY_GMAIL_PASS
    payment_server.YANDEX_API_KEY   = PAY_YA_API_KEY
    payment_server.YANDEX_FOLDER_ID = PAY_YA_FOLDER_ID
    payment_server.BOT_TOKEN_REF    = BOT_TOKEN
    payment_server.ADMIN_IDS_REF    = ADMIN_IDS
    from payment_server import app as flask_app
    port = int(os.environ.get("PORT", 8080))
    t = threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=port, use_reloader=False),
        daemon=True,
    )
    t.start()
    log.info(f"Payment server started on port {port}")

    log.info("Bot started. Listening...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
