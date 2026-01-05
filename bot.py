import logging
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.errors import UniqueViolation
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
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)

# Переменные окружения (Koyeb предоставляет их автоматически)
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
KOYEB_PUBLIC_DOMAIN = os.getenv("KOYEB_PUBLIC_DOMAIN")

if not TOKEN:
    logging.error("Переменная TOKEN не установлена!")
    exit(1)
if not DATABASE_URL:
    logging.error("Переменная DATABASE_URL не установлена! Подключите PostgreSQL в Koyeb.")
    exit(1)
if not KOYEB_PUBLIC_DOMAIN:
    logging.error("Переменная KOYEB_PUBLIC_DOMAIN не установлена!")
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
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    except Exception as e:
        logging.error(f"Failed to connect to DB: {e}")
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


# --------------------- Клавиатуры ---------------------
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

    await message.answer(
        f"📂 Выбери категорию для <b>{'доходов' if typ == 'income' else 'расходов'}</b>:",
        reply_markup=builder.as_markup()
    )
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
    except ValueError:
        await message.answer("❌ Введи корректную сумму (число > 0)")
        return

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
                "INSERT INTO transactions (user_id, type, category, amount, date) "
                "VALUES (%s, %s, %s, %s, %s)",
                (message.from_user.id, typ, cat, amount, datetime.now().strftime("%Y-%m-%d %H:%M"))
            )
        conn.commit()

        emoji = "💹" if typ == "income" else "📉"
        await message.answer(
            f"{emoji} <b>{'Доход' if typ == 'income' else 'Расход'}</b> добавлен!\n"
            f"💰 <b>{amount:.2f} сўм</b> → {cat}",
            reply_markup=main_kb()
        )
    except Exception as e:
        logging.error(f"Transaction add error: {e}")
        await message.answer("❌ Ошибка при добавлении транзакции.")
    finally:
        conn.close()

    await state.clear()


# --------------------- Долги ---------------------
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
        f"{'Я должен (-)' if data['is_me'] else 'Мне должны (+)'}"
    )
    await state.set_state(States.entering_debt_amount)


@dp.message(States.entering_debt_amount)
async def add_debt(message: Message, state: FSMContext):
    text = message.text.strip().replace(",", ".")
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введи корректную сумму (число > 0)")
        return

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
                "INSERT INTO debts (user_id, debtor, amount, description, date) "
                "VALUES (%s, %s, %s, %s, %s)",
                (message.from_user.id, data["debtor"], sign * amount, description,
                 datetime.now().strftime("%Y-%m-%d %H:%M"))
            )
        conn.commit()
        await message.answer(
            f"🤝 Долг записан: <b>{amount:.2f} сўм</b> ({description}) — {data['debtor']}",
            reply_markup=main_kb()
        )
    except Exception as e:
        logging.error(f"Debt add error: {e}")
        await message.answer("❌ Ошибка при добавлении долга.")
    finally:
        conn.close()

    await state.clear()


@dp.callback_query(F.data == "pay_debt")
async def pay_debt_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    uid = callback.from_user.id
    conn = get_db_connection()
    if not conn:
        await callback.message.answer("❌ Ошибка базы данных.")
        return

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, debtor, amount, description, date FROM debts "
                "WHERE user_id=%s AND amount < 0 ORDER BY date DESC", (uid,)
            )
            rows = cur.fetchall()

        if not rows:
            await callback.message.answer("ℹ️ Нет долгов, которые вы должны.", reply_markup=main_kb())
            await state.clear()
            return

        builder = InlineKeyboardBuilder()
        for row in rows:
            text = f"{row['description']} {row['debtor']} {abs(row['amount']):.2f} сўм ({row['date'][:10]})"
            builder.button(text=text, callback_data=f"pay_{row['id']}")
        builder.button(text="❌ Отмена", callback_data="cancel")
        builder.adjust(1)

        await callback.message.edit_text("Выберите долг для погашения:", reply_markup=builder.as_markup())
        await state.set_state(States.choosing_debt_to_pay)
    except Exception as e:
        logging.error(f"Pay debt error: {e}")
        await callback.message.answer("❌ Ошибка при загрузке долгов.")
    finally:
        conn.close()


