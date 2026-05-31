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

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

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
    """최대 20개의 영어 제목을 한 번에 번역"""
    global current_key_idx, client
    if not titles:
        return []
    prompt = (
        "다음 영어 뉴스 제목들을 한국어로 번역해주세요.\n"
        "각 제목을 순서대로 번역하고, 번역 결과만 한 줄씩 출력하세요.\n"
        "다른 설명은 일절 추가하지 마세요.\n\n" + "\n".join(titles)
    )
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            return response.text.strip().split('\n')
        except Exception as e:
            print(f"Gemini translation error: {e}")
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                if switch_to_next_key():
                    time.sleep(1)
                else:
                    print("All keys exhausted, waiting 60s...")
                    time.sleep(60)
            else:
                time.sleep(5)
    return []

def call_gemini_analyze(prompt):
    """Gemini 분석 요청 (JSON 응답 파싱)"""
    global current_key_idx, client
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("```")[1].strip()
                if text.startswith("json"):
                    text = text[4:].strip()
            return json.loads(text)
        except Exception as e:
            print(f"Gemini analyze error: {e}")
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                if switch_to_next_key():
                    time.sleep(1)
                else:
                    print("All keys exhausted, waiting 60s...")
                    time.sleep(60)
            else:
                time.sleep(5)
    return []

# --- 유틸리티 함수들 ---
def get_date_and_weather():
    today = datetime.now().strftime("%Y년 %m월 %d일")
    weather_info = "서울 날씨 정보를 가져올 수 없습니다."
    try:
        resp = requests.get("https://wttr.in/Seoul?format=j1", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            current = data['current_condition'][0]
            today_weather = data['weather'][0]
            weather_info = (
                f"서울 날씨: {current['weatherDesc'][0]['value']} | "
                f"현재 {current['temp_C']}°C | "
                f"최저 {today_weather['mintempC']}°C | "
                f"최고 {today_weather['maxtempC']}°C | "
                f"강수량 {today_weather['hourly'][0].get('precipMM', '0.0')}mm | "
                f"습도 {current['humidity']}%"
            )
    except Exception as e:
        print(f"Weather error: {e}")
    return f"📅 {today}\n{weather_info}"

def get_market_indicators():
    def safe_yf(symbol):
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="2d")
            if len(hist) >= 2:
                prev = hist['Close'].iloc[-2]
                last = hist['Close'].iloc[-1]
                change = ((last - prev) / prev) * 100
                return f"{last:,.2f} ({change:+.2f}%)"
            elif len(hist) == 1:
                return f"{hist['Close'].iloc[-1]:,.2f}"
            else:
                return "정보 없음"
        except:
            return "정보 없음"

    kospi_str = safe_yf("^KS11")
    kosdaq_str = safe_yf("^KQ11")
    sp500_str = safe_yf("^GSPC")
    nasdaq_str = safe_yf("^IXIC")
    dow_str = safe_yf("^DJI")

    try:
        resp = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        usd_krw = resp.json()['rates']['KRW']
        krw_str = f"{usd_krw:,.2f}"
    except:
        krw_str = "정보 없음"

    return (
        "📊 주요 금융 지표\n"
        f"- KOSPI: {kospi_str}\n"
        f"- KOSDAQ: {kosdaq_str}\n"
        f"- USD/KRW: {krw_str} 원\n"
        f"- S&P 500: {sp500_str}\n"
        f"- NASDAQ: {nasdaq_str}\n"
        f"- Dow Jones: {dow_str}"
    )

