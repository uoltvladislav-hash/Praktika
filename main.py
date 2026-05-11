"""
Конвертер валют
===============
Простое веб-приложение для конвертации валют на FastAPI + SQLite.
Запуск: python main.py
Открыть: http://127.0.0.1:8000
API документ: http://127.0.0.1:8000/docs
"""

import sqlite3
from datetime import datetime
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import uvicorn

# ============= НАСТРОЙКА ПРИЛОЖЕНИЯ =============
app = FastAPI(title="💱 Конвертер валют", version="1.0.0")

# Папка с HTML-шаблонами
templates = Jinja2Templates(directory="templates")

# Папка с CSS, картинками и другими статическими файлами
app.mount("/static", StaticFiles(directory="static"), name="static")

# ============= БАЗА ДАННЫХ =============
# SQLite — простая база данных в одном файле
DB_NAME = "converter.db"


def get_db():
    """Подключение к базе данных"""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row  # Чтобы можно было обращаться по имени столбца
    return conn


def init_database():
    """Создание таблиц, если их ещё нет + заполнение тестовыми данными"""
    conn = get_db()
    cur = conn.cursor()

    # Таблица валют (хранит код, название и знак валюты)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS currencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,   -- Например: USD
            name TEXT NOT NULL,          -- Например: Доллар США
            sign TEXT NOT NULL           -- Например: $
        )
    """)

    # Таблица курсов обмена
    cur.execute("""
        CREATE TABLE IF NOT EXISTS exchange_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            base_code TEXT NOT NULL,      -- Исходная валюта
            target_code TEXT NOT NULL,    -- Целевая валюта
            rate REAL NOT NULL,           -- Курс: 1 base = ? target
            UNIQUE(base_code, target_code)
        )
    """)

    # Таблица истории конвертаций
    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_code TEXT NOT NULL,
            to_code TEXT NOT NULL,
            amount REAL NOT NULL,
            result REAL NOT NULL,
            rate REAL NOT NULL,
            date TEXT NOT NULL
        )
    """)

    # Если таблица валют пустая — заполняем
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
        cur.executemany(
            "INSERT INTO currencies (code, name, sign) VALUES (?, ?, ?)",
            currencies
        )

        # Добавляем курсы относительно RUB
        rates = [
            ("RUB", "USD", 0.011),
            ("RUB", "EUR", 0.010),
            ("RUB", "GBP", 0.0086),
            ("RUB", "CNY", 0.079),
            ("RUB", "JPY", 1.72),
            ("RUB", "KZT", 4.95),
            ("RUB", "TRY", 0.35),
        ]
        cur.executemany(
            "INSERT INTO exchange_rates (base_code, target_code, rate) VALUES (?, ?, ?)",
            rates
        )

    conn.commit()
    conn.close()


# Инициализируем БД при запуске
init_database()


# ============= ФУНКЦИИ ДЛЯ РАБОТЫ С ВАЛЮТАМИ =============

def get_all_currencies():
    """Получить список всех валют из БД"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM currencies ORDER BY code")
    result = [dict(row) for row in cur.fetchall()]
    conn.close()
    return result


def find_exchange_rate(from_code: str, to_code: str):
    """
    Найти курс обмена между двумя валютами.
    Сначала ищем прямой курс, потом обратный, потом через RUB.
    """
    conn = get_db()
    cur = conn.cursor()

    # 1. Прямой курс: from -> to
    cur.execute(
        "SELECT rate FROM exchange_rates WHERE base_code = ? AND target_code = ?",
        (from_code, to_code)
    )
    row = cur.fetchone()
    if row:
        conn.close()
        return row["rate"]

    # 2. Обратный курс: to -> from (переворачиваем)
    cur.execute(
        "SELECT rate FROM exchange_rates WHERE base_code = ? AND target_code = ?",
        (to_code, from_code)
    )
    row = cur.fetchone()
    if row:
        conn.close()
        return 1.0 / row["rate"]

    # 3. Кросс-курс через RUB (если одна из валют — RUB)
    conn.close()
    rate_from = get_rate_via_rub(from_code)
    rate_to = get_rate_via_rub(to_code)
    if rate_from and rate_to:
        return rate_to / rate_from

    return None


def get_rate_via_rub(code: str):
    """Получить курс валюты к RUB"""
    if code == "RUB":
        return 1.0

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT rate FROM exchange_rates WHERE base_code = 'RUB' AND target_code = ?",
        (code,)
    )
    row = cur.fetchone()
    conn.close()
    return row["rate"] if row else None


def save_to_history(from_code, to_code, amount, result, rate):
    """Сохранить конвертацию в историю"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO history (from_code, to_code, amount, result, rate, date) VALUES (?, ?, ?, ?, ?, ?)",
        (from_code, to_code, amount, result, rate, datetime.now().strftime("%d.%m.%Y %H:%M"))
    )
    conn.commit()
    conn.close()


def get_history(limit=10):
    """Получить последние конвертации из истории"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,))
    result = [dict(row) for row in cur.fetchall()]
    conn.close()
    return result


# ============= ВЕБ-СТРАНИЦЫ =============

@app.get("/")
def home(request: Request):
    """Главная страница с формой конвертации"""
    currencies = get_all_currencies()
    history = get_history()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "currencies": currencies,
        "history": history,
        "result": None
    })


@app.post("/convert")
def convert_currency(
    request: Request,
    from_currency: str = Form(...),
    to_currency: str = Form(...),
    amount: float = Form(...)
):
    """Обработка формы конвертации"""
    currencies = get_all_currencies()
    history = get_history()

    # Проверка: сумма должна быть положительной
    if amount <= 0:
        return templates.TemplateResponse("index.html", {
            "request": request,
            "currencies": currencies,
            "history": history,
            "error": "Сумма должна быть больше нуля"
        })

    # Ищем курс
    rate = find_exchange_rate(from_currency, to_currency)
    if rate is None:
        return templates.TemplateResponse("index.html", {
            "request": request,
            "currencies": currencies,
            "history": history,
            "error": f"Курс {from_currency} → {to_currency} не найден"
        })

    # Считаем результат
    result = round(amount * rate, 2)

    # Сохраняем в историю
    save_to_history(from_currency, to_currency, amount, result, rate)

    # Обновляем историю
    history = get_history()

    return templates.TemplateResponse("index.html", {
        "request": request,
        "currencies": currencies,
        "history": history,
        "result": {
            "from_code": from_currency,
            "to_code": to_currency,
            "amount": amount,
            "converted": result,
            "rate": rate
        }
    })


# ============= REST API =============

@app.get("/api/currencies")
def api_currencies():
    """GET /api/currencies — список валют (JSON)"""
    return get_all_currencies()


@app.get("/api/exchange")
def api_exchange(from_: str, to: str, amount: float):
    """
    GET /api/exchange?from=USD&to=RUB&amount=100
    Конвертация через API (возвращает JSON)
    """
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть положительной")

    rate = find_exchange_rate(from_.upper(), to.upper())
    if rate is None:
        raise HTTPException(status_code=404, detail="Курс не найден")

    result = round(amount * rate, 2)
    return {
        "from": from_.upper(),
        "to": to.upper(),
        "amount": amount,
        "result": result,
        "rate": rate
    }


# ============= ЗАПУСК =============
if __name__ == "__main__":
    print("=" * 50)
    print("💱 Конвертер валют запущен!")
    print("Открой в браузере: http://127.0.0.1:8000")
    print("API документация:   http://127.0.0.1:8000/docs")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=8000)