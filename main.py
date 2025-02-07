import os
import re
import json
import sqlite3
import smtplib
import requests
import threading
from datetime import datetime, timedelta

from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler

import openai
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# ===============================
# Настройка переменных окружения
# ===============================
TWILIO_ACCOUNT_SID    = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN     = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER")

SMTP_SERVER    = os.environ.get("SMTP_SERVER")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER      = os.environ.get("SMTP_USER")
SMTP_PASSWORD  = os.environ.get("SMTP_PASSWORD")
MANAGER_EMAIL  = os.environ.get("MANAGER_EMAIL")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
WEATHER_API_KEY= os.environ.get("WEATHER_API_KEY")

openai.api_key = OPENAI_API_KEY

# ===============================
# Инициализация Flask-приложения
# ===============================
app = Flask(__name__)

# ===============================
# Инициализация базы данных (SQLite)
# ===============================
def init_db():
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    # Таблица для заказов
    c.execute('''CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    phone TEXT,
                    email TEXT,
                    order_details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT
                )''')
    # Таблица для логирования чата
    c.execute('''CREATE TABLE IF NOT EXISTS chat_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    message TEXT,
                    response TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )''')
    # Таблица для базы знаний
    c.execute('''CREATE TABLE IF NOT EXISTS knowledge_base (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT,
                    answer TEXT
                )''')
    conn.commit()
    conn.close()

init_db()

# ===============================
# Вспомогательные функции
# ===============================
def is_valid_phone(phone):
    """Проверка валидности номера телефона (базовая проверка)."""
    return re.match(r'^\+?\d{7,15}$', phone) is not None

def is_valid_email(email):
    """Проверка валидности email."""
    return re.match(r"[^@]+@[^@]+\.[^@]+", email) is not None

def send_whatsapp_message(to_phone, message):
    """
    Функция отправки сообщения в WhatsApp.
    В реальном применении используйте Twilio или другой сервис.
    """
    print(f"[WhatsApp] Отправка сообщения на {to_phone}: {message}")
    # Пример с использованием requests (закомментирован):
    # url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    # data = {
    #     'From': f'whatsapp:{TWILIO_WHATSAPP_NUMBER}',
    #     'To': f'whatsapp:{to_phone}',
    #     'Body': message
    # }
    # response = requests.post(url, data=data, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
    # return response.json()

def send_email(to_email, subject, message):
    """Функция отправки email через SMTP."""
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        msg = f"Subject: {subject}\n\n{message}"
        server.sendmail(SMTP_USER, to_email, msg)
        server.quit()
        print(f"[Email] Сообщение отправлено на {to_email}")
    except Exception as e:
        print("[Email] Ошибка отправки:", e)

def log_chat(user_id, message, response):
    """Логирование вопросов и ответов в базу данных."""
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("INSERT INTO chat_logs (user_id, message, response) VALUES (?, ?, ?)", (str(user_id), message, response))
    conn.commit()
    conn.close()

# ===============================
# Flask: Обработка заявок (/order)
# ===============================
@app.route("/order", methods=["POST"])
def order():
    data = request.json
    name = data.get("name")
    phone = data.get("phone")
    email = data.get("email")
    order_details = data.get("order_details")

    # Валидация обязательных полей
    if not (name and phone and order_details):
        return jsonify({"error": "Отсутствуют обязательные поля"}), 400
    if not is_valid_phone(phone):
        return jsonify({"error": "Неверный формат телефона"}), 400
    if email and not is_valid_email(email):
        return jsonify({"error": "Неверный формат email"}), 400

    # Сохранение заявки в БД
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("INSERT INTO orders (name, phone, email, order_details, status) VALUES (?, ?, ?, ?, ?)",
              (name, phone, email, order_details, "new"))
    order_id = c.lastrowid
    conn.commit()
    conn.close()

    # Отправка сообщения в WhatsApp
    whatsapp_message = f"Спасибо, {name}! Ваш заказ получен. Детали заказа: {order_details}. Мы скоро свяжемся с вами."
    send_whatsapp_message(phone, whatsapp_message)

    # Отправка email (если указан) и уведомление менеджера
    if email:
        email_subject = "Подтверждение заказа"
        email_message = f"Здравствуйте, {name}!\n\nВаш заказ: {order_details} успешно принят."
        send_email(email, email_subject, email_message)
    manager_message = f"Новая заявка от {name}.\nТелефон: {phone}\nEmail: {email}\nЗаказ: {order_details}"
    send_email(MANAGER_EMAIL, "Новая заявка", manager_message)

    # Планирование напоминания через 24 часа
    scheduler.add_job(func=send_reminder, trigger="date",
                      run_date=datetime.now() + timedelta(hours=24),
                      args=[phone, name, order_details])
    return jsonify({"status": "Заявка получена", "order_id": order_id}), 200

def send_reminder(phone, name, order_details):
    """Функция для отправки напоминания клиенту."""
    reminder_message = f"Здравствуйте, {name}. Напоминаем, что ваш заказ ({order_details}) ожидает подтверждения. Свяжитесь с нами для завершения заказа."
    send_whatsapp_message(phone, reminder_message)

# ===============================
# Telegram-бот: обработка команд и сообщений
# ===============================
def start(update: Update, context: CallbackContext):
    update.message.reply_text("Привет! Я бот автоматизации бизнеса. Задавайте вопросы по клубнике.")

