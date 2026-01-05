import logging
import os
from datetime import datetime, timedelta
import psycopg
from psycopg.rows import dict_row
from psycopg.errors import UniqueViolation

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
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(level=logging.INFO)

# Переменные окружения
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
KOYEB_PUBLIC_DOMAIN = os.getenv("KOYEB_PUBLIC_DOMAIN")

if not TOKEN or not DATABASE_URL or not KOYEB_PUBLIC_DOMAIN:
    logging.error("Не установлены обязательные переменные: TOKEN, DATABASE_URL, KOYEB_PUBLIC_DOMAIN")
    exit(1)

PORT = int(os.getenv("PORT", 8000))
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{KOYEB_PUBLIC_DOMAIN}{WEBHOOK_PATH}"

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# --------------------- БД ---------------------
def get_db_connection():
    try:
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    except Exception as e:
        logging.error(f"DB connection error: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    type TEXT NOT NULL,
                    category TEXT NOT NULL,
                    amount REAL NOT NULL,
                    date TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS debts (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    debtor TEXT NOT NULL,
                    amount REAL NOT NULL,
                    description TEXT NOT NULL,
                    date TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    UNIQUE(user_id, type, name)
                )
            """)
        conn.commit()
        logging.info("DB initialized")
    except Exception as e:
        logging.error(f"DB init error: {e}")
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
            custom = [row["name"] for row in cur.fetchall()]
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
    choosing_stats_type = State()
    choosing_stats_period = State()
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
        reply_markup=main_kb()
    )

# ===================== Доходы / Расходы =====================

@dp.message(F.text.in_(["Доходы 💹", "Расходы 📉"]))
async def choose_category(message: Message, state: FSMContext):
    typ = "income" if "Доходы" in message.text else "expense"
    await state.update_data(type=typ)
    cats = get_categories(message.from_user.id, typ)

    kb = InlineKeyboardBuilder()
    for c in cats:
        kb.button(text=c, callback_data=f"cat|{typ}|{c}")
    kb.adjust(2)

    await message.answer("Выберите категорию:", reply_markup=kb.as_markup())
    await state.set_state(States.choosing_category)

@dp.callback_query(F.data.startswith("cat|"))
async def category_selected(cb: CallbackQuery, state: FSMContext):
    _, typ, cat = cb.data.split("|", 2)
    await state.update_data(type=typ, category=cat)
    await cb.message.edit_text(f"Введите сумму для <b>{cat}</b>:")
    await state.set_state(States.entering_amount)

@dp.message(States.entering_amount)
async def save_transaction(message: Message, state: FSMContext):
    try:
        amount = Decimal(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except:
        await message.answer("Введите корректную сумму")
        return

    data = await state.get_data()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO transactions (user_id, type, category, amount)
                VALUES (%s, %s, %s, %s)
            """, (message.from_user.id, data["type"], data["category"], amount))
        conn.commit()

    await message.answer("Операция сохранена", reply_markup=main_kb())
    await state.clear()

# --------------------- Долги ---------------------
@dp.message(F.text == "🤝 Долги")
async def debts_menu(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Я дал в долг", callback_data="debt_give")],
        [InlineKeyboardButton(text="➖ Я должен", callback_data="debt_take")],
        [InlineKeyboardButton(text="📋 Список долгов", callback_data="debt_list")],
        [InlineKeyboardButton(text="✅ Погасить долг", callback_data="debt_pay")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])
    await state.clear()
    await message.answer("🤝 Управление долгами:", reply_markup=kb)

@dp.callback_query(F.data.in_(["debt_give", "debt_take"]))
async def choose_debt_type(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    debt_type = 1 if callback.data == "debt_give" else -1
    await state.update_data(debt_type=debt_type)

    await callback.message.edit_text("Введите имя человека или описание долга:")
    await state.set_state(States.entering_debtor_name)

@dp.message(States.entering_debtor_name)
async def enter_debtor_name(message: Message, state: FSMContext):
    await state.update_data(debtor_name=message.text)
    await message.answer("Введите сумму долга:")
    await state.set_state(States.entering_debt_amount)

@dp.message(States.entering_debt_amount)
async def enter_debt_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите корректную сумму числом.")
        return

    data = await state.get_data()
    signed_amount = amount * data["debt_type"]

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO debts (user_id, debtor, amount, description, date)
                VALUES (%s, %s, %s, %s, NOW())
            """, (
                message.from_user.id,
                data["debtor_name"],
                signed_amount,
                data["debtor_name"]
            ))
            conn.commit()
    finally:
        conn.close()

    await message.answer("✅ Долг сохранён.", reply_markup=main_kb())
    await state.clear()

@dp.callback_query(F.data == "debt_list")
async def list_debts(callback: CallbackQuery):
    await callback.answer()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, debtor, amount
                FROM debts
                WHERE user_id=%s
                ORDER BY date DESC
            """, (callback.from_user.id,))
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        await callback.message.edit_text("📭 У вас нет активных долгов.")
        return

    text = "📋 <b>Ваши долги:</b>\n\n"
    for r in rows:
        sign = "➕" if r["amount"] > 0 else "➖"
        text += f"{sign} {r['debtor']}: {r['amount']:.2f}\n"

    await callback.message.edit_text(text)