@dp.callback_query(F.data == "return_debt")
async def return_debt_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    uid = callback.from_user.id
    conn = get_db_connection()
    if not conn:
        await callback.message.answer("❌ Ошибка базы данных.")
        return

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, debtor, amount, description, date FROM debts "
                "WHERE user_id=%s AND amount > 0 ORDER BY date DESC", (uid,)
            )
            rows = cur.fetchall()

        if not rows:
            await callback.message.answer("ℹ️ Нет долгов, которые вам должны.", reply_markup=main_kb())
            await state.clear()
            return

        builder = InlineKeyboardBuilder()
        for row in rows:
            text = f"{row['description']} {row['debtor']} {row['amount']:.2f} сўм ({row['date'][:10]})"
            builder.button(text=text, callback_data=f"return_{row['id']}")
        builder.button(text="❌ Отмена", callback_data="cancel")
        builder.adjust(1)

        await callback.message.edit_text("Выберите долг для возврата:", reply_markup=builder.as_markup())
        await state.set_state(States.choosing_debt_to_pay)
    except Exception as e:
        logging.error(f"Return debt error: {e}")
        await callback.message.answer("❌ Ошибка при загрузке долгов.")
    finally:
        conn.close()


@dp.callback_query(F.data.startswith(("pay_", "return_")))
async def process_debt_payment(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action, debt_id_str = callback.data.split("_")
    try:
        debt_id = int(debt_id_str)
    except ValueError:
        await callback.message.answer("❌ Некорректный ID долга.")
        return

    conn = get_db_connection()
    if not conn:
        await callback.message.answer("❌ Ошибка базы данных.")
        return

    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM debts WHERE id=%s AND user_id=%s", (debt_id, callback.from_user.id))
            if cur.rowcount == 0:
                await callback.message.answer("❌ Долг не найден или уже обработан.")
                return
        conn.commit()

        action_text = "погашен" if action == "pay" else "возвращён"
        await callback.message.edit_text(f"✅ Долг {action_text}!", reply_markup=None)
        await callback.message.answer("Выбери следующее действие:", reply_markup=main_kb())
    except Exception as e:
        logging.error(f"Debt process error: {e}")
        await callback.message.answer("❌ Ошибка при обработке долга.")
    finally:
        conn.close()

    await state.clear()


@dp.callback_query(F.data == "debt_info")
async def debt_info(callback: CallbackQuery):
    await callback.answer()
    uid = callback.from_user.id
    conn = get_db_connection()
    if not conn:
        await callback.message.answer("❌ Ошибка базы данных.")
        return

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT debtor, amount, description, date FROM debts WHERE user_id=%s ORDER BY date DESC", (uid,)
            )
            rows = cur.fetchall()

        if not rows:
            await callback.message.answer("ℹ️ Долгов пока нет.", reply_markup=main_kb())
            return

        text = "ℹ️ <b>Информация о долгах:</b>\n\n"
        for row in rows:
            sign = "-" if row['amount'] < 0 else "+"
            text += f"{row['description']} {row['debtor']} {sign}{abs(row['amount']):.2f} сўм ({row['date'][:10]})\n"

        await callback.message.answer(text, reply_markup=main_kb())
    except Exception as e:
        logging.error(f"Debt info error: {e}")
        await callback.message.answer("❌ Ошибка при загрузке информации о долгах.")
    finally:
        conn.close()


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
            cur.execute(
                "INSERT INTO categories (user_id, type, name) VALUES (%s, %s, %s)",
                (user_id, typ, name)
            )
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


# --------------------- Баланс ---------------------
@dp.message(F.text == "Баланс 💼")
async def show_balance(message: Message):
    uid = message.from_user.id
    conn = get_db_connection()
    if not conn:
        await message.answer("❌ Ошибка связи с базой данных. Попробуй позже.")
        return

    try:
        with conn.cursor() as cur:
            # Доходы
            cur.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE user_id=%s AND type='income'",
                (uid,)
            )
            income = cur.fetchone()[0]

            # Расходы
            cur.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE user_id=%s AND type='expense'",
                (uid,)
            )
            expense = cur.fetchone()[0]

            # Долги (нетто: положительные — мне должны, отрицательные — я должен)
            cur.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM debts WHERE user_id=%s",
                (uid,)
            )
            debt = cur.fetchone()[0]

        balance = income - expense

        await message.answer(
            f"💼 <b>Твой текущий баланс</b>\n\n"
            f"📊 Доходы: <b>{income:.2f} сўм</b>\n"
            f"📉 Расходы: <b>{expense:.2f} сўм</b>\n"
            f"🤝 Долги (нетто): <b>{debt:+.2f} сўм</b>\n"
            f"🌟 <b>Чистый баланс (Доходы − Расходы): {balance:.2f} сўм</b>",
            reply_markup=main_kb()
        )
    except Exception as e:
        logging.error(f"Balance calculation error: {e}", exc_info=True)  # Полная трассировка
        await message.answer("❌ Ошибка при расчёте баланса. Попробуй позже.")
    finally:
        conn.close()


# --------------------- Статистика ---------------------
@dp.message(F.text == "Статистика 📊")
async def show_stats_start(message: Message, state: FSMContext):
    await message.answer(
        "📊 Введи месяц для статистики в формате <code>YYYY-MM</code> (например, 2026-01).\n\n"
        "Или напиши <code>all</code> для статистики за последние 6 месяцев.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
        ])
    )
    await state.set_state(States.entering_stats_month)


