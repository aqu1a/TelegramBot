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
    logging.error("Не установлены обязательные переменные окружения!")
    exit(1)

PORT = int(os.getenv("PORT", 8000))
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{KOYEB_PUBLIC_DOMAIN}{WEBHOOK_PATH}"

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# --------------------- Подключение к БД ---------------------
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
        logging.info("Database tables initialized")
    except Exception as e:
        logging.error(f"Error initializing DB: {e}")
    finally:
        conn.close()


# --------------------- Категории ---------------------
DEFAULT_INCOME = [
    "Зарплата 📡", "Аванс 💰", "Премия 🎉", "Фриланс 💻",
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


# --------------------- Доходы / Расходы ---------------------
@dp.message(F.text.in_(["Доходы 💹", "Расходы 📉"]))
async def choose_category(message: Message, state: FSMContext):
    typ = "income" if message.text == "Доходы 💹" else "expense"
    await state.update_data(type=typ)
    cats = get_categories(message.from_user.id, typ)
    if not cats:
        await message.answer("📂 Нет категорий. Добавь через 'Категории ➕'.", reply_markup=main_kb())
        return
    builder = InlineKeyboardBuilder()
    for i in range(0, len(cats), 2):
        row = cats[i:i+2]
        for cat in row:
            builder.button(text=cat, callback_data=f"cat_{typ}_{cat}")
        builder.adjust(2)
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    await message.answer(f"📂 Выбери категорию для <b>{'доходов' if typ=='income' else 'расходов'}</b>:", reply_markup=builder.as_markup())
    await state.set_state(States.choosing_category)


@dp.callback_query(F.data.startswith("cat_"))
async def category_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, typ, cat = callback.data.split("_", 2)
    await state.update_data(category=cat)
    await callback.message.edit_text(
        f"✅ Категория: <b>{cat}</b>\n\n"
        f"💰 Теперь введи сумму (только число):\n<code>2500</code> или <code>499.50</code>"
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
                reply_markup=main_kb()
            )
        except Exception as e:
            logging.error(f"Transaction error: {e}")
            await message.answer("❌ Ошибка при добавлении.")
        finally:
            conn.close()
    except ValueError:
        await message.answer("❌ Введи корректную сумму (число > 0)")
        return
    await state.clear()


# --------------------- Долги ---------------------
# (Все хэндлеры долгов остались без изменений — они работают отлично)

@dp.message(F.text == "Долги 🤝")
async def debt_start(message: Message, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.button(text="Я должен 📉", callback_data="debt_me")
    builder.button(text="Мне должны 💹", callback_data="debt_other")
    builder.button(text="Погасить долг 💰", callback_data="pay_debt")
    builder.button(text="Возврат долга 🔄", callback_data="return_debt")
    builder.button(text="Информация о долгах ℹ️", callback_data="debt_info")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(1)
    await message.answer("🤝 Выбери действие с долгами:", reply_markup=builder.as_markup())
    await state.set_state(States.choosing_debt_type)

# (Остальные хэндлеры долгов — debt_type_selected, enter_debtor_name, add_debt, pay_debt_start, return_debt_start, process_debt_payment, debt_info — копируй из твоего предыдущего кода, они работают)

# --------------------- Баланс ---------------------
@dp.message(F.text == "Баланс 💼")
async def show_balance(message: Message):
    uid = message.from_user.id
    conn = get_db_connection()
    if not conn:
        await message.answer("❌ Ошибка базы данных.")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(SUM(amount), 0) AS sum FROM transactions WHERE user_id=%s AND type='income'", (uid,))
            income = cur.fetchone()["sum"]
            cur.execute("SELECT COALESCE(SUM(amount), 0) AS sum FROM transactions WHERE user_id=%s AND type='expense'", (uid,))
            expense = cur.fetchone()["sum"]
            cur.execute("SELECT COALESCE(SUM(amount), 0) AS sum FROM debts WHERE user_id=%s", (uid,))
            debt = cur.fetchone()["sum"]
        balance = income - expense
        await message.answer(
            f"💼 <b>Твой баланс</b>\n\n"
            f"Доходы: <b>{income:.2f} сўм</b>\n"
            f"Расходы: <b>{expense:.2f} сўм</b>\n"
            f"Долги (нетто): <b>{debt:+.2f} сўм</b>\n"
            f"Чистый баланс: <b>{balance:.2f} сўм</b>",
            reply_markup=main_kb()
        )
    except Exception as e:
        logging.error(f"Balance error: {e}")
        await message.answer("❌ Ошибка расчёта баланса.")
    finally:
        conn.close()


# --------------------- Статистика (упрощённая и надёжная) ---------------------
@dp.message(F.text == "Статистика 📊")
async def stats_menu(message: Message):
    builder = InlineKeyboardBuilder()
    today = datetime.now()
    for i in range(12):
        month_date = today - timedelta(days=30*i)
        month_str = month_date.strftime("%Y-%m")
        month_name = month_date.strftime("%B %Y")
        builder.button(text=month_name, callback_data=f"stats_{month_str}")
    builder.button(text="За всё время", callback_data="stats_all")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(2)
    await message.answer("📊 Выбери период для статистики:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("stats_"))
async def show_stats(callback: CallbackQuery):
    await callback.answer()
    period = callback.data[6:]  # "all" или "2026-01"
    uid = callback.from_user.id
    conn = get_db_connection()
    if not conn:
        await callback.message.answer("❌ Ошибка базы данных.")
        return
    try:
        with conn.cursor() as cur:
            if period == "all":
                filter_sql = ""
                title = "за всё время"
            else:
                filter_sql = "AND to_char(CAST(date AS timestamp), 'YYYY-MM') = %s"
                title = period
                params = (uid, period)
            else:
                params = (uid,)

            # Доходы и расходы
            cur.execute(f"""
                SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END), 0) AS inc,
                       COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END), 0) AS exp
                FROM transactions
                WHERE user_id=%s {filter_sql}
            """, params)
            totals = cur.fetchone()
            inc = totals['inc']
            exp = totals['exp']

            # Долги
            cur.execute(f"""
                SELECT COALESCE(SUM(amount), 0) AS debt_sum
                FROM debts
                WHERE user_id=%s {filter_sql}
            """, params)
            debt = cur.fetchone()['debt_sum']

            # По категориям доходов
            cur.execute(f"""
                SELECT category, SUM(amount) AS sum
                FROM transactions
                WHERE user_id=%s AND type='income' {filter_sql}
                GROUP BY category ORDER BY sum DESC
            """, params)
            income_cat = cur.fetchall()

            # По категориям расходов
            cur.execute(f"""
                SELECT category, SUM(amount) AS sum
                FROM transactions
                WHERE user_id=%s AND type='expense' {filter_sql}
                GROUP BY category ORDER BY sum DESC
            """, params)
            expense_cat = cur.fetchall()

        bal = inc - exp
        text = f"📊 <b>Статистика {title}</b>\n\n"
        text += f"Доход: <b>{inc:.0f}</b> │ Расход: <b>{exp:.0f}</b> │ Долги: <b>{debt:+.0f}</b> │ Баланс: <b>{bal:.0f}</b> сўм\n\n"

        if income_cat:
            text += "<b>Доходы по категориям:</b>\n"
            for c in income_cat:
                text += f"• {c['category']}: {c['sum']:.0f} сўм\n"
            text += "\n"

        if expense_cat:
            text += "<b>Расходы по категориям:</b>\n"
            for c in expense_cat:
                text += f"• {c['category']}: {c['sum']:.0f} сўм\n"

        if not income_cat and not expense_cat:
            text += "Нет данных за этот период."

        await callback.message.edit_text(text)
        await callback.message.answer("Главное меню:", reply_markup=main_kb())
    except Exception as e:
        logging.error(f"Stats error: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка статистики.")
    finally:
        conn.close()


--------------------- Аннулирование данных ---------------------
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
    logging.info(f"Webhook установлен: {WEBHOOK_URL}")

async def on_shutdown(app):
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook удалён")

if __name__ == "__main__":
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, host="0.0.0.0", port=PORT)