@dp.callback_query(F.data == "debt_pay")
async def choose_debt_to_pay(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, debtor, amount
                FROM debts
                WHERE user_id=%s
            """, (callback.from_user.id,))
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        await callback.message.edit_text("Нет долгов для погашения.")
        return

    kb = InlineKeyboardMarkup()
    for r in rows:
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{r['debtor']} ({r['amount']:+.2f})",
                callback_data=f"debt_done_{r['id']}"
            )
        ])

    kb.inline_keyboard.append(
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    )

    await callback.message.edit_text(
        "Выберите долг для погашения:",
        reply_markup=kb
    )
    await state.set_state(States.choosing_debt_to_pay)

@dp.callback_query(F.data.startswith("debt_done_"))
async def pay_debt(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    debt_id = int(callback.data.split("_")[-1])

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM debts WHERE id=%s AND user_id=%s",
                (debt_id, callback.from_user.id)
            )
            conn.commit()
    finally:
        conn.close()

    await callback.message.edit_text("✅ Долг погашен.")
    await callback.message.answer("Выберите действие:", reply_markup=main_kb())
    await state.clear()

# --------------------- Категории ---------------------
@dp.message(F.text == "Категории ➕")
async def add_category_start(message: Message, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.button(text="Доходы 💹", callback_data="newcat_income")
    builder.button(text="Расходы 📉", callback_data="newcat_expense")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(1)
    await message.answer("➕ Для какого типа добавить категорию?", reply_markup=builder.as_markup())
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
    conn = get_db_connection()
    if not conn:
        await message.answer("❌ Ошибка базы данных.")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO categories (user_id, type, name) VALUES (%s, %s, %s)", (user_id, typ, name))
        conn.commit()
        await message.answer(f"✅ Категория <b>{name}</b> добавлена!", reply_markup=main_kb())
    except UniqueViolation:
        await message.answer("❌ Такая категория уже существует!", reply_markup=main_kb())
    except Exception as e:
        logging.error(f"Category add error: {e}")
        await message.answer("❌ Ошибка при добавлении категории.")
    finally:
        conn.close()
    await state.clear()

# ===================== Баланс =====================

@dp.message(F.text == "Баланс 💼")
async def balance(message: Message):
    uid = message.from_user.id
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                  COALESCE(SUM(CASE WHEN type='income' THEN amount END),0) AS inc,
                  COALESCE(SUM(CASE WHEN type='expense' THEN amount END),0) AS exp
                FROM transactions WHERE user_id=%s
            """, (uid,))
            row = cur.fetchone()

            cur.execute("SELECT COALESCE(SUM(amount),0) AS debt FROM debts WHERE user_id=%s", (uid,))
            debt = cur.fetchone()["debt"]

    cash_balance = row["inc"] - row["exp"]
    full_balance = cash_balance + debt

    await message.answer(
        f"💼 <b>Баланс</b>\n\n"
        f"💹 Доходы: {row['inc']:.2f}\n"
        f"📉 Расходы: {row['exp']:.2f}\n"
        f"💰 Денежный баланс: {cash_balance:.2f}\n"
        f"🤝 Долги (нетто): {debt:+.2f}\n"
        f"⭐ С учётом долгов: {full_balance:.2f}",
        reply_markup=main_kb()
    )
    except Exception as e:
        logging.error(f"Balance error: {e}", exc_info=True)
        await message.answer("❌ Ошибка при расчёте баланса. Попробуй позже.")
    finally:
        conn.close()

# ================= СТАТИСТИКА =================

