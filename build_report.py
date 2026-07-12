import os
import time
import re
import json
import html
import urllib.parse
import requests
import yfinance as yf
import feedparser
from datetime import datetime
from google import genai

# --- 환경 변수 ---
GEMINI_KEYS = [os.environ["GEMINI_API_KEY"]]
for i in range(2, 10):
    key = os.environ.get(f"GEMINI_API_KEY_{i}")
    if key:
        GEMINI_KEYS.append(key)

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

current_key_idx = 0
client = genai.Client(api_key=GEMINI_KEYS[current_key_idx])

def switch_to_next_key():
    global current_key_idx, client
    if current_key_idx + 1 < len(GEMINI_KEYS):
        current_key_idx += 1
        client = genai.Client(api_key=GEMINI_KEYS[current_key_idx])
        print(f"Switched to API key #{current_key_idx + 1}")
        return True
    return False

def call_gemini_translate(titles):
    if not titles: return []
    prompt = "다음 영어 뉴스 제목들을 한국어로 번역해주세요.\n각 제목을 순서대로 번역하고, 번역 결과만 한 줄씩 출력하세요.\n\n" + "\n".join(titles)
    global current_key_idx, client
    for attempt in range(3):
        try:
            resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            return resp.text.strip().split('\n')
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                if switch_to_next_key(): time.sleep(1)
                else: time.sleep(60)
            else: time.sleep(5)
    return []

def call_gemini_analyze(prompt):
    global current_key_idx, client
    for attempt in range(3):
        try:
            resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            text = resp.text.strip()
            if text.startswith("```"): text = text.split("```")[1].strip()
            if text.startswith("json"): text = text[4:].strip()
            return json.loads(text)
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                if switch_to_next_key(): time.sleep(1)
                else: time.sleep(60)
            else: time.sleep(5)
    return []