def handle_private_message(update: Update, context: CallbackContext):
    """Обработка сообщений в личном чате Telegram."""
    user_message = update.message.text
    user_id = update.message.from_user.id

    # Проверка базы знаний
    answer = check_knowledge_base(user_message)
    if answer:
        update.message.reply_text(answer)
        log_chat(user_id, user_message, answer)
    else:
        # Если ответа нет в базе, используем GPT-3.5 Turbo для стандартного ответа
        response = ask_gpt(user_message, model="gpt-3.5-turbo")
        update.message.reply_text(response)
        log_chat(user_id, user_message, response)

def handle_group_question(update: Update, context: CallbackContext):
    """
    Обработка команды /question в групповом чате.
    Фильтруем темы (только по клубнике, фермах, выращиванию).
    """
    if context.args:
        question = " ".join(context.args)
        keywords = ["клубника", "ферма", "выращивание", "теплица"]
        if any(kw in question.lower() for kw in keywords):
            answer = check_knowledge_base(question)
            if not answer:
                answer = ask_gpt(question, model="gpt-3.5-turbo")
            reply = f"{answer}\n\nСовет: регулярно проверяйте состояние растений!"
            update.message.reply_text(reply)
            log_chat(update.message.chat.id, question, reply)
        else:
            update.message.reply_text("Ваш вопрос не относится к теме клубники или фермерства.")
    else:
        update.message.reply_text("Пожалуйста, задайте вопрос после команды /question.")

def checklist_command(update: Update, context: CallbackContext):
    """Генерация чек-листа по заданной теме (например, 'теплица')."""
    if context.args:
        topic = " ".join(context.args)
        checklist = generate_checklist(topic)
        update.message.reply_text(checklist)
    else:
        update.message.reply_text("Укажите тему для чек-листа, например: /checklist теплица")

def daily_report(update: Update, context: CallbackContext):
    """Генерация ежедневного отчёта по наиболее часто задаваемым вопросам."""
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("SELECT message, COUNT(*) as cnt FROM chat_logs GROUP BY message ORDER BY cnt DESC LIMIT 5")
    popular = c.fetchall()
    conn.close()
    report = "Ежедневный отчёт по вопросам:\n"
    for msg, cnt in popular:
        report += f"{msg}: {cnt} раз(а)\n"
    update.message.reply_text(report)

def complex_consultation(update: Update, context: CallbackContext):
    """Использование GPT-4 для сложных консультаций (/complex)."""
    if context.args:
        question = " ".join(context.args)
        answer = ask_gpt(question, model="gpt-4")
        update.message.reply_text(answer)
        log_chat(update.message.from_user.id, question, answer)
    else:
        update.message.reply_text("Пожалуйста, укажите вопрос после команды /complex")

def check_knowledge_base(question):
    """Поиск ответа в базе знаний."""
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("SELECT answer FROM knowledge_base WHERE question = ?", (question,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def ask_gpt(question, model="gpt-3.5-turbo"):
    """
    Функция для обращения к OpenAI.
    Использует GPT-3.5 Turbo для стандартных вопросов и GPT-4 для сложных.
    """
    try:
        response = openai.ChatCompletion.create(
            model=model,
            messages=[
                {"role": "system", "content": "Ты бот, который отвечает на вопросы по клубнике кратко и по делу."},
                {"role": "user", "content": question}
            ],
            temperature=0.7,
        )
        answer = response.choices[0].message.content.strip()
        return answer
    except Exception as e:
        print("[OpenAI] Ошибка:", e)
        return "Извините, произошла ошибка при обработке запроса."

def generate_checklist(topic):
    """Пример генерации чек-листа по заданной теме."""
    checklists = {
        "теплица": "Чек-лист по подготовке теплицы:\n1. Проверьте температуру\n2. Убедитесь в наличии вентиляции\n3. Проверьте систему полива",
        "ошибки": "Топ-5 ошибок при выращивании:\n1. Неправильный режим полива\n2. Некачественный грунт\n3. Недостаток света\n4. Плохой дренаж\n5. Неподходящий сорт клубники"
    }
    return checklists.get(topic.lower(), "Чек-лист не найден для указанной темы.")

# ===============================
# Запуск Telegram-бота в отдельном потоке
# ===============================
def run_telegram_bot():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("question", handle_group_question))
    dp.add_handler(CommandHandler("checklist", checklist_command))
    dp.add_handler(CommandHandler("report", daily_report))
    dp.add_handler(CommandHandler("complex", complex_consultation))
    dp.add_handler(MessageHandler(Filters.private & Filters.text, handle_private_message))

    updater.start_polling()
    updater.idle()

# ===============================
# Планировщик задач (напоминания, уведомления о погоде)
# ===============================
scheduler = BackgroundScheduler()
scheduler.start()

def weather_notification():
    """
    Функция проверки погоды.
    Здесь можно интегрировать OpenWeatherMap API и отправлять рекомендации.
    """
    print("[Weather] Проверка погоды и отправка уведомлений...")
    # Пример:
    # url = f"http://api.openweathermap.org/data/2.5/weather?q=Moscow&appid={WEATHER_API_KEY}&units=metric"
    # response = requests.get(url).json()
    # Если влажность высока, отправить уведомление пользователям.
    # ...

scheduler.add_job(func=weather_notification, trigger="interval", hours=1)

# ===============================
# Основной запуск: Flask-сервер + Telegram-бот
# ===============================
if __name__ == "__main__":
    # Запуск Telegram-бота в отдельном потоке
    threading.Thread(target=run_telegram_bot).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
