import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import ParseMode, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.executor import start_webhook
import sqlite3
from datetime import datetime

# Получаем токен и порт из переменных окружения (для Koyeb)
TOKEN = os.getenv('TOKEN')  # Установи в Koyeb как env variable
PORT = int(os.getenv('PORT', 8443))
import os  # Уже есть в коде сверху

WEBHOOK_HOST = f"https://{os.getenv('KOYEB_PUBLIC_DOMAIN')}"
WEBHOOK_PATH = '/webhook'
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# Подключение к БД (SQLite)
conn = sqlite3.connect('finance.db')
cursor = conn.cursor()

# Создание таблиц
cursor.execute('''
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT,  -- 'income' или 'expense'
    category TEXT,
    amount REAL,
    description TEXT,
    date TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS debts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    debtor TEXT,  -- 'me' (я должен) или имя (должен мне)
    amount REAL,
    description TEXT,
    date TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT,  -- 'income' или 'expense'
    name TEXT
)
''')
conn.commit()

# Предопределённые категории (можно добавить больше)
DEFAULT_INCOME_CATS = ['Зарплата 💰', 'Подарок 🎁', 'Инвестиции 📈']
DEFAULT_EXPENSE_CATS = ['Еда 🍔', 'Транспорт 🚗', 'Развлечения 🎉']

# Функция для получения категорий пользователя
def get_categories(user_id, cat_type):
    cursor.execute("SELECT name FROM categories WHERE user_id = ? AND type = ?", (user_id, cat_type))
    cats = [row[0] for row in cursor.fetchall()]
    return DEFAULT_INCOME_CATS + cats if cat_type == 'income' else DEFAULT_EXPENSE_CATS + cats

# Команда /start
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    user_id = message.from_user.id
    # Инициализируем категории, если нужно
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton('Доходы 💹'), KeyboardButton('Расходы 📉'))
    keyboard.add(KeyboardButton('Долги 🤝'), KeyboardButton('Баланс 💼'))
    keyboard.add(KeyboardButton('Статистика 📊'), KeyboardButton('Добавить категорию ➕'))
    await message.reply("Привет! Я твой финансовый помощник 😊\n"
                        "Выбери действие с кнопок ниже. Для ввода суммы просто напиши число после выбора.",
                        reply_markup=keyboard)

# Обработчик для кнопок (текстовые сообщения)
@dp.message_handler(lambda message: message.text in ['Доходы 💹', 'Расходы 📉', 'Долги 🤝', 'Баланс 💼', 'Статистика 📊', 'Добавить категорию ➕'])
async def handle_buttons(message: types.Message):
    text = message.text
    if text == 'Доходы 💹':
        await show_categories(message, 'income')
    elif text == 'Расходы 📉':
        await show_categories(message, 'expense')
    elif text == 'Долги 🤝':
        await add_debt_start(message)
    elif text == 'Баланс 💼':
        await show_balance(message)
    elif text == 'Статистика 📊':
        await show_stats(message)
    elif text == 'Добавить категорию ➕':
        await add_category_start(message)

# Показать категории (inline buttons)
async def show_categories(message: types.Message, cat_type):
    user_id = message.from_user.id
    cats = get_categories(user_id, cat_type)
    keyboard = InlineKeyboardMarkup(row_width=2)
    for cat in cats:
        keyboard.add(InlineKeyboardButton(cat, callback_data=f"{cat_type}_{cat}"))
    keyboard.add(InlineKeyboardButton("Отмена ❌", callback_data="cancel"))
    await message.reply(f"Выбери категорию для { 'дохода 💹' if cat_type == 'income' else 'расхода 📉' }:",
                        reply_markup=keyboard)

# Callback для выбора категории
@dp.callback_query_handler(lambda c: c.data.startswith('income_') or c.data.startswith('expense_'))
async def process_category(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    cat_type, category = callback_query.data.split('_', 1)
    user_id = callback_query.from_user.id
    # Сохраняем состояние (можно использовать FSM, но для простоты используем глобальный или просто ждём сообщение)
    await bot.send_message(callback_query.from_user.id, f"Выбрана категория: {category}\n"
                           f"Введи сумму (число) и описание (опционально) через пробел: ")
    # Для обработки следующего сообщения зарегистрируем временный хэндлер, но для простоты используем state или ждём
    # Здесь для упрощения: следующий хэндлер на текст после этого
    dp.register_message_handler(lambda m: add_transaction(m, cat_type, category), content_types=['text'])

# Добавление транзакции
async def add_transaction(message: types.Message, cat_type, category):
    try:
        parts = message.text.split()
        amount = float(parts[0])
        description = ' '.join(parts[1:]) or 'Без описания'
        user_id = message.from_user.id
        date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        amt = amount if cat_type == 'income' else -amount
        cursor.execute("INSERT INTO transactions (user_id, type, category, amount, description, date) VALUES (?, ?, ?, ?, ?, ?)",
                       (user_id, cat_type, category, amt, description, date))
        conn.commit()
        await message.reply(f"{'Доход 💹' if cat_type == 'income' else 'Расход 📉'} {amount} добавлен в {category}: {description} 🎉")
    except:
        await message.reply("Ошибка 😔 Формат: <сумма> <описание>")

# Добавление категории
async def add_category_start(message: types.Message):
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("Для доходов 💹", callback_data="add_cat_income"))
    keyboard.add(InlineKeyboardButton("Для расходов 📉", callback_data="add_cat_expense"))
    await message.reply("Выбери тип категории:", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith('add_cat_'))
