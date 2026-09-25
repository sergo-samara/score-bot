import urllib.request
import re
import time
import concurrent.futures
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
}

def fetch_html(url, timeout=10):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='ignore')

def get_match_detailed_stats(match_url):
    """
    Scrapes detailed stats from match page:
    1st half over 0.5 for last 20 matches for Team 1 and Team 2 (and home/away stats).
    """
    stats = {
        't1_over05_last20': None,
        't1_under05_last20': None,
        't2_over05_last20': None,
        't2_under05_last20': None,
        't1_home_over05': None,
        't2_away_over05': None
    }
    
    if not match_url:
        return stats
        
    full_url = f"https://24score.pro{match_url}" if not match_url.startswith('http') else match_url
    
    try:
        html = fetch_html(full_url)
        soup = BeautifulSoup(html, 'html.parser')
        
        # Search for 1st half 0.5 goals in last 20 block
        for d in soup.find_all(lambda tag: tag.has_attr('class') and 'data_h2h_last20' in tag['class']):
            for tr in d.find_all('tr'):
                if 'Первый тайм' in tr.get_text():
                    tds = tr.find_all('td')
                    if len(tds) >= 6:
                        t1_05_div = tds[0].find('div', class_=lambda c: c and '05' in c)
                        t2_05_div = tds[5].find('div', class_=lambda c: c and '05' in c)
                        
                        if t1_05_div:
                            vals = [v.get_text(strip=True) for v in t1_05_div.find_all('div', class_='ou-data')]
                            if len(vals) >= 2 and vals[0].isdigit():
                                stats['t1_over05_last20'] = int(vals[0])
                                stats['t1_under05_last20'] = int(vals[1])
                            elif len(vals) >= 1 and vals[0].isdigit():
                                stats['t1_over05_last20'] = int(vals[0])
                        
                        if t2_05_div:
                            vals = [v.get_text(strip=True) for v in t2_05_div.find_all('div', class_='ou-data')]
                            if len(vals) >= 2 and vals[0].isdigit():
                                stats['t2_over05_last20'] = int(vals[0])
                                stats['t2_under05_last20'] = int(vals[1])
                            elif len(vals) >= 1 and vals[0].isdigit():
                                stats['t2_over05_last20'] = int(vals[0])
                        
                        # Home / Away columns
                        if len(tds) >= 8:
                            t1_home_div = tds[2].find('div', class_=lambda c: c and '05' in c)
                            t2_away_div = tds[7].find('div', class_=lambda c: c and '05' in c)
                            if t1_home_div:
                                vals = [v.get_text(strip=True) for v in t1_home_div.find_all('div', class_='ou-data')]
                                if vals and vals[0].isdigit():
                                    stats['t1_home_over05'] = int(vals[0])
                            if t2_away_div:
                                vals = [v.get_text(strip=True) for v in t2_away_div.find_all('div', class_='ou-data')]
                                if vals and vals[0].isdigit():
                                    stats['t2_away_over05'] = int(vals[0])
                                    
                        return stats
    except Exception as e:
        print(f"Error parsing stats for {match_url}: {e}")
        
    return stats

def scan_all_matches(date_str=None, min_draw=5.0, min_over05=15, time_offset=3):
    """
    Scans upcoming matches from 24score.pro:
    - Filters by Draw odds >= min_draw (default 5.0)
    - Checks detailed match page in parallel for 1st half >0.5 goals >= min_over05 (default 15/20) for any team
    - Returns structured list of matching games
    """
    url = f'https://24score.pro/football/?time_offset={time_offset}'
    if date_str:
        url += f'&date={date_str}'
    
    html = fetch_html(url)
    soup = BeautifulSoup(html, 'html.parser')
    
    table = soup.find('table', class_='daymatches')
    if not table:
        return []
    
    current_league = "Футбол"
    candidates = []
    
    for row in table.find_all('tr'):
        classes = row.get('class', [])
        if not classes:
            th = row.find(['th', 'td'])
            if th:
                raw_text = th.get_text(strip=True)
                m = re.match(r'^([^0-9]+)', raw_text)
                if m:
                    current_league = m.group(1).strip()
            continue
            
        if 'hidden' in classes or 'null' in classes:
            continue
            
        tds = row.find_all('td')
        if len(tds) < 8:
            continue
            
        # Match time
        time_td = row.find('td', class_='time')
        if not time_td:
            continue
        time_str = time_td.contents[0].strip() if time_td.contents else ""
        if not re.match(r'^\d{1,2}:\d{2}$', time_str):
            continue
            
        # Match ID
        match_id_span = time_td.find('span')
        match_id = match_id_span.get_text(strip=True) if match_id_span else ""
        
        # Status / score (must be upcoming)
        score_td = row.find('td', class_='score')
        score_text = score_td.get_text(strip=True) if score_td else ""
        if '—' not in score_text and score_text != '':
            continue
            
        # Teams
        team_tds = row.find_all('td', class_='team')
        if len(team_tds) >= 2:
            team1 = team_tds[0].get_text(strip=True)
            team2 = team_tds[1].get_text(strip=True)
        else:
            continue
            
        # 1X2 odds
        odds_1x2 = row.find_all('td', class_=lambda c: c and 'odds_1x2' in c)
        if len(odds_1x2) < 3:
            continue
            
        p1_str = odds_1x2[0].get_text(strip=True)
        draw_str = odds_1x2[1].get_text(strip=True)
        p2_str = odds_1x2[2].get_text(strip=True)
        
        try:
            draw_odds = float(draw_str.replace(',', '.'))
        except ValueError:
            continue
            
        # Check condition 1: кэф на ничью >= min_draw
        if draw_odds >= min_draw:
            match_link = row.find('a', href=lambda h: h and '/football/match/' in h)
            match_url = match_link['href'] if match_link else ""
            
            candidates.append({
                'id': match_id,
                'time': time_str,
                'date': date_str or datetime.now().strftime('%Y-%m-%d'),
                'league': current_league,
                'team1': team1,
                'team2': team2,
                'draw_odds': draw_odds,
                'p1_odds': p1_str,
                'p2_odds': p2_str,
                'match_url': match_url,
                'full_url': f"https://24score.pro{match_url}"
            })
            
    if not candidates:
        return []
        
    # Fetch candidate details in parallel with ThreadPoolExecutor
    def fetch_cand_stats(cand):
        stats = get_match_detailed_stats(cand['match_url'])
        cand.update(stats)
        t1_val = cand['t1_over05_last20']
        t2_val = cand['t2_over05_last20']
        
        t1_passes = t1_val is not None and t1_val >= min_over05
        t2_passes = t2_val is not None and t2_val >= min_over05
        cand['t1_passes'] = t1_passes
        cand['t2_passes'] = t2_passes
        cand['matches_criteria'] = t1_passes or t2_passes
        return cand
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        evaluated_candidates = list(executor.map(fetch_cand_stats, candidates))
        
    results = [c for c in evaluated_candidates if c['matches_criteria']]
    results.sort(key=lambda x: x['time'])
    return results
