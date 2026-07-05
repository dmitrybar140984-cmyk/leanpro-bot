"""
LeanPro Payment Server
- /verify      — проверка email+код (вызывается из auth.js)
- /grant       — ручная выдача доступа (admin)
- /webhook/yookassa — webhook от ЮКассы после оплаты
- /health      — статус сервера
"""

import os
import json
import logging
import random
import string
import smtplib
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import threading
import requests
from flask import Flask, request, jsonify

log = logging.getLogger(__name__)
app = Flask(__name__)

# ─── CONFIG (значения передаются из bot.py) ───────────────────────────────────
YOOKASSA_SHOP_ID = ""
YOOKASSA_SECRET  = ""
SMTP_USER        = ""
SMTP_PASSWORD    = ""
SMTP_HOST        = "smtp.yandex.ru"
SMTP_PORT        = 587
ADMIN_TOKEN      = "leanpro-admin-2025"

# BOT_TOKEN и ADMIN_IDS передаются из bot.py для Telegram-уведомлений
BOT_TOKEN_REF    = ""
ADMIN_IDS_REF    = []

CODES_FILE = Path("codes.json")

COURSE_NAMES = {
    "lean-intro":      "Введение в Lean",
    "5s":              "Система 5S на производстве",
    "vsm":             "Картирование потока создания ценности",
    "lean-flow":       "Lean Flow: производственный поток",
    "lean-leader":     "Lean-лидер: полный курс",
    "six-sigma":       "Six Sigma Green Belt",
    "kaizen":          "Кайдзен и непрерывное улучшение",
    "ladm":            "LADM: Архитектура производственных линий",
    "standard-times":  "Стандартные времена в Lean",
    "corporate":       "Lean-трансформация предприятия",
}

COURSE_PRICES = {
    "lean-intro":      "9900.00",
    "5s":              "14900.00",
    "vsm":             "19900.00",
    "lean-flow":       "19900.00",
    "lean-leader":     "59900.00",
    "six-sigma":       "59900.00",
    "kaizen":          "59900.00",
    "ladm":            "69900.00",
    "standard-times":  "19900.00",
    "corporate":       "1000000.00",
}

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def load_codes() -> dict:
    if CODES_FILE.exists():
        try:
            return json.loads(CODES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

BACKUP_MARKER = "🔐LEANPRO_BACKUP\n"

def _write_codes_file(codes: dict):
    """Write codes to local file only — no Telegram backup."""
    CODES_FILE.write_text(json.dumps(codes, ensure_ascii=False, indent=2), encoding="utf-8")

def save_codes(codes: dict):
    """Write codes to file and send Telegram backup."""
    _write_codes_file(codes)
    backup_codes_to_telegram(codes)

def backup_codes_to_telegram(codes: dict):
    """Pin a full-JSON backup of codes in admin's private Telegram chat."""
    if not BOT_TOKEN_REF or not ADMIN_IDS_REF:
        return
    admin_id = ADMIN_IDS_REF[0]
    text = BACKUP_MARKER + json.dumps(codes, ensure_ascii=False)
    if len(text) > 4000:
        log.warning("Backup JSON too large for Telegram message — skipping backup")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN_REF}/sendMessage",
            json={"chat_id": admin_id, "text": text},
            timeout=10,
        )
        data = r.json()
        if data.get("ok"):
            msg_id = data["result"]["message_id"]
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN_REF}/pinChatMessage",
                json={"chat_id": admin_id, "message_id": msg_id, "disable_notification": True},
                timeout=10,
            )
            total = sum(len(v) for v in codes.values())
            log.info(f"Telegram backup saved (msg_id={msg_id}, entries={total})")
        else:
            log.warning(f"Telegram backup sendMessage failed: {data}")
    except Exception as e:
        log.error(f"Telegram backup error: {e}")

def generate_code(email: str, course_id: str) -> str:
    initials = email.split("@")[0][:4].upper()
    course_short = course_id.replace("-", "").upper()[:4]
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"{initials}-{course_short}-{suffix}"

def grant_access(email: str, course_id: str, code: str):
    codes = load_codes()
    if course_id not in codes:
        codes[course_id] = {}
    codes[course_id][email.strip().lower()] = code.strip().upper()
    save_codes(codes)
    log.info(f"Access granted: {email} → {course_id} [{code}]")

def notify_admin(text: str):
    """Отправляет уведомление администратору через Telegram."""
    if not BOT_TOKEN_REF or not ADMIN_IDS_REF:
        return
    for admin_id in ADMIN_IDS_REF:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN_REF}/sendMessage",
                json={"chat_id": admin_id, "text": text, "parse_mode": "HTML"},
                timeout=5,
            )
        except Exception as e:
            log.error(f"Telegram notify error: {e}")

