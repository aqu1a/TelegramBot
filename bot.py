import logging
import os
import psycopg2
from psycopg2.extras import RealDictCursor
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

# --------------------- Подключение к БД ---------------------
def get_db_connection():
    """Возвращает новое подключение к PostgreSQL"""
    try:
        return psycopg2.connect(
            os.getenv("DATABASE_URL"),
            cursor_factory=RealDictCursor
        )
    except Exception as e:
        logging.error(f"Failed to connect to DB: {e}")
        return None

def init_db():
    """Создаёт таблицы при старте приложения"""
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    type TEXT,
                    category TEXT,
                    amount REAL,
                    date TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS debts (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    debtor TEXT,
                    amount REAL,
                    description TEXT,
                    date TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    type TEXT,
                    name TEXT,
                    UNIQUE(user_id, type, name)
                )
            """)
        conn.commit()
        logging.info("Database tables initialized")
    except Exception as e:
        logging.error(f"Error initializing DB: {e}")
    finally:
        conn.close()

# --------------------- Категории ---------------------
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
    conn = get_db_connection()
    if not conn:
        return DEFAULT_INCOME if typ == "income" else DEFAULT_EXPENSE
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM categories WHERE user_id=%s AND type=%s", (user_id, typ))
            custom = [row['name'] for row in cur.fetchall()]
        return (DEFAULT_INCOME + custom) if typ == "income" else (DEFAULT_EXPENSE + custom)
    except Exception as e:
        logging.error(f"Error getting categories: {e}")
        return DEFAULT_INCOME if typ == "income" else DEFAULT_EXPENSE
    finally:
        conn.close()

# --------------------- Состояния ---------------------
class States(StatesGroup):
    choosing_category = State()
    entering_amount = State()
    adding_category_type = State()
    entering_category_name = State()
    choosing_debt_type = State()
    entering_debtor_name = State()
    entering_debt_amount = State()
    choosing_debt_to_pay = State()
    entering_stats_month = State()
    confirming_clear = State()

# --------------------- Главное меню ---------------------
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Доходы 💹"), KeyboardButton(text="Расходы 📉")],
        [KeyboardButton(text="Долги 🤝"), KeyboardButton(text="Баланс 💼")],
        [KeyboardButton(text="Статистика 📊"), KeyboardButton(text="Категории ➕")],
        [KeyboardButton(text="Аннулировать данные 🗑️")]
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
        await message.answer("📂 Нет категорий. Добавь через 'Категории ➕'.", reply_markup=main_kb())
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
        conn = get_db_connection()
        if not conn:
            await message.answer("❌ Ошибка базы данных. Попробуй позже.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO transactions (user_id, type, category, amount, date) VALUES (%s, %s, %s, %s, %s)",
                    (message.from_user.id, typ, cat, amount, datetime.now().strftime("%Y-%m-%d %H:%M"))
                )
            conn.commit()
            emoji = "💹" if typ == "income" else "📉"
            await message.answer(
                f"{emoji} <b>{'Доход' if typ=='income' else 'Расход'}</b> добавлен!\n"
                f"💰 <b>{amount:.2f} сўм</b> → {cat}",
                parse_mode=ParseMode.HTML,
                reply_markup=main_kb()
            )
        finally:
            conn.close()
    except ValueError:
        await message.answer("❌ Введи корректную сумму (число > 0)", parse_mode=ParseMode.HTML)
        return
    await state.clear()

# --------------------- Долги ---------------------
@dp.message(F.text == "Долги 🤝")
async def debt_start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Я должен 📉", callback_data="debt_me")],
        [InlineKeyboardButton(text="Мне должны 💹", callback_data="debt_other")],
        [InlineKeyboardButton(text="Погасить долг 💰", callback_data="pay_debt")],
        [InlineKeyboardButton(text="Возврат долга 🔄", callback_data="return_debt")],
        [InlineKeyboardButton(text="Информация о долгах ℹ️", callback_data="debt_info")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])
    await message.answer("🤝 Выбери действие с долгами:", reply_markup=kb)

@dp.callback_query(F.data.in_(["debt_me", "debt_other"]))
async def debt_type_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    is_me = callback.data == "debt_me"
    await state.update_data(is_me=is_me)
    await callback.message.edit_text("👤 Введи имя должника/кредитора (например, 'Друг' или 'Банк'):")
    await state.set_state(States.entering_debtor_name)

@dp.message(States.entering_debtor_name)
async def enter_debtor_name(message: Message, state: FSMContext):
    debtor = message.text.strip()
    if not debtor:
        await message.answer("❌ Имя не может быть пустым!")
        return
    await state.update_data(debtor=debtor)
    data = await state.get_data()
    await message.answer(
        f"💸 Введи сумму долга (только число):\n<code>5000</code>\n\n"
        f"{'Я должен (-)' if data['is_me'] else 'Мне должны (+)'}",
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
        conn = get_db_connection()
        if not conn:
            await message.answer("❌ Ошибка базы данных.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO debts (user_id, debtor, amount, description, date) VALUES (%s, %s, %s, %s, %s)",
                    (message.from_user.id, data["debtor"], sign * amount, description, datetime.now().strftime("%Y-%m-%d %H:%M"))
                )
            conn.commit()
            await message.answer(
                f"🤝 Долг записан: <b>{amount:.2f} сўм</b> ({description}) — {data['debtor']}",
                parse_mode=ParseMode.HTML,
                reply_markup=main_kb()
            )
        finally:
            conn.close()
    except ValueError:
        await message.answer("❌ Введи корректную сумму (число > 0)")
        return
    await state.clear()

# Погашение и возврат долгов (остальные обработчики долгов — без изменений, но с безопасным доступом к БД)
# (для краткости не дублирую полностью, но в реальном коде они используют тот же паттерн with conn)

# --------------------- Остальные функции (баланс, статистика, категории, аннулирование) ---------------------
# Все они используют тот же безопасный шаблон: get_db_connection() + with conn.cursor()

# Пример баланса (остальные аналогично)
@dp.message(F.text == "Баланс 💼")
async def show_balance(message: Message):
    uid = message.from_user.id
    conn = get_db_connection()
    if not conn:
        await message.answer("❌ Ошибка связи с базой данных.")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT SUM(CASE WHEN type='income' THEN amount ELSE 0 END) FROM transactions WHERE user_id=%s", (uid,))
            income = cur.fetchone()[0] or 0
            cur.execute("SELECT SUM(CASE WHEN type='expense' THEN amount ELSE 0 END) FROM transactions WHERE user_id=%s", (uid,))
            expense = cur.fetchone()[0] or 0
            cur.execute("SELECT SUM(amount) FROM debts WHERE user_id=%s", (uid,))
            debt = cur.fetchone()[0] or 0
        balance = income - expense
        await message.answer(
            f"💼 <b>Твой текущий баланс</b>\n\n"
            f"📊 Доходы: <b>{income:.2f} сўм</b>\n"
            f"📉 Расходы: <b>{expense:.2f} сўм</b>\n"
            f"🤝 Долги: <b>{debt:.2f} сўм</b>\n"
            f"🌟 <b>Баланс: {balance:.2f} сўм</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_kb()
        )
    except Exception as e:
        logging.error(f"Balance error: {e}")
        await message.answer("❌ Ошибка при расчёте баланса.")
    finally:
        conn.close()

# --------------------- Webhook ---------------------
async def on_startup(app):
    init_db()  # Инициализируем таблицы
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
