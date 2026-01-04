import logging
import os
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TOKEN")
PORT = int(os.getenv("PORT", 8000))
WEBHOOK_HOST = f"https://{os.getenv('KOYEB_PUBLIC_DOMAIN')}"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

conn = sqlite3.connect("finance.db", check_same_thread=False)
cursor = conn.cursor()

# Таблицы
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT,
    category TEXT,
    amount REAL,
    description TEXT,
    date TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS debts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    debtor TEXT,  -- 'me' или имя
    amount REAL,
    description TEXT,
    date TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT,
    name TEXT UNIQUE
)
""")
conn.commit()

DEFAULT_INCOME = ["Зарплата 💰", "Фриланс 💻", "Подарок 🎁", "Инвестиции 📈"]
DEFAULT_EXPENSE = ["Еда 🍔", "Транспорт 🚗", "Развлечения 🎬", "Покупки 🛍️"]

def get_categories(user_id: int, typ: str):
    cursor.execute("SELECT name FROM categories WHERE user_id=? AND type=?", (user_id, typ))
    custom = [r[0] for r in cursor.fetchall()]
    return (DEFAULT_INCOME + custom) if typ == "income" else (DEFAULT_EXPENSE + custom)

class States(StatesGroup):
    income_expense_category = State()
    income_expense_amount = State()
    add_category_type = State()
    add_category_name = State()
    debt_type = State()
    debt_amount = State()

def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Доходы 💹"), KeyboardButton(text="Расходы 📉")],
        [KeyboardButton(text="Долги 🤝"), KeyboardButton(text="Баланс 💼")],
        [KeyboardButton(text="Статистика 📊"), KeyboardButton(text="Категории ➕")]
    ], resize_keyboard=True)

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 <b>Привет! Я твой финансовый бот</b>\n\n"
        "Используй кнопки ниже:\n"
        "• Добавляй доходы и расходы с категориями\n"
        "• Веди учёт долгов\n"
        "• Смотри баланс и статистику\n"
        "• Добавляй свои категории\n\n"
        "Готов? Жми кнопку ↓",
        reply_markup=main_kb(),
        parse_mode=ParseMode.HTML
    )

@dp.message(F.text.in_(["Доходы 💹", "Расходы 📉"]))
async def choose_category(message: Message, state: FSMContext):
    typ = "income" if "Доходы" in message.text else "expense"
    await state.update_data(type=typ)
    cats = get_categories(message.from_user.id, typ)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=c, callback_data=f"cat_{typ}_{c}") for c in cats[i:i+2]]
        for i in range(0, len(cats), 2)
    ] + [[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]])
    await message.answer(f"📂 Выбери категорию для <b>{message.text}</b>:", reply_markup=kb, parse_mode=ParseMode.HTML)
    await state.set_state(States.income_expense_category)

@dp.callback_query(F.data.startswith("cat_"))
async def category_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, typ, cat = callback.data.split("_", 2)
    await state.update_data(category=cat)
    await callback.message.edit_text(f"✅ Категория: <b>{cat}</b>\n\n💰 Введи сумму и описание (через пробел):\n<code>1500 Зарплата за январь</code>", parse_mode=ParseMode.HTML)
    await state.set_state(States.income_expense_amount)

@dp.message(States.income_expense_amount)
async def add_transaction(message: Message, state: FSMContext):
    try:
        parts = message.text.strip().split(maxsplit=1)
        amount = float(parts[0])
        desc = parts[1] if len(parts) > 1 else "Без описания"
        data = await state.get_data()
        typ = data["type"]
        cat = data["category"]
        sign = 1 if typ == "income" else -1
        cursor.execute(
            "INSERT INTO transactions (user_id, type, category, amount, description, date) VALUES (?, ?, ?, ?, ?, ?)",
            (message.from_user.id, typ, cat, sign * amount, desc, datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        conn.commit()
        await message.answer(f"🎉 <b>{'Доход' if typ=='income' else 'Расход'}</b> добавлен!\n💵 {amount} ₽ → {cat}\n📝 {desc}", parse_mode=ParseMode.HTML, reply_markup=main_kb())
    except:
        await message.answer("❌ Неверный формат. Пример: <code>1200 Такси</code>", parse_mode=ParseMode.HTML)
    await state.clear()

@dp.message(F.text == "Долги 🤝")
async def debt_start(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Я должен 📉", callback_data="debt_me")],
        [InlineKeyboardButton(text="Мне должны 💹", callback_data="debt_other")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])
    await message.answer("🤝 Выбери тип долга:", reply_markup=kb)
    await state.set_state(States.debt_type)

@dp.callback_query(F.data.in_(["debt_me", "debt_other"]))
async def debt_type_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    is_me = callback.data == "debt_me"
    await state.update_data(is_me=is_me)
    await callback.message.edit_text(f"💸 Введи сумму и описание:\n<code>5000 Другу на поездку</code>\n\n{'Я должен (-)' if is_me else 'Мне должны (+)'}", parse_mode=ParseMode.HTML)
    await state.set_state(States.debt_amount)

@dp.message(States.debt_amount)
async def add_debt(message: Message, state: FSMContext):
    try:
        parts = message.text.strip().split(maxsplit=1)
        amount = float(parts[0])
        desc = parts[1] if len(parts) > 1 else "Долг"
        data = await state.get_data()
        debtor = "me" if data["is_me"] else desc.split()[-1] if len(parts)>1 else "Кто-то"
        sign = -1 if data["is_me"] else 1
        cursor.execute(
            "INSERT INTO debts (user_id, debtor, amount, description, date) VALUES (?, ?, ?, ?, ?)",
            (message.from_user.id, debtor, sign * amount, desc, datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        conn.commit()
        await message.answer(f"🤝 Долг записан: {amount} ₽ ({'я должен' if data['is_me'] else 'мне должны'}) — {desc}", reply_markup=main_kb())
    except:
        await message.answer("❌ Формат: <code>3000 Васе на день рождения</code>", parse_mode=ParseMode.HTML)
    await state.clear()

@dp.message(F.text == "Категории ➕")
async def add_category_start(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Доходы 💹", callback_data="newcat_income")],
        [InlineKeyboardButton(text="Расходы 📉", callback_data="newcat_expense")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])
    await message.answer("➕ Для какого типа добавить категорию?", reply_markup=kb)
    await state.set_state(States.add_category_type)

@dp.callback_query(F.data.startswith("newcat_"))
async def add_category_type(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    typ = callback.data.split("_")[1]
    await state.update_data(cat_type=typ)
    await callback.message.edit_text(f"📝 Введи название новой категории для {'доходов' if typ=='income' else 'расходов'}:")
    await state.set_state(States.add_category_name)

@dp.message(States.add_category_name)
async def save_new_category(message: Message, state: FSMContext):
    name = message.text.strip()
    data = await state.get_data()
    typ = data["cat_type"]
    user_id = message.from_user.id
    try:
        cursor.execute("INSERT INTO categories (user_id, type, name) VALUES (?, ?, ?)", (user_id, typ, name))
        conn.commit()
        await message.answer(f"✅ Категория <b>{name}</b> добавлена в { 'доходы' if typ=='income' else 'расходы' }!", parse_mode=ParseMode.HTML, reply_markup=main_kb())
    except sqlite3.IntegrityError:
        await message.answer("❌ Такая категория уже есть!", reply_markup=main_kb())
    await state.clear()

@dp.message(F.text == "Баланс 💼")
async def show_balance(message: Message):
    uid = message.from_user.id
    cursor.execute("SELECT SUM(amount) FROM transactions WHERE user_id=?", (uid,))
    trans = cursor.fetchone()[0] or 0
    cursor.execute("SELECT SUM(amount) FROM debts WHERE user_id=?", (uid,))
    debt = cursor.fetchone()[0] or 0
    total = trans + debt
    await message.answer(
        f"💼 <b>Твой баланс</b>\n\n"
        f"📊 Транзакции: <b>{trans:+.2f} ₽</b>\n"
        f"🤝 Долги: <b>{debt:+.2f} ₽</b>\n"
        f"🌟 <b>Итого: {total:.2f} ₽</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_kb()
    )

@dp.message(F.text == "Статистика 📊")
async def show_stats(message: Message):
    uid = message.from_user.id
    cursor.execute("""
        SELECT strftime('%Y-%m', date) AS month,
               SUM(CASE WHEN type='income' THEN amount ELSE 0 END) AS inc,
               SUM(CASE WHEN type='expense' THEN -amount ELSE 0 END) AS exp
        FROM transactions WHERE user_id=? 
        GROUP BY month ORDER BY month DESC LIMIT 6
    """, (uid,))
    rows = cursor.fetchall()
    if not rows:
        await message.answer("📊 Нет данных для статистики. Добавь транзакции!", reply_markup=main_kb())
        return
    text = "📊 <b>Статистика за последние месяцы</b>\n\n"
    for month, inc, exp in rows:
        bal = inc - exp
        text += f"<code>{month}</code> │ +{inc:.0f} │ -{exp:.0f} │ <b>{bal:+.0f} ₽</b>\n"
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=main_kb())

@dp.callback_query(F.data == "cancel")
async def cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Отменено")
    await state.clear()
    await callback.message.edit_text("🏠 Главное меню:", reply_markup=main_kb())

async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook set to {WEBHOOK_URL}")

async def on_shutdown(app):
    await bot.delete_webhook()
    logging.info("Webhook deleted")

if __name__ == "__main__":
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, host="0.0.0.0", port=PORT)