# --- 유틸리티 함수들 ---
def get_date_and_weather(show_weather=True):
    today = datetime.now().strftime("%Y년 %m월 %d일")
    if not show_weather:
        return f"📅 {today}"
    weather_info = "서울 날씨 정보를 가져올 수 없습니다."
    try:
        resp = requests.get("https://wttr.in/Seoul?format=j1", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            cur = data['current_condition'][0]
            tw = data['weather'][0]
            weather_info = (f"서울 날씨: {cur['weatherDesc'][0]['value']} | 현재 {cur['temp_C']}°C | "
                            f"최저 {tw['mintempC']}°C | 최고 {tw['maxtempC']}°C | "
                            f"강수량 {tw['hourly'][0].get('precipMM','0.0')}mm | 습도 {cur['humidity']}%")
    except Exception as e: print(f"Weather error: {e}")
    return f"📅 {today}\n{weather_info}"

def get_market_indicators():
    def safe_yf(symbol):
        try:
            t = yf.Ticker(symbol)
            h = t.history(period="5d")
            valid = h['Close'].dropna()
            if len(valid) >= 2:
                prev = valid.iloc[-2]
                last = valid.iloc[-1]
                change = ((last - prev) / prev) * 100
                return f"{last:,.2f} ({change:+.2f}%)"
            elif len(valid) == 1:
                return f"{valid.iloc[-1]:,.2f}"
            return "정보 없음"
        except Exception as e:
            print(f"yfinance error for {symbol}: {e}")
            return "정보 없음"

    try: krw = f"{requests.get('https://api.exchangerate-api.com/v4/latest/USD', timeout=5).json()['rates']['KRW']:,.2f}"
    except: krw = "정보 없음"

    return ("📊 주요 금융 지표\n"
            f"- USD/KRW: {krw} 원\n"
            f"- KOSPI: {safe_yf('^KS11')}\n"
            f"- NASDAQ: {safe_yf('^IXIC')}\n"
            f"- Google: {safe_yf('GOOGL')}\n"
            f"- Apple: {safe_yf('AAPL')}\n"
            f"- Microsoft: {safe_yf('MSFT')}\n"
            f"- QQQM: {safe_yf('QQQM')}")

def fetch_news_naver(query, max_results=30):
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET: return []
    try:
        headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
        params = {"query": query, "display": min(max_results, 100), "start": 1, "sort": "date"}
        resp = requests.get("https://openapi.naver.com/v1/search/news.json", headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            return [{"title": re.sub(r'<.*?>', '', item['title']), "url": item.get('originallink') or item['link']}
                    for item in resp.json().get("items", [])]
    except Exception as e: print(f"Naver error '{query}': {e}")
    return []

def fetch_news_google_keywords(keywords, max_results=30, region='global'):
    articles = []
    per_kw = max_results // len(keywords) if keywords else 0
    for kw in keywords:
        try:
            encoded = urllib.parse.quote(kw)
            url = (f"https://news.google.com/rss/search?q={encoded}&hl={'ko' if region=='kr' else 'en'}&"
                   f"gl={'KR' if region=='kr' else 'US'}&ceid={'KR:ko' if region=='kr' else 'US:en'}")
            feed = feedparser.parse(url)
            for entry in feed.entries[:per_kw]:
                articles.append({"title": entry.title, "url": entry.link})
        except Exception as e: print(f"Google error '{kw}': {e}")
    return articles

def select_important_articles(articles, top_n, context=""):
    if not articles:
        return []
    prompt = (
        f"당신은 바쁜 전문가를 위한 뉴스 큐레이터입니다. 다음은 오늘의 {context} 뉴스 제목 목록입니다.\n"
        f"이 중에서 **가장 중요하고 영향력 있는 기사 {top_n}개**를 선택해주세요.\n"
        f"중요도는 시장에 미치는 영향, 사회적 파장, 정책적 중요성, 혹은 광범위한 대중의 관심을 기준으로 판단하세요.\n"
        f"선택한 기사의 번호만 JSON 배열로 반환하세요. 예: [3, 7, 15]\n\n"
    )
    for i, a in enumerate(articles):
        prompt += f"{i+1}. {a['title']}\n"

    ids = call_gemini_analyze(prompt)
    if ids and isinstance(ids, list):
        return [articles[i-1] for i in ids if 1 <= i <= len(articles)][:top_n]
    return articles[:top_n]

def translate_selected_articles(article_lists):
    all_eng, mapping = [], []
    for lst in article_lists:
        for i, a in enumerate(lst):
            if not re.search(r'[가-힣]', a['title']):
                all_eng.append(a['title'])
                mapping.append((lst, i))
    if not all_eng: return
    translated = call_gemini_translate(all_eng)
    for (lst, idx), tr in zip(mapping, translated):
        if tr: lst[idx]['translated_title'] = tr

def format_articles(articles, max_display):
    if not articles: return "관련 뉴스가 없습니다.\n"
    lines = []
    for a in articles[:max_display]:
        title = a.get('translated_title', a['title'])
        safe_title = html.escape(html.unescape(title), quote=True)
        safe_url = a['url'].replace("&", "&amp;")
        lines.append(f"- <a href=\"{safe_url}\">{safe_title}</a>")
    return "\n".join(lines) + "\n"

# ===== 메인 실행 =====
if __name__ == "__main__":
    now_utc = datetime.utcnow().hour
    show_weather = (now_utc >= 20 or now_utc <= 1)   # 오전 6시(UTC 21시)는 날씨 포함

    report = get_date_and_weather(show_weather) + "\n\n" + get_market_indicators() + "\n\n"

    # 국내 증시
    domestic_raw = fetch_news_naver("증시", 30)
    domestic = select_important_articles(domestic_raw, 7, "국내 증시")

    # 해외 증시
    us_raw = fetch_news_google_keywords(
        ["stock market", "Federal Reserve", "S&P 500", "NASDAQ", "earnings", "AI stocks", "tech stocks"],
        max_results=30, region='global'
    )
    us = select_important_articles(us_raw, 7, "해외 증시")

    # 정치/사회
    politics_raw = fetch_news_naver("정치", 30)
    society_raw = fetch_news_naver("사회", 30)
    combined = politics_raw + society_raw
    dom_news = select_important_articles(combined, 6, "국내 정치/사회")

    # 번역
    translate_selected_articles([us])

    # 최종 리포트 조합
    report += "🇰🇷 국내 증시\n" + format_articles(domestic, 7)
    report += "\n🇺🇸 해외 증시\n" + format_articles(us, 7)
    report += "\n📰 정치/사회\n" + format_articles(dom_news, 6)

    # 파일로 저장
    with open("report.txt", "w", encoding="utf-8") as f:
        f.write(report)
    print("Report saved to report.txt")
