#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
24SCORE.PRO — TELEGRAM-БОТ ДЛЯ ФУТБОЛЬНЫХ СИГНАЛОВ
Готов к работе на Render.com (Free Web Service)
"""

import sys
import os
import time
import json
import urllib.request
import urllib.parse
import re
import threading
import concurrent.futures
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

# ==================== НАСТРОЙКИ БОТА ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "6799385620:AAEjPUtNMDORMSbW4uEaR0TNaDDrS8PMroc")

MIN_DRAW_ODDS = 5.0          # Кэф на ничью >= 5.0
MIN_1ST_HALF_OVER05 = 15     # Голов в 1-м тайме > 0.5 >= 15 из 20 матчей
ALERT_MINUTES_BEFORE = 30    # Сигнал за 30 минут до матча
TIMEZONE_OFFSET = 3          # GMT+3 (Москва)
CHECK_INTERVAL_SEC = 30      # Интервал проверки времени (секунды)
# ========================================================

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
}

DOMAINS = ['https://24score.pro', 'https://en.24score.com']

subscribers = set()
alerted_matches = set()
cached_matches = []
cache_time = 0

def fetch_html(url_path):
    for dom in DOMAINS:
        try:
            req = urllib.request.Request(dom + url_path, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read().decode('utf-8', errors='ignore'), dom
        except Exception:
            continue
    return None, None

def get_match_detailed_stats(base_dom, match_url):
    stats = {'t1_over05': None, 't2_over05': None}
    if not match_url: return stats
    full_url = f"{base_dom}{match_url}"
    try:
        req = urllib.request.Request(full_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        soup = BeautifulSoup(html, 'html.parser')
        for d in soup.find_all(lambda tag: tag.has_attr('class') and 'data_h2h_last20' in tag['class']):
            for tr in d.find_all('tr'):
                if 'Первый тайм' in tr.get_text() or '1st half' in tr.get_text().lower():
                    tds = tr.find_all('td')
                    if len(tds) >= 6:
                        t1_05 = tds[0].find('div', class_=lambda c: c and '05' in c)
                        t2_05 = tds[5].find('div', class_=lambda c: c and '05' in c)
                        if t1_05:
                            v = t1_05.find('div', class_='ou-data')
                            if v and v.get_text(strip=True).isdigit():
                                stats['t1_over05'] = int(v.get_text(strip=True))
                        if t2_05:
                            v = t2_05.find('div', class_='ou-data')
                            if v and v.get_text(strip=True).isdigit():
                                stats['t2_over05'] = int(v.get_text(strip=True))
                        return stats
    except Exception:
        pass
    return stats

def scan_matches(date_str=None, min_draw=MIN_DRAW_ODDS, min_over05=MIN_1ST_HALF_OVER05, tz=TIMEZONE_OFFSET):
    path = f'/football/?time_offset={tz}'
    if date_str: path += f'&date={date_str}'
    html, base_dom = fetch_html(path)
    if not html: return []
    
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table', class_='daymatches')
    if not table: return []
    
    current_league = "Футбол"
    candidates = []
    
    for row in table.find_all('tr'):
        classes = row.get('class', [])
        if not classes:
            th = row.find(['th', 'td'])
            if th:
                raw_text = th.get_text(strip=True)
                m = re.match(r'^([^0-9]+)', raw_text)
                if m: current_league = m.group(1).strip()
            continue
        if 'hidden' in classes or 'null' in classes: continue
        tds = row.find_all('td')
        if len(tds) < 8: continue
        
        time_td = row.find('td', class_='time')
        if not time_td: continue
        time_str = time_td.contents[0].strip() if time_td.contents else ""
        if not re.match(r'^\d{1,2}:\d{2}$', time_str): continue
        
        match_id_span = time_td.find('span')
        match_id = match_id_span.get_text(strip=True) if match_id_span else ""
        
        score_td = row.find('td', class_='score')
        score_text = score_td.get_text(strip=True) if score_td else ""
        if '—' not in score_text and score_text != '': continue
        
        team_tds = row.find_all('td', class_='team')
        if len(team_tds) >= 2:
            team1 = team_tds[0].get_text(strip=True)
            team2 = team_tds[1].get_text(strip=True)
        else: continue
        
        odds_1x2 = row.find_all('td', class_=lambda c: c and 'odds_1x2' in c)
        if len(odds_1x2) < 3: continue
        draw_str = odds_1x2[1].get_text(strip=True)
        try:
            draw_odds = float(draw_str.replace(',', '.'))
        except ValueError: continue
        
        if draw_odds >= min_draw:
            match_link = row.find('a', href=lambda h: h and '/football/match/' in h)
            match_url = match_link['href'] if match_link else ""
            candidates.append({
                'id': match_id,
                'time': time_str,
                'date': date_str or (datetime.now(timezone.utc) + timedelta(hours=tz)).strftime('%Y-%m-%d'),
                'league': current_league,
                'team1': team1,
                'team2': team2,
                'draw_odds': draw_odds,
                'full_url': f"{base_dom}{match_url}",
                'match_url': match_url,
                'base_dom': base_dom
            })
            
    if not candidates: return []
    
    def check_cand(c):
        stats = get_match_detailed_stats(c['base_dom'], c['match_url'])
        c.update(stats)
        t1_val = c.get('t1_over05')
        t2_val = c.get('t2_over05')
        passes = (t1_val is not None and t1_val >= min_over05) or (t2_val is not None and t2_val >= min_over05)
        c['passes'] = passes
        return c
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        evaluated = list(executor.map(check_cand, candidates))
        
    passed = [c for c in evaluated if c['passes']]
    passed.sort(key=lambda x: x['time'])
    return passed

def calculate_minutes_to_match(match_time_str, match_date_str, tz=TIMEZONE_OFFSET):
    try:
        now = datetime.now(timezone.utc) + timedelta(hours=tz)
        now_naive = now.replace(tzinfo=None)
        match_dt = datetime.strptime(f"{match_date_str} {match_time_str}", "%Y-%m-%d %H:%M")
        diff = (match_dt - now_naive).total_seconds()
        return int(diff // 60)
    except Exception:
        return 9999

# ================= TELEGRAM API =================
def tg_request(method, data=None):
    token = os.environ.get("BOT_TOKEN", BOT_TOKEN)
    if not token or token == "ВАШ_ТОКЕН_ОТ_BOTFATHER": return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        if data:
            req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
        else:
            req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None

def send_message(chat_id, text, reply_markup=None):
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': False}
    if reply_markup: payload['reply_markup'] = reply_markup
    return tg_request('sendMessage', payload)

def get_main_keyboard():
    return {
        'keyboard': [
            [{'text': '⚽ Матчи на сегодня'}, {'text': '📅 Матчи на завтра'}],
            [{'text': '🔄 Обновить сейчас'}, {'text': '⚙️ Мои критерии'}]
        ],
        'resize_keyboard': True
    }

def format_card(m, is_alert=False):
    mins = calculate_minutes_to_match(m['time'], m['date'])
    if mins < 0:
        badge = "<i>(идёт / завершён)</i>"
    elif mins <= ALERT_MINUTES_BEFORE:
        badge = f"<b>🚨 СИГНАЛ: через {mins} мин!</b>"
    else:
        h = mins // 60
        mn = mins % 60
        badge = f"<i>(через {h}ч {mn}м)</i>"

    t1_pass = m.get('t1_over05') and m['t1_over05'] >= MIN_1ST_HALF_OVER05
    t2_pass = m.get('t2_over05') and m['t2_over05'] >= MIN_1ST_HALF_OVER05

    return (
        f"🏆 <b>{m['league']}</b>\n"
        f"⏰ <b>{m['time']}</b> МСК {badge}\n"
        f"⚽ <b>{m['team1']}</b> — <b>{m['team2']}</b>\n\n"
        f"📊 <b>Кэф на Ничью:</b> <code>X = {m['draw_odds']}</code>\n"
        f"📈 <b>1-й тайм (ТБ 0.5 за 20 матчей):</b>\n"
        f"  • {m['team1']}: <b>{m.get('t1_over05', '—')}/20</b> {'✅' if t1_pass else ''}\n"
        f"  • {m['team2']}: <b>{m.get('t2_over05', '—')}/20</b> {'✅' if t2_pass else ''}\n"
        f"🔗 <a href=\"{m['full_url']}\">Открыть на 24score</a>\n"
    )

def background_monitor():
    print("[*] Фоновый монитор сигналов (за 30 мин) запущен...")
    while True:
        try:
            token = os.environ.get("BOT_TOKEN", BOT_TOKEN)
            if subscribers and token and token != "ВАШ_ТОКЕН_ОТ_BOTFATHER":
                matches = scan_matches()
                for m in matches:
                    mins = calculate_minutes_to_match(m['time'], m['date'])
                    if 0 <= mins <= ALERT_MINUTES_BEFORE:
                        key = f"{m['id']}_{m['time']}_{m['date']}"
                        if key not in alerted_matches:
                            alerted_matches.add(key)
                            text = f"🚨🚨🚨 <b>СИГНАЛ ЗА {mins} МИНУТ ДО МАТЧА!</b> 🚨🚨🚨\n\n{format_card(m, True)}"
                            for uid in list(subscribers):
                                send_message(uid, text, get_main_keyboard())
        except Exception as e:
            print(f"Ошибка монитора: {e}")
        time.sleep(CHECK_INTERVAL_SEC)

def start_bot():
    global subscribers
    offset = 0
    print("[*] Бот успешно запущен и слушает Telegram...")
    while True:
        try:
            res = tg_request('getUpdates', {'offset': offset, 'timeout': 20})
            if res and res.get('ok'):
                for update in res.get('result', []):
                    offset = update['update_id'] + 1
                    msg = update.get('message')
                    if not msg or 'text' not in msg: continue
                    chat_id = msg['chat']['id']
                    text = msg['text'].strip()
                    subscribers.add(chat_id)

                    if text == '/start':
                        welcome = (
                            f"👋 <b>Добро пожаловать в 24score Alert Bot!</b>\n\n"
                            f"Критерии отбора:\n"
                            f"1. Ничья (Х) &ge; {MIN_DRAW_ODDS}\n"
                            f"2. 1-й тайм (ТБ 0.5 за 20 матчей) &ge; {MIN_1ST_HALF_OVER05} у любой команды\n"
                            f"3. Сигнал ровно за <b>30 минут</b> до матча со звуком!\n\n"
                            f"Нажмите кнопку ниже:"
                        )
                        send_message(chat_id, welcome, get_main_keyboard())
                    elif 'Матчи на сегодня' in text or 'Обновить' in text:
                        send_message(chat_id, "🔍 <i>Сканирую 24score...</i>")
                        now_msk = (datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)).strftime('%Y-%m-%d')
                        m_list = scan_matches(now_msk)
                        if not m_list:
                            send_message(chat_id, "ℹ️ На сегодня подходящих матчей пока нет.", get_main_keyboard())
                        else:
                            send_message(chat_id, f"⚽ <b>Найдено матчей: {len(m_list)}</b>")
                            for m in m_list:
                                send_message(chat_id, format_card(m), get_main_keyboard())
                                time.sleep(0.3)
                    elif 'Матчи на завтра' in text:
                        send_message(chat_id, "🔍 <i>Сканирую 24score на завтра...</i>")
                        tm_msk = (datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET) + timedelta(days=1)).strftime('%Y-%m-%d')
                        m_list = scan_matches(tm_msk)
                        if not m_list:
                            send_message(chat_id, "ℹ️ На завтра подходящих матчей пока нет.", get_main_keyboard())
                        else:
                            send_message(chat_id, f"⚽ <b>Найдено матчей на завтра: {len(m_list)}</b>")
                            for m in m_list:
                                send_message(chat_id, format_card(m), get_main_keyboard())
                                time.sleep(0.3)
                    elif 'Мои критерии' in text:
                        send_message(chat_id, f"⚙️ Критерии: X &ge; {MIN_DRAW_ODDS}, 1-й тайм >0.5 &ge; {MIN_1ST_HALF_OVER05}/20. Сигнал за 30 мин.", get_main_keyboard())
        except Exception:
            time.sleep(3)

# HTTP сервер для прохождения проверки портов Render.com
class RenderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"24Score Telegram Bot is LIVE and RUNNING!")
    def log_message(self, format, *args):
        pass

if __name__ == '__main__':
    # 1. СРАЗУ открываем порт 10000 для Render.com
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), RenderHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"===> Port {port} opened successfully for Render!")

    # 2. Запускаем фоновый мониторинг сигналов за 30 мин
    threading.Thread(target=background_monitor, daemon=True).start()

    # 3. Запускаем Telegram бота
    start_bot()