@dp.message(F.text == "Статистика 📊")
async def stats_start(message: Message, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.button(text="Общая", callback_data="stats_general")
    kb.button(text="Подробная", callback_data="stats_detailed")
    kb.adjust(1)
    await message.answer("Выберите тип статистики:", reply_markup=kb.as_markup())
    await state.set_state(States.choosing_stats_type)

@dp.callback_query(F.data.startswith("stats_"))
async def choose_period(cb: CallbackQuery, state: FSMContext):
    await state.update_data(stats_type=cb.data.split("_")[1])
    kb = InlineKeyboardBuilder()
    kb.button(text="Текущий месяц", callback_data="period_month")
    kb.button(text="Прошлый месяц", callback_data="period_prev_month")
    kb.button(text="Год", callback_data="period_year")
    kb.button(text="Все время", callback_data="period_all")
    kb.adjust(2)
    await cb.message.edit_text("Выберите период:", reply_markup=kb.as_markup())
    await state.set_state(States.choosing_stats_period)

def get_period(period: str):
    now = datetime.now()
    if period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0)
        end = now
    elif period == "prev_month":
        first = now.replace(day=1)
        end = first
        start = (first - timedelta(days=1)).replace(day=1)
    elif period == "year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0)
        end = now
    else:
        start = datetime(2000, 1, 1)
        end = now
    return start, end

@dp.callback_query(F.data.startswith("period_"))
async def show_stats(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    stats_type = data["stats_type"]
    period_key = cb.data.split("_")[1]
    start, end = get_period(period_key)
    uid = cb.from_user.id

    with db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
              COALESCE(SUM(CASE WHEN type='income' THEN amount END),0) AS inc,
              COALESCE(SUM(CASE WHEN type='expense' THEN amount END),0) AS exp
            FROM transactions
            WHERE user_id=%s AND date BETWEEN %s AND %s
        """, (uid, start, end))
        totals = cur.fetchone()

        cur.execute("""
            SELECT COALESCE(SUM(amount),0) AS debt
            FROM debts WHERE user_id=%s
        """, (uid,))
        debt = cur.fetchone()["debt"]

        text = (
            f"📊 <b>Статистика</b>\n\n"
            f"💹 Доходы: {totals['inc']:.2f}\n"
            f"📉 Расходы: {totals['exp']:.2f}\n"
            f"💰 Баланс: {(totals['inc'] - totals['exp']):.2f}\n"
            f"🤝 Долги: {debt:+.2f}\n"
            f"⭐ Итог: {(totals['inc'] - totals['exp'] + debt):.2f}\n\n"
        )

        if stats_type == "detailed":
            cur.execute("""
                SELECT category, SUM(amount) s
                FROM transactions
                WHERE user_id=%s AND type='expense'
                AND date BETWEEN %s AND %s
                GROUP BY category
                ORDER BY s DESC
            """, (uid, start, end))
            rows = cur.fetchall()
            if rows:
                text += "<b>Расходы по категориям:</b>\n"
                for r in rows:
                    text += f"• {r['category']}: {r['s']:.2f}\n"

    await cb.message.edit_text(text)
    await cb.message.answer("Главное меню", reply_markup=main_kb())
    await state.clear()

# --------------------- Аннулирование данных ---------------------
@dp.message(F.text == "Аннулировать данные 🗑️")
async def clear_data_start(message: Message, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, очистить всё", callback_data="confirm_clear")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(1)
    await message.answer("🗑️ Вы уверены, что хотите аннулировать все данные?", reply_markup=builder.as_markup())
    await state.set_state(States.confirming_clear)

@dp.callback_query(F.data == "confirm_clear")
async def clear_data_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    uid = callback.from_user.id
    conn = get_db_connection()
    if not conn:
        await callback.message.answer("❌ Ошибка базы данных.")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transactions WHERE user_id=%s", (uid,))
            cur.execute("DELETE FROM debts WHERE user_id=%s", (uid,))
            cur.execute("DELETE FROM categories WHERE user_id=%s", (uid,))
        conn.commit()
        await callback.message.edit_text("🗑️ Все данные аннулированы!", reply_markup=None)
        await callback.message.answer("Выбери действие:", reply_markup=main_kb())
    except Exception as e:
        logging.error(f"Clear data error: {e}")
        await callback.message.answer("❌ Ошибка при очистке данных.")
    finally:
        conn.close()
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
    init_db()
    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    logging.info(f"Webhook set: {WEBHOOK_URL}")

async def on_shutdown(app):
    await bot.delete_webhook(drop_pending_updates=True)
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
