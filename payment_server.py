"""
LeanPro Payment Server
Принимает webhook от ЮКассы, генерирует уникальный код доступа,
обновляет credentials.js через GitHub API, отправляет email покупателю.
"""

import os
import json
import hmac
import base64
import hashlib
import logging
import random
import string
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import uuid
import requests
from flask import Flask, request, jsonify
from yookassa import Configuration, Payment

log = logging.getLogger(__name__)

app = Flask(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────────────────

YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET  = os.environ.get("YOOKASSA_SECRET", "")
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO      = os.environ.get("GITHUB_REPO", "dmitrybar140984-cmyk/lean-site")
SMTP_USER        = os.environ.get("SMTP_USER", "")        # ваш @yandex.ru
SMTP_PASSWORD    = os.environ.get("SMTP_PASSWORD", "")    # пароль приложения
SMTP_HOST        = os.environ.get("SMTP_HOST", "smtp.yandex.ru")
SMTP_PORT        = int(os.environ.get("SMTP_PORT", "465"))

CREDENTIALS_PATH = "lean-site/credentials.js"

if YOOKASSA_SHOP_ID and YOOKASSA_SECRET:
    Configuration.account_id = YOOKASSA_SHOP_ID
    Configuration.secret_key = YOOKASSA_SECRET

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

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def generate_code(email: str, course_id: str) -> str:
    """Генерирует уникальный код вида IVAN-5S-A7X2."""
    initials = email.split("@")[0][:4].upper()
    course_short = course_id.replace("-", "").upper()[:4]
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"{initials}-{course_short}-{suffix}"


def get_credentials_file() -> tuple[str, str]:
    """Получает текущий credentials.js из GitHub. Возвращает (content, sha)."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{CREDENTIALS_PATH}"
    resp = requests.get(url, headers={"Authorization": f"token {GITHUB_TOKEN}"})
    resp.raise_for_status()
    data = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data["sha"]


def add_code_to_credentials(email: str, course_id: str, code: str):
    """Добавляет email+код в credentials.js через GitHub API."""
    content, sha = get_credentials_file()

    # Ищем нужный курс и вставляем строку после открывающей скобки
    marker = f"  '{course_id}': {{"
    if marker not in content:
        raise ValueError(f"Курс '{course_id}' не найден в credentials.js")

    new_line = f"\n    '{email}': '{code}',"
    content = content.replace(marker, marker + new_line, 1)

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{CREDENTIALS_PATH}"
    payload = {
        "message": f"Access granted: {email} → {course_id}",
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "sha": sha,
    }
    resp = requests.put(
        url,
        headers={"Authorization": f"token {GITHUB_TOKEN}"},
        json=payload,
    )
    resp.raise_for_status()
    log.info(f"credentials.js обновлён: {email} → {course_id}")


def send_email(to_email: str, course_id: str, code: str):
    """Отправляет письмо с кодом доступа покупателю."""
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
    <p>Для входа перейдите на сайт и введите:</p>
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
      Код доступа привязан к вашему email и не передаётся другим лицам.<br>
      По вопросам: <a href="mailto:dmitry_bar@mail.ru">dmitry_bar@mail.ru</a>
    </p>
  </div>
</div>
"""
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, to_email, msg.as_string())

    log.info(f"Email отправлен: {to_email} ({course_name})")


# ─── ROUTES ──────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/create-payment", methods=["POST"])
def create_payment():
    """Создаёт платёж в ЮКассе и возвращает URL для оплаты."""
    body      = request.get_json(force=True)
    email     = body.get("email", "").strip().lower()
    course_id = body.get("course_id", "").strip()

    if not email or not course_id:
        return jsonify({"error": "email and course_id required"}), 400
    if course_id not in COURSE_PRICES:
        return jsonify({"error": "unknown course"}), 400

    amount      = COURSE_PRICES[course_id]
    course_name = COURSE_NAMES.get(course_id, course_id)

    payment = Payment.create({
        "amount": {"value": amount, "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": f"https://leanprorus.ru/thank-you.html?course={course_id}",
        },
        "capture": True,
        "description": f"Курс «{course_name}»",
        "receipt": {
            "customer": {"email": email},
            "items": [{
                "description": course_name,
                "quantity": "1",
                "amount": {"value": amount, "currency": "RUB"},
                "vat_code": 1,
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }],
        },
        "metadata": {"email": email, "course_id": course_id},
    }, str(uuid.uuid4()))

    return jsonify({"confirmation_url": payment.confirmation.confirmation_url})


@app.route("/webhook/yookassa", methods=["POST"])
def yookassa_webhook():
    """Принимает уведомление об успешной оплате от ЮКассы."""
    try:
        data = request.get_json(force=True)
        log.info(f"Webhook received: {json.dumps(data)[:200]}")

        event = data.get("event", "")
        if event != "payment.succeeded":
            return jsonify({"status": "ignored"}), 200

        payment = data.get("object", {})
        status  = payment.get("status", "")
        if status != "succeeded":
            return jsonify({"status": "ignored"}), 200

        # Email покупателя
        email = (
            payment.get("receipt", {}).get("customer", {}).get("email")
            or payment.get("metadata", {}).get("email")
        )
        if not email:
            log.error("Email покупателя не найден в webhook")
            return jsonify({"error": "no email"}), 400

        # ID курса из метаданных платежа
        course_id = payment.get("metadata", {}).get("course_id", "")
        if not course_id:
            log.error("course_id не найден в metadata платежа")
            return jsonify({"error": "no course_id"}), 400

        # Генерируем код и сохраняем
        code = generate_code(email, course_id)
        add_code_to_credentials(email, course_id, code)
        send_email(email, course_id, code)

        log.info(f"✅ Доступ выдан: {email} → {course_id} [{code}]")
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        log.exception(f"Ошибка обработки webhook: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/grant", methods=["POST"])
def manual_grant():
    """Ручная выдача доступа (для тестов и ручных заказов)."""
    auth = request.headers.get("X-Admin-Token", "").strip()
    expected = YOOKASSA_SECRET.strip()
    log.info(f"Grant auth check: received={repr(auth[:10])}... expected={repr(expected[:10])}...")
    if auth != expected:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(force=True)
    email     = body.get("email", "").strip().lower()
    course_id = body.get("course_id", "").strip()

    if not email or not course_id:
        return jsonify({"error": "email and course_id required"}), 400

    code = generate_code(email, course_id)
    add_code_to_credentials(email, course_id, code)
    send_email(email, course_id, code)

    return jsonify({"status": "ok", "code": code}), 200


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