def send_contact_email(name: str, phone: str, email: str, course: str, message: str):
    """Отправляет заявку с сайта на email администратора."""
    if not SMTP_USER or not SMTP_PASSWORD:
        log.warning("SMTP не настроен — уведомление не отправлено")
        return
    admin_email = "dmitry_bar@mail.ru"
    msg = MIMEMultipart("alternative")
    subject_course = f" — {course}" if course else ""
    msg["Subject"] = f"Новая заявка с сайта: {name}{subject_course}"
    msg["From"]    = SMTP_USER
    msg["To"]      = admin_email
    message_block = (
        f"<div style='margin-top:16px;padding:16px;background:#fff;"
        f"border-radius:6px;border:1px solid #e2e8f0'>{message}</div>"
        if message else ""
    )
    html = f"""
<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;color:#1e293b">
  <div style="background:#0f4c81;padding:24px 32px;border-radius:8px 8px 0 0">
    <h1 style="color:#fff;margin:0;font-size:22px">&#x2B21; LeanPro — Новая заявка</h1>
  </div>
  <div style="background:#f8fafc;padding:32px;border-radius:0 0 8px 8px;border:1px solid #e2e8f0">
    <table style="width:100%;border-collapse:collapse">
      <tr><td style="padding:8px 0;color:#64748b;width:120px">Имя</td>
          <td style="padding:8px 0;font-weight:bold">{name}</td></tr>
      <tr><td style="padding:8px 0;color:#64748b">Телефон</td>
          <td style="padding:8px 0">{phone or '—'}</td></tr>
      <tr><td style="padding:8px 0;color:#64748b">Email</td>
          <td style="padding:8px 0"><a href="mailto:{email}">{email or '—'}</a></td></tr>
      <tr><td style="padding:8px 0;color:#64748b">Курс</td>
          <td style="padding:8px 0">{course or '—'}</td></tr>
    </table>
    {message_block}
  </div>
</div>"""
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, admin_email, msg.as_string())
    log.info(f"Contact email: {name} ({email})")

def send_email(to_email: str, course_id: str, code: str):
    """Отправляет письмо с кодом доступа покупателю."""
    if not SMTP_USER or not SMTP_PASSWORD:
        log.warning("SMTP не настроен — письмо не отправлено")
        return
    course_name = COURSE_NAMES.get(course_id, course_id)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Ваш доступ к курсу «{course_name}» — LeanPro"
    msg["From"]    = SMTP_USER
    msg["To"]      = to_email
    html = f"""
<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;color:#1e293b">
  <div style="background:#0f4c81;padding:24px 32px;border-radius:8px 8px 0 0">
    <h1 style="color:#fff;margin:0;font-size:22px">⬡ LeanPro</h1>
  </div>
  <div style="background:#f8fafc;padding:32px;border-radius:0 0 8px 8px;border:1px solid #e2e8f0">
    <h2 style="margin-top:0">Доступ к курсу открыт!</h2>
    <p>Вы приобрели курс <strong>«{course_name}»</strong>.</p>
    <ul style="line-height:2">
      <li><strong>Email:</strong> {to_email}</li>
      <li><strong>Код доступа:</strong>
        <span style="background:#0f4c81;color:#fff;padding:4px 12px;border-radius:4px;
                     font-family:monospace;font-size:16px;letter-spacing:2px">{code}</span>
      </li>
    </ul>
    <a href="https://leanprorus.ru" style="display:inline-block;margin-top:16px;
       background:#0f4c81;color:#fff;padding:12px 28px;border-radius:6px;
       text-decoration:none;font-weight:bold">Перейти к курсу →</a>
    <p style="margin-top:32px;font-size:13px;color:#64748b">
      Код привязан к вашему email.<br>
      Вопросы: <a href="mailto:dmitry_bar@mail.ru">dmitry_bar@mail.ru</a>
    </p>
  </div>
</div>"""
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, to_email, msg.as_string())
    log.info(f"Email отправлен: {to_email}")

# ─── ROUTES ──────────────────────────────────────────────────────────────────

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

@app.route("/health", methods=["GET"])
def health():
    codes = load_codes()
    total = sum(len(v) for v in codes.values())
    return jsonify({"status": "ok", "total_codes": total,
                    "smtp": "set" if SMTP_USER else "not set",
                    "telegram": "set" if BOT_TOKEN_REF else "not set"})