@dp.message(States.entering_stats_month)
async def show_stats(message: Message, state: FSMContext):
    month_input = message.text.strip()
    uid = message.from_user.id
    conn = get_db_connection()
    if not conn:
        await message.answer("❌ Ошибка связи с базой данных. Попробуй позже.")
        await state.clear()
        return

    try:
        if month_input.lower() == 'all':
            # Последние 6 месяцев с транзакциями
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        to_char(CAST(date AS timestamp), 'YYYY-MM') AS month,
                        COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END), 0) AS inc,
                        COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END), 0) AS exp
                    FROM transactions
                    WHERE user_id=%s
                    GROUP BY month
                    ORDER BY month DESC
                    LIMIT 6
                """, (uid,))
                trans_rows = cur.fetchall()

                # Долги по месяцам (для дополнения)
                cur.execute("""
                    SELECT 
                        to_char(CAST(date AS timestamp), 'YYYY-MM') AS month,
                        COALESCE(SUM(amount), 0) AS debt_sum
                    FROM debts 
                    WHERE user_id=%s
                    GROUP BY month
                    ORDER BY month DESC
                """, (uid,))
                debt_dict = {row['month']: row['debt_sum'] for row in cur.fetchall()}

            if not trans_rows:
                await message.answer("📊 Пока нет данных для статистики. Добавь доходы или расходы!", reply_markup=main_kb())
                await state.clear()
                return

            text = "📊 <b>Статистика за последние 6 месяцев</b>\n\n"
            for row in trans_rows:
                debt = debt_dict.get(row['month'], 0.0)
                balance = row['inc'] - row['exp']
                text += (
                    f"<code>{row['month']}</code> │ "
                    f"Доход: <b>{row['inc']:.0f}</b> │ "
                    f"Расход: <b>{row['exp']:.0f}</b> │ "
                    f"Долги: <b>{debt:+.0f}</b> │ "
                    f"Баланс: <b>{balance:.0f}</b> сўм\n"
                )

        else:
            # Конкретный месяц
            try:
                datetime.strptime(month_input, "%Y-%m")
            except ValueError:
                await message.answer("❌ Некорректный формат. Используй YYYY-MM, например: <code>2026-01</code>")
                return

            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END), 0) AS inc,
                        COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END), 0) AS exp
                    FROM transactions
                    WHERE user_id=%s 
                      AND to_char(CAST(date AS timestamp), 'YYYY-MM') = %s
                """, (uid, month_input))
                row = cur.fetchone()
                inc = row['inc']
                exp = row['exp']

                cur.execute("""
                    SELECT COALESCE(SUM(amount), 0)
                    FROM debts
                    WHERE user_id=%s 
                      AND to_char(CAST(date AS timestamp), 'YYYY-MM') = %s
                """, (uid, month_input))
                debt = cur.fetchone()[0]

            balance = inc - exp
            text = f"📊 <b>Статистика за {month_input}</b>\n\n"
            text += (
                f"Доход: <b>{inc:.0f}</b> │ "
                f"Расход: <b>{exp:.0f}</b> │ "
                f"Долги: <b>{debt:+.0f}</b> │ "
                f"Баланс: <b>{balance:.0f}</b> сўм"
            )

        await message.answer(text, reply_markup=main_kb())

    except Exception as e:
        logging.error(f"Stats calculation error: {e}", exc_info=True)
        await message.answer("❌ Ошибка при формировании статистики. Попробуй позже.")
    finally:
        conn.close()

    await state.clear()

# --------------------- Аннулирование данных ---------------------
@dp.message(F.text == "Аннулировать данные 🗑️")
async def clear_data_start(message: Message, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, очистить всё", callback_data="confirm_clear")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(1)

    await message.answer(
        "🗑️ <b>Внимание!</b> Ты действительно хочешь удалить ВСЕ свои данные?\n"
        "Это действие необратимо.",
        reply_markup=builder.as_markup()
    )
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

        await callback.message.edit_text("🗑️ Все данные успешно удалены!", reply_markup=None)
        await callback.message.answer("Главное меню:", reply_markup=main_kb())
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
    await callback.message.edit_text("🏠 Действие отменено.", reply_markup=None)
    await callback.message.answer("Выбери действие:", reply_markup=main_kb())


# --------------------- Неизвестные сообщения ---------------------
@dp.message()
async def unknown_message(message: Message):
    await message.answer("❓ Не понял команду. Используй кнопки ниже или /start", reply_markup=main_kb())


# --------------------- Webhook ---------------------
async def on_startup(app):
    init_db()
    await bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True)
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