def get_trending_keywords():
    """실시간 급상승 검색어 Top 5 (signal.bz, 실패 시 폴백)"""
    try:
        resp = requests.get("https://api.signal.bz/news/realtime", headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            keywords = [item['keyword'] for item in data.get('result', [])[:5]]
            if keywords:
                return keywords
    except Exception as e:
        print(f"Trending keywords error: {e}")
    # 폴백 키워드
    return ["정치", "경제", "사회", "세계", "IT"]

def fetch_news_naver(query, max_results=10):
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return []
    try:
        headers = {
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
        }
        params = {
            "query": query,
            "display": min(max_results, 100),
            "start": 1,
            "sort": "date"
        }
        resp = requests.get("https://openapi.naver.com/v1/search/news.json", headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            articles = []
            for item in data.get("items", []):
                link = item.get("originallink") or item.get("link")
                title = re.sub(r'<.*?>', '', item["title"])
                articles.append({"title": title, "url": link})
            return articles
    except Exception as e:
        print(f"Naver API error for '{query}': {e}")
    return []

def fetch_news_google_keywords(keywords, max_results=30, region='global'):
    articles = []
    per_kw = max_results // len(keywords) if keywords else 0
    for kw in keywords:
        try:
            encoded_kw = urllib.parse.quote(kw)
            if region == 'kr':
                url = f"https://news.google.com/rss/search?q={encoded_kw}&hl=ko&gl=KR&ceid=KR:ko"
            else:
                url = f"https://news.google.com/rss/search?q={encoded_kw}&hl=en&gl=US&ceid=US:en"
            feed = feedparser.parse(url)
            for entry in feed.entries[:per_kw]:
                articles.append({"title": entry.title, "url": entry.link})
        except Exception as e:
            print(f"Google News error for '{kw}': {e}")
    return articles

def translate_selected_articles(article_lists):
    """여러 리스트의 기사 중 영어 제목을 한 번에 번역"""
    all_eng = []
    mapping = []
    for lst in article_lists:
        for i, a in enumerate(lst):
            if not re.search(r'[가-힣]', a['title']):
                all_eng.append(a['title'])
                mapping.append((lst, i))
    if not all_eng:
        return
    translated = call_gemini_translate(all_eng)
    for (lst, idx), tr_title in zip(mapping, translated):
        if tr_title:
            lst[idx]['translated_title'] = tr_title

def format_articles(articles, max_display):
    """기사 리스트를 HTML 하이퍼링크로 포맷팅 (URL 숨김)"""
    if not articles:
        return "관련 뉴스가 없습니다.\n"
    lines = []
    for a in articles[:max_display]:
        title = a.get('translated_title', a['title'])
        escaped_title = html.escape(title)
        lines.append(f"- <a href=\"{a['url']}\">{escaped_title}</a>")
    return "\n".join(lines) + "\n"

def send_telegram(text):
    """텔레그램 메시지 전송 (HTML 파싱 모드)"""
    max_len = 3500
    chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)] if len(text) > max_len else [text]
    for idx, chunk in enumerate(chunks):
        if len(chunks) > 1:
            chunk = f"[{idx+1}/{len(chunks)}]\n{chunk}"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML"
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                # HTML 파싱 오류 시 일반 텍스트로 재시도
                payload.pop("parse_mode")
                requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"Telegram send error: {e}")
    print("All chunks sent successfully.")

# ===== 메인 실행 =====
if __name__ == "__main__":
    # 헤더
    report = get_date_and_weather() + "\n\n" + get_market_indicators() + "\n\n"

    # 1. 실시간 검색어 Top 5 + 관련 뉴스 1건
    trending = get_trending_keywords()
    trend_articles = []  # 번역 대상에 포함시키기 위해 수집
    if trending:
        report += "🔥 실시간 검색어 Top 5 관련 뉴스\n"
        for idx, keyword in enumerate(trending):
            articles = fetch_news_naver(keyword, 1)
            if articles:
                trend_articles.append(articles[0])
                title = articles[0]['title']
                report += f"{idx+1}. {keyword} - {title}: {articles[0]['url']}\n"
            else:
                report += f"{idx+1}. {keyword} - 관련 뉴스 없음\n"
        report += "\n"

    # 2. 정치 (30개 수집 후 Gemini가 중요도 3개 선별)
    politics_raw = fetch_news_naver("정치", 30)
    if politics_raw:
        titles_str = ""
        for i, a in enumerate(politics_raw):
            titles_str += f"{i+1}. {a['title']}\n"
        prompt = f"""다음은 오늘 정치 뉴스 제목 목록입니다.
이 중에서 **대한민국 국민에게 가장 중요하다고 생각되는 기사 3개**를 골라주세요.
선택한 기사의 번호만 JSON 배열로 반환하세요. 예: [3, 7, 15]

기사 목록:
{titles_str}"""
        selected_ids = call_gemini_analyze(prompt)
        if selected_ids and isinstance(selected_ids, list):
            politics = [politics_raw[i-1] for i in selected_ids if 1 <= i <= len(politics_raw)][:3]
        else:
            politics = politics_raw[:3]
    else:
        politics = []

    # 3. 증시 (네이버 4건)
    stocks = fetch_news_naver("증시", 10)[:4]

    # 4. 미국 주식 (구글 뉴스 5건)
    us_stocks = fetch_news_google_keywords(
        ["stock market", "Federal Reserve", "S&P 500", "NASDAQ", "earnings", "tech stocks"],
        max_results=30, region='global'
    )[:5]

    # 5. 국제 정세 (구글 뉴스 3건)
    world = fetch_news_google_keywords(
        ["world news", "geopolitics", "IMF", "United Nations", "NATO"],
        max_results=15, region='global'
    )[:3]

    # 번역: 미국 주식, 국제 정세, 트렌드 뉴스(영어일 경우)를 한 번에
    translate_selected_articles([us_stocks, world, trend_articles])

    # 각 섹션 포맷
    report += "🇰🇷 정치\n" + format_articles(politics, 3)
    report += "\n🇰🇷 증시\n" + format_articles(stocks, 4)
    report += "\n🇺🇸 미국 주식\n" + format_articles(us_stocks, 5)
    report += "\n🌍 국제 정세\n" + format_articles(world, 3)

    print(f"Report length: {len(report)}")
    send_telegram(report)
    print("Script finished.")
