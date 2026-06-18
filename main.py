"""
Конвертер валют
===============
Простое веб-приложение для конвертации валют на FastAPI + SQLite.
Запуск: python main.py
Открыть: http://127.0.0.1:8000
"""

import sqlite3
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Form, HTTPException, Query
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import uvicorn
import requests
import xml.etree.ElementTree as ET
import random

app = FastAPI(title="💱 Конвертер валют", version="1.0.0")

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

DB_NAME = "converter.db"

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS currencies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        sign TEXT NOT NULL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS exchange_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        base_code TEXT NOT NULL,
        target_code TEXT NOT NULL,
        rate REAL NOT NULL,
        UNIQUE(base_code, target_code))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_code TEXT NOT NULL,
        to_code TEXT NOT NULL,
        amount REAL NOT NULL,
        result REAL NOT NULL,
        rate REAL NOT NULL,
        date TEXT NOT NULL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS rate_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        currency_code TEXT NOT NULL,
        rate REAL NOT NULL,
        date TEXT NOT NULL)""")
    cur.execute("SELECT COUNT(*) FROM currencies")
    if cur.fetchone()[0] == 0:
        currencies = [
            ("RUB", "Российский рубль", "₽"),
            ("USD", "Доллар США", "$"),
            ("EUR", "Евро", "€"),
            ("GBP", "Фунт стерлингов", "£"),
            ("CNY", "Китайский юань", "¥"),
            ("JPY", "Японская иена", "¥"),
            ("KZT", "Казахстанский тенге", "₸"),
            ("TRY", "Турецкая лира", "₺"),
        ]
        cur.executemany("INSERT INTO currencies (code, name, sign) VALUES (?, ?, ?)", currencies)
    conn.commit()
    conn.close()

init_database()

def update_rates_from_cbr():
    """Загружает актуальные курсы с API ЦБ РФ (RUB -> валюта)."""
    try:
        url = "https://www.cbr.ru/scripts/XML_daily.asp"
        response = requests.get(url, timeout=5)
        response.encoding = 'windows-1251'
        root = ET.fromstring(response.text)
        conn = get_db()
        cur = conn.cursor()
        for valute in root.findall("Valute"):
            char_code = valute.find("CharCode").text
            if char_code not in ["USD", "EUR", "GBP", "CNY", "JPY", "KZT", "TRY"]:
                continue
            value = float(valute.find("Value").text.replace(",", "."))
            nominal = int(valute.find("Nominal").text)
            rub_per_unit = value / nominal
            rate = 1.0 / rub_per_unit  # сколько валюты в 1 RUB
            cur.execute("INSERT OR REPLACE INTO exchange_rates (base_code, target_code, rate) VALUES (?, ?, ?)",
                        ("RUB", char_code, rate))
        conn.commit()
        conn.close()
        print("✅ Курсы обновлены с ЦБ РФ")
    except Exception as e:
        print(f"⚠️ Ошибка ЦБ: {e}. Использую резервные курсы.")
        _load_fallback_rates()

def _load_fallback_rates():
    conn = get_db()
    cur = conn.cursor()
    fallback = [("RUB", "USD", 0.0109), ("RUB", "EUR", 0.0100), ("RUB", "GBP", 0.0085),
                ("RUB", "CNY", 0.079), ("RUB", "JPY", 1.64), ("RUB", "KZT", 5.00), ("RUB", "TRY", 0.35)]
    for base, target, rate in fallback:
        cur.execute("INSERT OR REPLACE INTO exchange_rates (base_code, target_code, rate) VALUES (?, ?, ?)",
                    (base, target, rate))
    conn.commit()
    conn.close()

def generate_history_rates():
    """Генерирует историю курсов (RUB за 1 единицу валюты) за 365 дней, удаляя старые данные."""
    conn = get_db()
    cur = conn.cursor()
    # Удаляем старые данные, чтобы график всегда строился заново и соответствовал текущему курсу
    cur.execute("DELETE FROM rate_history")

    # Получаем текущие курсы: RUB -> валюта (сколько валюты за 1 RUB)
    cur.execute("SELECT target_code, rate FROM exchange_rates WHERE base_code = 'RUB'")
    current_rates = {row["target_code"]: row["rate"] for row in cur.fetchall()}
    if not current_rates:
        current_rates = {"USD": 0.0109, "EUR": 0.0100, "GBP": 0.0085,
                         "CNY": 0.079, "JPY": 1.64, "KZT": 5.00, "TRY": 0.35}

    random.seed(42)  # для воспроизводимости
    today = datetime.now().date()
    for days_ago in range(365, -1, -1):
        date = today - timedelta(days=days_ago)
        date_str = date.strftime("%Y-%m-%d")
        for code, rate_to_rub in current_rates.items():
            # вариация ±2% относительно текущего курса
            variation = 1 + random.uniform(-0.02, 0.02)
            historical_rate = rate_to_rub * variation
            # переводим в "сколько рублей за 1 единицу валюты"
            rub_per_unit = 1.0 / historical_rate
            cur.execute("INSERT INTO rate_history (currency_code, rate, date) VALUES (?, ?, ?)",
                        (code, rub_per_unit, date_str))
    conn.commit()
    conn.close()
    print("📊 Сгенерирована история курсов за 365 дней")

update_rates_from_cbr()
generate_history_rates()

def get_all_currencies():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM currencies ORDER BY code")
    result = [dict(row) for row in cur.fetchall()]
    conn.close()
    return result

def find_exchange_rate(from_code, to_code):
    conn = get_db()
    cur = conn.cursor()
    # прямой
    cur.execute("SELECT rate FROM exchange_rates WHERE base_code=? AND target_code=?", (from_code, to_code))
    row = cur.fetchone()
    if row:
        conn.close()
        return row["rate"]
    # обратный
    cur.execute("SELECT rate FROM exchange_rates WHERE base_code=? AND target_code=?", (to_code, from_code))
    row = cur.fetchone()
    if row:
        conn.close()
        return 1.0 / row["rate"]
    # кросс через RUB
    conn.close()
    r1 = _rate_via_rub(from_code)
    r2 = _rate_via_rub(to_code)
    if r1 and r2:
        return r2 / r1
    return None

def _rate_via_rub(code):
    if code == "RUB":
        return 1.0
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT rate FROM exchange_rates WHERE base_code='RUB' AND target_code=?", (code,))
    row = cur.fetchone()
    conn.close()
    return row["rate"] if row else None

def save_to_history(from_code, to_code, amount, result, rate):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO history (from_code, to_code, amount, result, rate, date) VALUES (?,?,?,?,?,?)",
                (from_code, to_code, amount, result, rate, datetime.now().strftime("%d.%m.%Y %H:%M")))
    conn.commit()
    conn.close()

def get_history(limit=10):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,))
    result = [dict(row) for row in cur.fetchall()]
    conn.close()
    return result

@app.get("/api/rate_history")
def api_rate_history(currency: str = Query(...), days: int = Query(30)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""SELECT date, rate FROM rate_history
                   WHERE currency_code = ? AND date >= date('now', ?)
                   ORDER BY date ASC""",
                (currency.upper(), f'-{days} days'))
    rows = cur.fetchall()
    conn.close()
    return [{"date": row["date"], "rate": row["rate"]} for row in rows]