@app.route("/verify", methods=["POST", "OPTIONS"])
def verify():
    """Проверяет email+код для входа в курс."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    body      = request.get_json(force=True)
    email     = body.get("email", "").strip().lower()
    code      = body.get("code", "").strip().upper()
    course_id = body.get("course_id", "").strip()

    # Администраторский вход
    if email == "dmitry_bar@mail.ru" and code == "LP2025ADMIN":
        return jsonify({"ok": True, "admin": True,
                        "courses": list(COURSE_NAMES.keys())})

    if not email or not code or not course_id:
        return jsonify({"ok": False, "error": "missing fields"}), 400

    codes = load_codes()
    expected = codes.get(course_id, {}).get(email)
    if expected and expected == code:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "invalid credentials"}), 401

@app.route("/grant", methods=["POST"])
def manual_grant():
    """Ручная выдача доступа."""
    auth = request.headers.get("X-Admin-Token", "").strip()
    if auth != ADMIN_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    body      = request.get_json(force=True)
    email     = body.get("email", "").strip().lower()
    course_id = body.get("course_id", "").strip()
    if not email or not course_id:
        return jsonify({"error": "email and course_id required"}), 400
    code = generate_code(email, course_id)
    grant_access(email, course_id, code)
    try:
        send_email(email, course_id, code)
    except Exception as e:
        log.error(f"Email error: {e}")
    notify_admin(
        f"✅ <b>Доступ выдан вручную</b>\n"
        f"📧 {email}\n📚 {COURSE_NAMES.get(course_id, course_id)}\n🔑 <code>{code}</code>"
    )
    return jsonify({"status": "ok", "code": code})

@app.route("/webhook/yookassa", methods=["POST"])
def yookassa_webhook():
    """Webhook от ЮКассы после успешной оплаты."""
    try:
        data    = request.get_json(force=True)
        event   = data.get("event", "")
        payment = data.get("object", {})
        if event != "payment.succeeded" or payment.get("status") != "succeeded":
            return jsonify({"status": "ignored"}), 200
        email = (payment.get("receipt", {}).get("customer", {}).get("email")
                 or payment.get("metadata", {}).get("email"))
        course_id = payment.get("metadata", {}).get("course_id", "")
        if not email or not course_id:
            return jsonify({"error": "missing email or course_id"}), 400
        code = generate_code(email, course_id)
        grant_access(email, course_id, code)
        try:
            send_email(email, course_id, code)
        except Exception as e:
            log.error(f"Email error: {e}")
        notify_admin(
            f"🎉 <b>Новая оплата!</b>\n"
            f"📧 {email}\n📚 {COURSE_NAMES.get(course_id, course_id)}\n🔑 <code>{code}</code>"
        )
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        log.exception(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/test-smtp")
def test_smtp():
    """Тест SMTP — только для отладки, вызвать вручную."""
    if not SMTP_USER or not SMTP_PASSWORD:
        return jsonify({"error": "SMTP not configured"}), 500
    try:
        msg = MIMEText("Тест SMTP с Railway. Если письмо дошло — всё работает.", "plain", "utf-8")
        msg["Subject"] = "LeanPro SMTP test"
        msg["From"]    = SMTP_USER
        msg["To"]      = "dmitry_bar@mail.ru"
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, "dmitry_bar@mail.ru", msg.as_string())
        return jsonify({"status": "ok", "message": "Test email sent"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/contact", methods=["POST", "OPTIONS"])
def contact():
    """Заявка с формы обратной связи → email + Telegram администратору."""
    if request.method == "OPTIONS":
        return jsonify({}), 200
    try:
        body    = request.get_json(force=True)
        name    = body.get("name", "").strip()
        phone   = body.get("phone", "").strip()
        email   = body.get("email", "").strip()
        course  = body.get("course", "").strip()
        message = body.get("message", "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        def _send_mail():
            try:
                send_contact_email(name, phone, email, course, message)
            except Exception as e:
                log.error(f"Contact email error: {e}")
                notify_admin(f"⚠️ <b>Ошибка отправки email</b> (заявка от {name}):\n<code>{e}</code>")
        threading.Thread(target=_send_mail, daemon=True).start()
        notify_admin(
            f"📩 <b>Новая заявка с сайта</b>\n"
            f"👤 {name}\n"
            f"📞 {phone or '—'}\n"
            f"📧 {email or '—'}\n"
            f"📚 {course or '—'}\n"
            f"💬 {message or '—'}"
        )
        return jsonify({"status": "ok"})
    except Exception as e:
        log.error(f"contact error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/create-payment", methods=["POST"])
def create_payment():
    """Создаёт платёж в ЮКассе."""
    try:
        from yookassa import Configuration, Payment
        import uuid
        if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET:
            return jsonify({"error": "YooKassa not configured"}), 500
        Configuration.account_id = YOOKASSA_SHOP_ID
        Configuration.secret_key  = YOOKASSA_SECRET
        body      = request.get_json(force=True)
        email     = body.get("email", "").strip().lower()
        course_id = body.get("course_id", "").strip()
        if not email or course_id not in COURSE_PRICES:
            return jsonify({"error": "invalid request"}), 400
        amount      = COURSE_PRICES[course_id]
        course_name = COURSE_NAMES.get(course_id, course_id)
        payment = Payment.create({
            "amount": {"value": amount, "currency": "RUB"},
            "confirmation": {"type": "redirect",
                             "return_url": f"https://leanprorus.ru/thank-you.html?course={course_id}"},
            "capture": True,
            "description": f"Курс «{course_name}»",
            "receipt": {"customer": {"email": email},
                        "items": [{"description": course_name, "quantity": "1",
                                   "amount": {"value": amount, "currency": "RUB"},
                                   "vat_code": 1, "payment_mode": "full_payment",
                                   "payment_subject": "service"}]},
            "metadata": {"email": email, "course_id": course_id},
        }, str(uuid.uuid4()))
        return jsonify({"confirmation_url": payment.confirmation.confirmation_url})
    except Exception as e:
        log.error(f"create-payment error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
