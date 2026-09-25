#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
24SCORE.PRO — TELEGRAM-БОТ ДЛЯ ФУТБОЛЬНЫХ СИГНАЛОВ
Критерии:
1. Футбол
2. Коэффициент на ничью (X) >= 5.0
3. 1-й тайм: тотал голов больше 0.5 за последние 20 матчей >= 15 у любой команды
4. Уведомление в личку Telegram ровно за 30 минут до начала матча
5. Кнопки мгновенного получения всех предстоящих матчей на сегодня и завтра
"""

import sys
import os
import time
import json
import urllib.request
import urllib.parse
import threading
from datetime import datetime, timezone, timedelta
from parser_core import scan_all_matches

# ==================== НАСТРОЙКИ БОТА ====================
# Вставьте сюда токен, полученный от @BotFather в Telegram:
BOT_TOKEN = os.getenv("BOT_TOKEN", "7665985450:AAEMMFBjem3EyFNF7ovLTIDgJK6S-4z2NxQ")

# Критерии по умолчанию:
MIN_DRAW_ODDS = 5.0          # Кэф на ничью >= 5.0
MIN_1ST_HALF_OVER05 = 15     # Голов в 1-м тайме > 0.5 >= 15 из 20 матчей
ALERT_MINUTES_BEFORE = 30    # Сигнал за 30 минут до матча
TIMEZONE_OFFSET = 3          # GMT+3 (Москва)
CHECK_INTERVAL_SEC = 30      # Интервал проверки времени (секунды)
# ========================================================

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Хранилище подписчиков (чат ID пользователей, нажавших /start)
SUBSCRIBERS_FILE = "subscribers.json"
alerted_matches = set()
cached_matches = []
cache_time = 0

def load_subscribers():
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_subscribers(subs):
    try:
        with open(SUBSCRIBERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(subs), f)
    except Exception as e:
        print(f"Ошибка сохранения подписчиков: {e}")

subscribers = load_subscribers()

def tg_request(method, data=None):
    """Отправка запроса к Telegram Bot API без внешних библиотек"""
    if BOT_TOKEN == "ВАШ_ТОКЕН_ОТ_BOTFATHER" or not BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        if data:
            json_data = json.dumps(data).encode('utf-8')
            req = urllib.request.Request(url, data=json_data, headers={'Content-Type': 'application/json'})
        else:
            req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        # print(f"TG API Error ({method}): {e}")
        return None

def send_message(chat_id, text, reply_markup=None):
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False
    }
    if reply_markup:
        payload['reply_markup'] = reply_markup
    return tg_request('sendMessage', payload)

def get_main_keyboard():
    return {
        'keyboard': [
            [{'text': '⚽ Матчи на сегодня'}, {'text': '📅 Матчи на завтра'}],
            [{'text': '🔄 Обновить сейчас'}, {'text': '⚙️ Мои критерии'}]
        ],
        'resize_keyboard': True
    }

def calculate_minutes_to_match(match_time_str, match_date_str, tz=TIMEZONE_OFFSET):
    try:
        now = datetime.now(timezone.utc) + timedelta(hours=tz)
        now_naive = now.replace(tzinfo=None)
        match_dt = datetime.strptime(f"{match_date_str} {match_time_str}", "%Y-%m-%d %H:%M")
        diff = (match_dt - now_naive).total_seconds()
        return int(diff // 60)
    except Exception:
        return 9999

def format_match_card(m, is_alert=False):
    mins = calculate_minutes_to_match(m['time'], m['date'])
    if mins < 0:
        time_badge = "<i>(идёт / завершён)</i>"
    elif mins <= ALERT_MINUTES_BEFORE:
        time_badge = f"<b>🚨 СИГНАЛ: через {mins} мин!</b>"
    else:
        h = mins // 60
        mn = mins % 60
        time_badge = f"<i>(через {h}ч {mn}м)</i>"

    t1_pass = m.get('t1_over05_last20') and m['t1_over05_last20'] >= MIN_1ST_HALF_OVER05
    t2_pass = m.get('t2_over05_last20') and m['t2_over05_last20'] >= MIN_1ST_HALF_OVER05

    t1_stat = f"<b>{m.get('t1_over05_last20', '—')}/20</b> {'✅' if t1_pass else ''}"
    t2_stat = f"<b>{m.get('t2_over05_last20', '—')}/20</b> {'✅' if t2_pass else ''}"

    card = (
        f"🏆 <b>{m['league']}</b>\n"
        f"⏰ <b>{m['time']}</b> МСК {time_badge}\n"
        f"⚽ <b>{m['team1']}</b> — <b>{m['team2']}</b>\n\n"
        f"📊 <b>Кэф на Ничью:</b> <code>X = {m['draw_odds']}</code>\n"
        f"📈 <b>1-й тайм (ТБ 0.5 за 20 матчей):</b>\n"
        f"  • {m['team1']}: {t1_stat}\n"
        f"  • {m['team2']}: {t2_stat}\n"
        f"🔗 <a href=\"{m['full_url']}\">Открыть на 24score.pro</a>\n"
    )
    return card

def get_matches_for_date(date_str=None):
    global cached_matches, cache_time
    now = time.time()
    # Кэшируем на 2 минуты для экономии запросов
    if not date_str and (now - cache_time < 120) and cached_matches:
        return cached_matches

    matches = scan_all_matches(
        date_str=date_str,
        min_draw=MIN_DRAW_ODDS,
        min_over05=MIN_1ST_HALF_OVER05,
        time_offset=TIMEZONE_OFFSET
    )
    if not date_str:
        cached_matches = matches
        cache_time = now
    return matches

def send_matches_report(chat_id, date_str=None, title="Сегодня"):
    send_message(chat_id, f"🔍 <i>Сканирую 24score.pro ({title})... Пожалуйста, подождите 3-5 секунд.</i>")
    matches = get_matches_for_date(date_str)
    
    if not matches:
        send_message(
            chat_id,
            f"ℹ️ <b>Матчей по критериям на {title.lower()} не найдено.</b>\n\n"
            f"Критерии: Ничья >= {MIN_DRAW_ODDS}, 1-й тайм >0.5 (из 20) >= {MIN_1ST_HALF_OVER05}.\n"
            f"Бот продолжает мониторить обновления!",
            reply_markup=get_main_keyboard()
        )
        return

    msg = f"⚽ <b>НАЙДЕНО МАТЧЕЙ ПО КРИТЕРИЯМ ({title}): {len(matches)}</b>\n\n"
    send_message(chat_id, msg)
    
    for m in matches:
        card = format_match_card(m)
        send_message(chat_id, card, reply_markup=get_main_keyboard())
        time.sleep(0.3)

# ================= ФОНОВЫЙ МОНИТОРИНГ (СИГНАЛЫ ЗА 30 МИН) =================
def background_signal_monitor():
    """Каждые 30 секунд проверяет матчи и отправляет сигнал подписчикам за 30 минут"""
    print("[*] Монитор 30-минутных сигналов запущен...")
    while True:
        try:
            if subscribers and BOT_TOKEN and BOT_TOKEN != "ВАШ_ТОКЕН_ОТ_BOTFATHER":
                matches = get_matches_for_date()
                for m in matches:
                    mins = calculate_minutes_to_match(m['time'], m['date'])
                    
                    # УСЛОВИЕ СИГНАЛА: ровно в окне <= 30 минут до начала (0 .. 30)
                    if 0 <= mins <= ALERT_MINUTES_BEFORE:
                        key = f"{m['id']}_{m['time']}_{m['date']}"
                        if key not in alerted_matches:
                            alerted_matches.add(key)
                            
                            alert_text = (
                                f"🚨🚨🚨 <b>ВНИМАНИЕ! СИГНАЛ ЗА {mins} МИНУТ!</b> 🚨🚨🚨\n\n"
                                f"{format_match_card(m, is_alert=True)}"
                            )
                            print(f"[!] СИГНАЛ ОТПРАВЛЕН: {m['team1']} vs {m['team2']} (через {mins} мин)")
                            for user_id in list(subscribers):
                                send_message(user_id, alert_text, reply_markup=get_main_keyboard())
        except Exception as e:
            print(f"[!] Ошибка монитора: {e}")
        time.sleep(CHECK_INTERVAL_SEC)

# ================= ОБРАБОТКА ВХОДЯЩИХ СООБЩЕНИЙ =================
def start_bot_polling():
    global subscribers
    offset = 0
    print("[*] Бот слушает сообщения в Telegram...")
    
    while True:
        try:
            res = tg_request('getUpdates', {'offset': offset, 'timeout': 20})
            if res and res.get('ok'):
                for update in res.get('result', []):
                    offset = update['update_id'] + 1
                    msg = update.get('message')
                    if not msg or 'text' not in msg:
                        continue
                        
                    chat_id = msg['chat']['id']
                    text = msg['text'].strip()
                    
                    if chat_id not in subscribers:
                        subscribers.add(chat_id)
                        save_subscribers(subscribers)

                    if text == '/start':
                        welcome = (
                            f"👋 <b>Добро пожаловать в 24score Alert Bot!</b>\n\n"
                            f"Я автоматически отслеживаю футбольные матчи на <b>24score.pro</b> по вашим критериям:\n"
                            f"1. <b>Кэф на ничью (Х):</b> &ge; {MIN_DRAW_ODDS}\n"
                            f"2. <b>1-й тайм (ТБ 0.5 за 20 матчей):</b> &ge; {MIN_1ST_HALF_OVER05} у любой команды\n"
                            f"3. <b>Сигнал:</b> ровно за <b>30 минут</b> до начала игры прямо сюда в чат с пуш-звуком!\n\n"
                            f"Нажмите кнопку ниже, чтобы посмотреть предстоящие матчи:"
                        )
                        send_message(chat_id, welcome, reply_markup=get_main_keyboard())

                    elif 'Матчи на сегодня' in text or 'Обновить' in text:
                        now_msk = (datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)).strftime('%Y-%m-%d')
                        send_matches_report(chat_id, now_msk, "Сегодня")

                    elif 'Матчи на завтра' in text:
                        tm_msk = (datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET) + timedelta(days=1)).strftime('%Y-%m-%d')
                        send_matches_report(chat_id, tm_msk, "Завтра")

                    elif 'Мои критерии' in text:
                        info = (
                            f"⚙️ <b>Текущие параметры фильтрации:</b>\n\n"
                            f"• <b>Спорт:</b> Футбол\n"
                            f"• <b>Коэффициент на ничью (Х):</b> &ge; <code>{MIN_DRAW_ODDS}</code>\n"
                            f"• <b>Голы в 1-м тайме:</b> ТБ 0.5 за 20 игр &ge; <code>{MIN_1ST_HALF_OVER05}</code>\n"
                            f"• <b>Время сигнала:</b> за <code>{ALERT_MINUTES_BEFORE}</code> минут до матча\n"
                            f"• <b>Часовой пояс:</b> МСК (GMT+3)\n\n"
                            f"Бот непрерывно мониторит расписание. Сигналы приходят автоматически!"
                        )
                        send_message(chat_id, info, reply_markup=get_main_keyboard())
        except Exception as e:
            print(f"[!] Ошибка polling: {e}")
            time.sleep(3)

if __name__ == '__main__':
    if BOT_TOKEN == "ВАШ_ТОКЕН_ОТ_BOTFATHER" or not BOT_TOKEN:
        print("\n" + "="*60)
        print(" ОШИБКА: Не указан BOT_TOKEN!")
        print(" 1. Откройте в Telegram бота @BotFather")
        print(" 2. Отправьте /newbot, введите имя и получите токен (напр. 1234567:ABC-DEF...)")
        print(" 3. Вставьте его в tg_bot.py в строку BOT_TOKEN = '...'")
        print("="*60 + "\n")
        # Но скрипт не падает, а подсказывает пользователю
    
    # Запускаем фоновый мониторинг сигналов за 30 мин
    t_monitor = threading.Thread(target=background_signal_monitor, daemon=True)
    t_monitor.start()
    
    # Запускаем прием сообщений
    start_bot_polling()