async def process_add_category(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    cat_type = callback_query.data.split('_')[2]
    await bot.send_message(callback_query.from_user.id, f"Введи название новой категории для { 'доходов 💹' if cat_type == 'income' else 'расходов 📉' }:")
    dp.register_message_handler(lambda m: save_category(m, cat_type), content_types=['text'])

async def save_category(message: types.Message, cat_type):
    name = message.text.strip() + ' 🆕'  # Добавим эмодзи для новых
    user_id = message.from_user.id
    cursor.execute("INSERT INTO categories (user_id, type, name) VALUES (?, ?, ?)", (user_id, cat_type, name))
    conn.commit()
    await message.reply(f"Категория '{name}' добавлена! 🎊")

# Добавление долга
async def add_debt_start(message: types.Message):
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("Я должен (вычесть из баланса) 📉", callback_data="debt_me"))
    keyboard.add(InlineKeyboardButton("Мне должны (добавить в баланс) 💹", callback_data="debt_other"))
    await message.reply("Тип долга:", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith('debt_'))
async def process_debt_type(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    debtor_type = callback_query.data
    await bot.send_message(callback_query.from_user.id, "Введи сумму, описание и имя (если не 'me') через пробел: <сумма> <описание> <имя>")
    dp.register_message_handler(lambda m: add_debt(m, debtor_type), content_types=['text'])

async def add_debt(message: types.Message, debtor_type):
    try:
        parts = message.text.split()
        amount = float(parts[0])
        description = parts[1]
        debtor = 'me' if debtor_type == 'debt_me' else ' '.join(parts[2:])
        user_id = message.from_user.id
        date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("INSERT INTO debts (user_id, debtor, amount, description, date) VALUES (?, ?, ?, ?, ?)",
                       (user_id, debtor, amount if debtor != 'me' else -amount, description, date))  # Отрицательно если 'me'
        conn.commit()
        await message.reply(f"Долг {amount} добавлен: {description} ({debtor}) 🤝")
    except:
        await message.reply("Ошибка 😔 Формат: <сумма> <описание> <имя>")

# Показать баланс
async def show_balance(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT SUM(amount) FROM transactions WHERE user_id = ?", (user_id,))
    trans_balance = cursor.fetchone()[0] or 0
    cursor.execute("SELECT SUM(amount) FROM debts WHERE user_id = ?", (user_id,))
    debt_balance = cursor.fetchone()[0] or 0
    total = trans_balance + debt_balance
    await message.reply(f"💼 Твой баланс:\n"
                        f"Из транзакций: {trans_balance:.2f} 💰\n"
                        f"Из долгов: {debt_balance:.2f} 🤝\n"
                        f"Общий: {total:.2f} 🌟")

# Показать статистику по месяцам
async def show_stats(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT strftime('%Y-%m', date) as month, SUM(CASE WHEN type='income' THEN amount ELSE 0 END) as income, "
                   "SUM(CASE WHEN type='expense' THEN -amount ELSE 0 END) as expense FROM transactions WHERE user_id = ? GROUP BY month ORDER BY month DESC",
                   (user_id,))
    stats = cursor.fetchall()
    if not stats:
        await message.reply("Нет данных для статистики 😔")
        return
    response = "📊 Статистика по месяцам:\n"
    for month, income, expense in stats:
        balance = income - expense
        response += f"{month}: Доходы {income:.2f} 💹 | Расходы {expense:.2f} 📉 | Баланс {balance:.2f} 💼\n"
    await message.reply(response)

# Отмена
@dp.callback_query_handler(lambda c: c.data == 'cancel')
async def cancel(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(callback_query.from_user.id, "Действие отменено ❌")

# Webhook setup для Koyeb
async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL)
    logging.info('Webhook set')

async def on_shutdown(dp):
    await bot.delete_webhook()
    logging.info('Webhook deleted')

if __name__ == '__main__':
    start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        skip_updates=True,
        host='0.0.0.0',
        port=PORT
    )


