import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qsl

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pixora.telegram")
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DATABASE_PATH", str(ROOT / "pixora.db"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "12")) * 1024 * 1024
FREE_JOBS = 2

app = FastAPI(title="PIXORA AI", version="0.1.0")


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                free_used INTEGER NOT NULL DEFAULT 0,
                subscription_until INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                operation TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)


init_db()


def telegram_user_id(init_data: str) -> int:
    token = os.getenv("BOT_TOKEN", "")
    if not token:
        raise HTTPException(503, "Сервис ещё не настроен: отсутствует BOT_TOKEN")
    if not init_data:
        raise HTTPException(401, "Откройте PIXORA AI через Telegram")

    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "Некорректные данные Telegram")

    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError:
        raise HTTPException(401, "Некорректная дата авторизации")
    now = int(time.time())
    if auth_date > now + 60 or now - auth_date > 86400:
        raise HTTPException(401, "Сессия Telegram устарела")

    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise HTTPException(401, "Не удалось проверить авторизацию Telegram")

    try:
        return int(json.loads(fields["user"])["id"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(401, "Не найден пользователь Telegram")


def ensure_user(telegram_id: int):
    now = int(time.time())
    with db() as con:
        con.execute(
            "INSERT OR IGNORE INTO users(telegram_id, created_at) VALUES (?, ?)",
            (telegram_id, now),
        )
        return con.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()


def record_job(telegram_id: int, mode: str, operation: str) -> tuple[int, int]:
    now = int(time.time())
    with db() as con:
        con.execute(
            "INSERT OR IGNORE INTO users(telegram_id, created_at) VALUES (?, ?)",
            (telegram_id, now),
        )
        user = con.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        if user["subscription_until"] <= now and user["free_used"] >= FREE_JOBS:
            raise HTTPException(402, "Бесплатные обработки закончились")
        if user["subscription_until"] <= now:
            con.execute(
                "UPDATE users SET free_used = free_used + 1 WHERE telegram_id = ?",
                (telegram_id,),
            )
        cursor = con.execute(
            "INSERT INTO jobs(telegram_id, mode, operation, created_at) VALUES (?, ?, ?, ?)",
            (telegram_id, mode, operation, now),
        )
        updated = con.execute(
            "SELECT free_used FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        return cursor.lastrowid, max(0, FREE_JOBS - updated["free_used"])


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "name": "PIXORA AI", "version": "0.1.0"}


@app.get("/api/me")
def me(x_telegram_init_data: str = Header(default="")):
    telegram_id = telegram_user_id(x_telegram_init_data)
    user = ensure_user(telegram_id)
    now = int(time.time())
    return {
        "free_remaining": max(0, FREE_JOBS - user["free_used"]),
        "subscription_active": user["subscription_until"] > now,
        "subscription_until": user["subscription_until"],
    }


@app.post("/api/process")
async def process_photo(
    file: UploadFile = File(...),
    mode: str = Form(...),
    operation: str = Form("enhance"),
    x_telegram_init_data: str = Header(default=""),
):
    telegram_id = telegram_user_id(x_telegram_init_data)
    if mode not in {"photo", "product"}:
        raise HTTPException(400, "Неизвестный режим")
    if mode == "product":
        raise HTTPException(
            501,
            "Генератор карточек товара ещё подключается. Бесплатная обработка не списана.",
        )
    if operation not in {"enhance", "warm", "bw"}:
        raise HTTPException(400, "Неизвестная операция")

    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if not raw:
        raise HTTPException(400, "Файл пустой")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Файл превышает допустимый размер")

    try:
        image = ImageOps.exif_transpose(Image.open(BytesIO(raw))).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(400, "Не удалось прочитать изображение")

    if operation == "enhance":
        image = ImageOps.autocontrast(image)
        image = ImageEnhance.Color(image).enhance(1.04)
        image = ImageEnhance.Sharpness(image).enhance(1.15)
    elif operation == "warm":
        image = ImageEnhance.Color(image).enhance(1.12)
    elif operation == "bw":
        image = ImageOps.grayscale(image).convert("RGB")

    output = BytesIO()
    image.save(output, format="JPEG", quality=92, optimize=True)
    job_id, free_remaining = record_job(telegram_id, mode, operation)
    return {
        "job_id": job_id,
        "image_base64": base64.b64encode(output.getvalue()).decode("ascii"),
        "free_remaining": free_remaining,
    }


@app.get("/api/plans")
def plans():
    return {
        "plans": [
            {"months": 1, "rub": 50},
            {"months": 3, "rub": 100},
            {"months": 6, "rub": 200},
        ],
        "payment_status": "not_configured",
    }


@app.post("/api/telegram/webhook")
async def telegram_webhook(
    update: dict,
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    expected_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    token = os.getenv("BOT_TOKEN", "")
    webapp_url = os.getenv("WEBAPP_URL", "")
    if not expected_secret or not hmac.compare_digest(x_telegram_bot_api_secret_token, expected_secret):
        logger.warning("Webhook rejected: secret missing or invalid")
        raise HTTPException(403, "Forbidden")
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()
    logger.info("Telegram update received: message=%s chat_id_present=%s", bool(message), bool(chat_id))
    if not token or not webapp_url:
        logger.error("Cannot reply: BOT_TOKEN_present=%s WEBAPP_URL_present=%s", bool(token), bool(webapp_url))
        raise HTTPException(503, "Bot is not configured")
    if not chat_id or not text.startswith("/start"):
        return {"ok": True}
    import httpx
    payload = {
        "chat_id": chat_id,
        "text": "Добро пожаловать в PIXORA AI! Нажми кнопку ниже, чтобы открыть фотостудию.",
        "reply_markup": {"inline_keyboard": [[{
            "text": "Открыть PIXORA AI", "web_app": {"url": webapp_url}
        }]]}
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
    if response.status_code >= 400:
        logger.error("Telegram sendMessage failed: status=%s body=%s", response.status_code, response.text[:1000])
        raise HTTPException(502, "Telegram API request failed")
    logger.info("Telegram /start reply sent successfully")
    return {"ok": True}


@app.on_event("startup")
async def register_telegram_webhook():
    token = os.getenv("BOT_TOKEN", "")
    webapp_url = os.getenv("WEBAPP_URL", "")
    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    logger.info(
        "Telegram webhook setup: BOT_TOKEN_present=%s WEBAPP_URL_present=%s WEBHOOK_SECRET_present=%s",
        bool(token), bool(webapp_url), bool(webhook_secret),
    )
    if not token or not webapp_url or not webhook_secret:
        logger.error("Telegram webhook not registered: one or more required environment variables are missing")
        return
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/setWebhook",
                json={
                    "url": f"{webapp_url.rstrip('/')}/api/telegram/webhook",
                    "secret_token": webhook_secret,
                    "allowed_updates": ["message"],
                },
            )
        logger.info("Telegram setWebhook response: status=%s body=%s", response.status_code, response.text[:1000])
        response.raise_for_status()
    except Exception:
        logger.exception("Telegram webhook registration failed")
