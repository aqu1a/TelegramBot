import logging
import os
import psycopg2  # Заменили sqlite3 на psycopg2 для PostgreSQL
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

# --------------------- PostgreSQL ---------------------
# Подключение к PostgreSQL через DATABASE_URL (настрой в Koyeb: добавь Serverless PostgreSQL и укажи переменную окружения DATABASE_URL)
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
conn.autocommit = True  # Для автоматического коммита, как в SQLite
cursor = conn.cursor()

# Создание таблиц (SQL совместим с PostgreSQL, добавили SERIAL для AUTOINCREMENT)
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    type TEXT,
    category TEXT,
    amount REAL,
    date TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS debts (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    debtor TEXT,
    amount REAL,
    description TEXT,
    date TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    type TEXT,
    name TEXT UNIQUE
)
""")

# --------------------- Категории ---------------------
# Оставили только базовые, пользователь добавляет свои через меню
DEFAULT_INCOME = [
    "Зарплата 💼", "Аванс 💰", "Премия 🎉", "Фриланс 💻",
    "Подарок 🎁", "Кэшбэк 💸", "Проценты по вкладу 📈", "Дивиденды 📊",
    "Продажа вещи 🛒", "Возврат долга 🤝", "Подработка ⚡", "Стипендия 📚",
    "Пенсия 👴", "Пособие 👶", "Алименты 👨‍👩‍👧"
]
DEFAULT_EXPENSE = [
    "Еда дома 🍳", "Кафе/рестораны 🍔", "Продукты 🛍️", "Алкоголь 🍷",
    "Транспорт 🚕", "Бензин ⛽", "Общественный транспорт 🚇", "Такси 🚖",
    "ЖКХ 🏠", "Интернет 📡", "Мобильная связь 📱", "Электричество ⚡",
    "Аренда жилья 🏡", "Одежда 👕", "Обувь 👟", "Косметика 💄",
    "Развлечения 🎬", "Кино 🎥", "Концерты 🎸", "Игры 🎮",
    "Подписки 📺", "Спорт/фитнес 🏋️", "Медицина 💊", "Лекарства 🏥",
    "Подарки 🎁", "Благотворительность ❤️", "Образование 📖", "Курсы 🎓",
    "Ремонт 🔧", "Бытовая техника 🧼", "Путешествия ✈️", "Отель 🏨"
]

def get_categories(user_id: int, typ: str):
    cursor.execute("SELECT name FROM categories WHERE user_id=%s AND type=%s", (user_id, typ))
    custom = [r[0] for r in cursor.fetchall()]
    return (DEFAULT_INCOME + custom) if typ == "income" else (DEFAULT_EXPENSE + custom)

# --------------------- Состояния ---------------------
class States(StatesGroup):
    choosing_category = State()
    entering_amount = State()
    adding_category_type = State()
    entering_category_name = State()
    choosing_debt_type = State()
    entering_debt_amount = State()
    entering_debtor_name = State()  # Новое: ввод имени должника/кредитора
    choosing_debt_to_pay = State()  # Для погашения
    entering_stats_month = State()  # Для выбора месяца в статистике
    confirming_clear = State()  # Для подтверждения очистки данных

# --------------------- Главное меню ---------------------
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Доходы 💹"), KeyboardButton(text="Расходы 📉")],
        [KeyboardButton(text="Долги 🤝"), KeyboardButton(text="Баланс 💼")],
        [KeyboardButton(text="Статистика 📊"), KeyboardButton(text="Категории ➕")],
        [KeyboardButton(text="Аннулировать данные 🗑️")]  # Новая кнопка
    ], resize_keyboard=True)

# --------------------- Старт ---------------------
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 <b>Привет! Я твой личный финансовый помощник</b>\n\n"
        "Здесь ты можешь:\n"
        "• Учитывать доходы и расходы\n"
        "• Вести долги\n"
        "• Видеть баланс и статистику\n"
        "• Добавлять свои категории\n\n"
        "Начнём? Выбери действие ниже ↓",
        parse_mode=ParseMode.HTML,
        reply_markup=main_kb()
    )

# --------------------- Доходы / Расходы ---------------------
@dp.message(F.text.in_(["Доходы 💹", "Расходы 📉"]))
async def choose_category(message: Message, state: FSMContext):
    typ = "income" if "Доходы" in message.text else "expense"
    await state.update_data(type=typ)
    cats = get_categories(message.from_user.id, typ)
    if not cats:
        await message.answer("📂 Нет категорий. Добавь через 'Категории ➕'.")
        return
    rows = [cats[i:i+2] for i in range(0, len(cats), 2)]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=c, callback_data=f"cat_{typ}_{c}") for c in row]
        for row in rows
    ] + [[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]])
    await message.answer(f"📂 Выбери категорию для <b>{message.text.lower()}</b>:", reply_markup=kb, parse_mode=ParseMode.HTML)
    await state.set_state(States.choosing_category)

@dp.callback_query(F.data.startswith("cat_"))
async def category_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, typ, cat = callback.data.split("_", 2)
    await state.update_data(category=cat)
    await callback.message.edit_text(
        f"✅ Категория: <b>{cat}</b>\n\n"
        f"💰 Теперь введи сумму (только число):\n<code>2500</code> или <code>499.50</code>",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(States.entering_amount)

@dp.message(States.entering_amount)
async def add_transaction(message: Message, state: FSMContext):
    text = message.text.strip().replace(",", ".")
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
        data = await state.get_data()
        typ = data["type"]
        cat = data["category"]
        cursor.execute(
            "INSERT INTO transactions (user_id, type, category, amount, date) VALUES (%s, %s, %s, %s, %s)",
            (message.from_user.id, typ, cat, amount, datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        emoji = "💹" if typ == "income" else "📉"
        await message.answer(
            f"{emoji} <b>{'Доход' if typ=='income' else 'Расход'}</b> добавлен!\n"
            f"💰 <b>{amount:.2f} сўм</b> → {cat}",
            parse_mode=ParseMode.HTML,
            reply_markup=main_kb()
        )
    except ValueError:
        await message.answer("❌ Введи корректную сумму (число > 0)", parse_mode=ParseMode.HTML)
        return
    await state.clear()

# --------------------- Долги ---------------------
@dp.message(F.text == "Долги 🤝")
async def debt_start(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Я должен 📉", callback_data="debt_me")],
        [InlineKeyboardButton(text="Мне должны 💹", callback_data="debt_other")],
        [InlineKeyboardButton(text="Погасить долг 💰", callback_data="pay_debt")],
        [InlineKeyboardButton(text="Возврат долга 🔄", callback_data="return_debt")],
        [InlineKeyboardButton(text="Информация о долгах ℹ️", callback_data="debt_info")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])
    await message.answer("🤝 Выбери действие с долгами:", reply_markup=kb)
    await state.set_state(States.choosing_debt_type)

@dp.callback_query(F.data.in_(["debt_me", "debt_other"]))
async def debt_type_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    is_me = callback.data == "debt_me"
    await state.update_data(is_me=is_me)
    await callback.message.edit_text(
        "👤 Введи имя должника/кредитора (например, 'Друг' или 'Банк'):",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(States.entering_debtor_name)

@dp.message(States.entering_debtor_name)
async def enter_debtor_name(message: Message, state: FSMContext):
    debtor = message.text.strip()
    if not debtor:
        await message.answer("❌ Имя не может быть пустым!")
        return
    await state.update_data(debtor=debtor)
    await message.answer(
        f"💸 Введи сумму долга (только число):\n<code>5000</code>\n\n"
        f"{'Я должен (-)' if (await state.get_data())['is_me'] else 'Мне должны (+)'}",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(States.entering_debt_amount)

@dp.message(States.entering_debt_amount)
async def add_debt(message: Message, state: FSMContext):
    text = message.text.strip().replace(",", ".")
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
        data = await state.get_data()
        sign = -1 if data["is_me"] else 1
        description = "Я должен" if data["is_me"] else "Мне должны"
        cursor.execute(
            "INSERT INTO debts (user_id, debtor, amount, description, date) VALUES (%s, %s, %s, %s, %s)",
            (message.from_user.id, data["debtor"], sign * amount, description, datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        await message.answer(
            f"🤝 Долг записан: <b>{amount:.2f} сўм</b> ({description}) - {data['debtor']}",
            parse_mode=ParseMode.HTML,
            reply_markup=main_kb()
        )
    except ValueError:
        await message.answer("❌ Введи корректную сумму (число > 0)", parse_mode=ParseMode.HTML)
        return
    await state.clear()

@dp.callback_query(F.data == "pay_debt")
async def pay_debt_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    uid = callback.from_user.id
    cursor.execute("SELECT id, debtor, amount, description, date FROM debts WHERE user_id=%s AND amount < 0", (uid,))
    rows = cursor.fetchall()
    if not rows:
        await callback.message.answer("ℹ️ Нет долгов, которые вы должны.", reply_markup=main_kb())
        await state.clear()
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{desc} {debtor} {amount:.2f} ({date})", callback_data=f"pay_{id}")]
        for id, debtor, amount, desc, date in rows
    ] + [[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]])
    await callback.message.edit_text("Выберите долг для погашения:", reply_markup=kb)
    await state.set_state(States.choosing_debt_to_pay)

@dp.callback_query(F.data == "return_debt")
async def return_debt_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    uid = callback.from_user.id
    cursor.execute("SELECT id, debtor, amount, description, date FROM debts WHERE user_id=%s AND amount > 0", (uid,))
    rows = cursor.fetchall()
    if not rows:
        await callback.message.answer("ℹ️ Нет долгов, которые вам должны.", reply_markup=main_kb())
        await state.clear()
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{desc} {debtor} {amount:.2f} ({date})", callback_data=f"return_{id}")]
        for id, debtor, amount, desc, date in rows
    ] + [[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]])
    await callback.message.edit_text("Выберите долг для возврата:", reply_markup=kb)
    await state.set_state(States.choosing_debt_to_pay)

@dp.callback_query(F.data.startswith(("pay_", "return_")))
async def process_debt_payment(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action, debt_id = callback.data.split("_")
    cursor.execute("DELETE FROM debts WHERE id=%s", (debt_id,))
    await callback.message.edit_text(f"✅ Долг {'погашен' if action == 'pay' else 'возвращён'}!", reply_markup=None)
    await callback.message.answer("Выбери действие:", reply_markup=main_kb())
    await state.clear()

@dp.callback_query(F.data == "debt_info")
async def debt_info(callback: CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    cursor.execute("SELECT debtor, amount, description, date FROM debts WHERE user_id=%s", (uid,))
    rows = cursor.fetchall()
    if not rows:
        await callback.message.answer("ℹ️ Долгов пока нет.", reply_markup=main_kb())
        return
    text = "ℹ️ <b>Информация о долгах:</b>\n\n"
    for debtor, amount, desc, date in rows:
        text += f"{desc} │ {amount:.2f} сўм │ {debtor} │ {date}\n"
    await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=main_kb())

# --------------------- Категории ---------------------
@dp.message(F.text == "Категории ➕")
async def add_category_start(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Доходы 💹", callback_data="newcat_income")],
        [InlineKeyboardButton(text="Расходы 📉", callback_data="newcat_expense")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])
    await message.answer("➕ Для какого типа добавить категорию?", reply_markup=kb)
    await state.set_state(States.adding_category_type)

@dp.callback_query(F.data.startswith("newcat_"))
async def add_category_type(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    typ = callback.data.split("_")[1]
    await state.update_data(cat_type=typ)
    await callback.message.edit_text("📝 Введи название новой категории (без эмодзи):")
    await state.set_state(States.entering_category_name)

@dp.message(States.entering_category_name)
async def save_new_category(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("❌ Название не может быть пустым!")
        return
    data = await state.get_data()
    typ = data["cat_type"]
    user_id = message.from_user.id
    try:
        cursor.execute("INSERT INTO categories (user_id, type, name) VALUES (%s, %s, %s)", (user_id, typ, name))
        await message.answer(f"✅ Категория <b>{name}</b> добавлена!", parse_mode=ParseMode.HTML, reply_markup=main_kb())
    except psycopg2.errors.UniqueViolation:
        await message.answer("❌ Такая категория уже существует!", reply_markup=main_kb())
    await state.clear()

# --------------------- Баланс ---------------------
@dp.message(F.text == "Баланс 💼")
async def show_balance(message: Message):
    uid = message.from_user.id
    cursor.execute("SELECT SUM(CASE WHEN type='income' THEN amount ELSE 0 END) FROM transactions WHERE user_id=%s", (uid,))
    income = cursor.fetchone()[0] or 0
    cursor.execute("SELECT SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) FROM transactions WHERE user_id=%s", (uid,))
    expense = cursor.fetchone()[0] or 0
    cursor.execute("SELECT SUM(amount) FROM debts WHERE user_id=%s", (uid,))
    debt = cursor.fetchone()[0] or 0
    balance = income - expense
    await message.answer(
        f"💼 <b>Твой текущий баланс</b>\n\n"
        f"📊 Доходы: <b>{income:.2f} сўм</b>\n"
        f"📉 Расходы: <b>{expense:.2f} сўм</b>\n"
        f"🤝 Долги: <b>{debt:.2f} сўм</b>\n"
        f"🌟 <b>Баланс (Доходы − Расходы): {balance:.2f} сўм</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_kb()
    )

# --------------------- Статистика ---------------------
@dp.message(F.text == "Статистика 📊")
async def show_stats_start(message: Message, state: FSMContext):
    await message.answer(
        "📊 Введи месяц для статистики в формате YYYY-MM (например, 2026-01).\n"
        "Или введи 'all' для последних 6 месяцев.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]])
    )
    await state.set_state(States.entering_stats_month)

@dp.message(States.entering_stats_month)
async def show_stats(message: Message, state: FSMContext):
    month_input = message.text.strip()
    uid = message.from_user.id
    if month_input.lower() == 'all':
        cursor.execute("""
            SELECT strftime('%Y-%m', date) AS month,
                   SUM(CASE WHEN type='income' THEN amount ELSE 0 END) AS inc,
                   SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) AS exp
            FROM transactions
            WHERE user_id=%s
            GROUP BY month
            ORDER BY month DESC
            LIMIT 6
        """, (uid,))
        trans_rows = cursor.fetchall()
        cursor.execute("""
            SELECT strftime('%Y-%m', date) AS month, SUM(amount)
            FROM debts WHERE user_id=%s
            GROUP BY month
        """, (uid,))
        debts_rows = {r[0]: r[1] for r in cursor.fetchall()}
        if not trans_rows:
            await message.answer("📊 Пока нет данных.", reply_markup=main_kb())
            await state.clear()
            return
        text = "📊 <b>Статистика за последние месяцы</b>\n\n"
        for month, inc, exp in trans_rows:
            debt = debts_rows.get(month, 0) or 0
            bal = inc - exp
            text += f"<code>{month}</code> │ Доход: {inc:.0f} │ Расход: {exp:.0f} │ Долги: {debt:.0f} │ <b>Баланс: {bal:.0f}</b>\n"
    else:
        try:
            datetime.strptime(month_input, "%Y-%m")
            cursor.execute("""
                SELECT SUM(CASE WHEN type='income' THEN amount ELSE 0 END) AS inc,
                       SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) AS exp
                FROM transactions
                WHERE user_id=%s AND strftime('%Y-%m', date) = %s
            """, (uid, month_input))
            inc, exp = cursor.fetchone()
            inc = inc or 0
            exp = exp or 0
            cursor.execute("SELECT SUM(amount) FROM debts WHERE user_id=%s AND strftime('%Y-%m', date) = %s", (uid, month_input))
            debt = cursor.fetchone()[0] or 0
            bal = inc - exp
            text = f"📊 <b>Статистика за {month_input}</b>\n\n"
            text += f"Доход: {inc:.0f} │ Расход: {exp:.0f} │ Долги: {debt:.0f} │ <b>Баланс: {bal:.0f}</b>\n"
        except ValueError:
            await message.answer("❌ Некорректный формат. Пример: 2026-01")
            return
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=main_kb())
    await state.clear()

# --------------------- Аннулирование данных ---------------------
@dp.message(F.text == "Аннулировать данные 🗑️")
async def clear_data_start(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да, очистить всё", callback_data="confirm_clear")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])
    await message.answer("🗑️ Вы уверены, что хотите аннулировать все данные (баланс, статистика, долги, категории)?", reply_markup=kb)
    await state.set_state(States.confirming_clear)

@dp.callback_query(F.data == "confirm_clear")
async def clear_data_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    uid = callback.from_user.id
    cursor.execute("DELETE FROM transactions WHERE user_id=%s", (uid,))
    cursor.execute("DELETE FROM debts WHERE user_id=%s", (uid,))
    cursor.execute("DELETE FROM categories WHERE user_id=%s", (uid,))
    await callback.message.edit_text("🗑️ Все данные аннулированы!", reply_markup=None)
    await callback.message.answer("Выбери действие:", reply_markup=main_kb())
    await state.clear()

# --------------------- Отмена ---------------------
@dp.callback_query(F.data == "cancel")
async def cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Отменено")
    await state.clear()
    await callback.message.edit_text("🏠 Главное меню:", reply_markup=None)
    await callback.message.answer("Выбери действие:", reply_markup=main_kb())

# --------------------- Неизвестные сообщения ---------------------
@dp.message()
async def unknown_message(message: Message):
    await message.answer("❓ Не понял. Используй кнопки ниже или команду /start", reply_markup=main_kb())

# --------------------- Webhook ---------------------
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