@app.get("/")
def home(request: Request):
    currencies = get_all_currencies()
    history = get_history()
    return templates.TemplateResponse("index.html", {"request": request, "currencies": currencies, "history": history, "result": None})

@app.post("/convert")
def convert_currency(request: Request, from_currency: str = Form(...), to_currency: str = Form(...), amount: float = Form(...)):
    currencies = get_all_currencies()
    history = get_history()
    if amount <= 0:
        return templates.TemplateResponse("index.html", {"request": request, "currencies": currencies, "history": history, "error": "Сумма должна быть больше нуля"})
    rate = find_exchange_rate(from_currency, to_currency)
    if rate is None:
        return templates.TemplateResponse("index.html", {"request": request, "currencies": currencies, "history": history, "error": f"Курс {from_currency} → {to_currency} не найден"})
    result = round(amount * rate, 2)
    save_to_history(from_currency, to_currency, amount, result, rate)
    history = get_history()
    return templates.TemplateResponse("index.html", {"request": request, "currencies": currencies, "history": history, "result": {"from_code": from_currency, "to_code": to_currency, "amount": amount, "converted": result, "rate": rate}})

if __name__ == "__main__":
    print("=" * 50)
    print("💱 Конвертер валют запущен!")
    print("Открой в браузере: http://127.0.0.1:8000")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=8000)
