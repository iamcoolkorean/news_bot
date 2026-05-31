import os
import time
import re
import json
import requests
import yfinance as yf
import feedparser
from datetime import datetime
from ddgs import DDGS
from ddgs import DDGSException
from google import genai

# --- 환경 변수 ---
GEMINI_KEYS = [os.environ["GEMINI_API_KEY"]]
for i in range(2, 10):
    key = os.environ.get(f"GEMINI_API_KEY_{i}")
    if key:
        GEMINI_KEYS.append(key)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# 네이버 API 키 (없으면 DDGS로 폴백)
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

def call_gemini_with_retry(prompt, max_retries=3):
    global current_key_idx, client
    last_error = None
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            return response.text.strip()
        except Exception as e:
            last_error = str(e)
            print(f"Gemini call failed: {last_error}")
            if "429" in last_error or "RESOURCE_EXHAUSTED" in last_error or "quota" in last_error.lower():
                if switch_to_next_key():
                    time.sleep(1)
                    continue
                else:
                    if attempt < max_retries - 1:
                        print(f"All keys exhausted. Waiting 60 seconds... (attempt {attempt+1}/{max_retries})")
                        time.sleep(60)
                        continue
            else:
                if attempt < max_retries - 1:
                    print(f"Retrying... (attempt {attempt+1}/{max_retries})")
                    time.sleep(5)
                    continue
    raise Exception(f"Gemini call failed after {max_retries} attempts: {last_error}")

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
    try:
        kospi = yf.Ticker("^KS11")
        kospi_hist = kospi.history(period="2d")
        kospi_str = "정보 없음"
        if len(kospi_hist) >= 2:
            prev_close = kospi_hist['Close'].iloc[-2]
            last_close = kospi_hist['Close'].iloc[-1]
            kospi_change = ((last_close - prev_close) / prev_close) * 100
            kospi_str = f"{last_close:,.2f} ({kospi_change:+.2f}%)"
        elif len(kospi_hist) == 1:
            last_close = kospi_hist['Close'].iloc[-1]
            kospi_str = f"{last_close:,.2f}"

        kosdaq = yf.Ticker("^KQ11")
        kosdaq_hist = kosdaq.history(period="2d")
        kosdaq_str = "정보 없음"
        if len(kosdaq_hist) >= 2:
            prev_close = kosdaq_hist['Close'].iloc[-2]
            last_close = kosdaq_hist['Close'].iloc[-1]
            kosdaq_change = ((last_close - prev_close) / prev_close) * 100
            kosdaq_str = f"{last_close:,.2f} ({kosdaq_change:+.2f}%)"
        elif len(kosdaq_hist) == 1:
            last_close = kosdaq_hist['Close'].iloc[-1]
            kosdaq_str = f"{last_close:,.2f}"

        resp = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        usd_krw = resp.json()['rates']['KRW']
        krw_str = f"{usd_krw:,.2f}"

        return (
            "📊 주요 금융 지표\n"
            f"- KOSPI: {kospi_str}\n"
            f"- KOSDAQ: {kosdaq_str}\n"
            f"- USD/KRW: {krw_str} 원"
        )
    except Exception as e:
        print(f"Market indicators error: {e}")
        return "📊 주요 금융 지표 정보를 가져올 수 없습니다."

def fetch_news_naver(query, max_results=50):
    """네이버 검색 API로 뉴스 검색 (최신순)"""
    articles = []
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return articles  # 키 없으면 빈 리스트 반환 → DDGS 폴백
    try:
        headers = {
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
        }
        # 한 번에 최대 100개까지 가능, 시작 위치 1, 정렬 date
        display = min(max_results, 100)
        params = {
            "query": query,
            "display": display,
            "start": 1,
            "sort": "date"
        }
        resp = requests.get("https://openapi.naver.com/v1/search/news.json", headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("items", []):
                # 네이버 뉴스 링크 또는 원문 링크 사용
                link = item.get("originallink") or item.get("link")
                title = re.sub(r'<.*?>', '', item["title"])  # 검색어 강조 태그 제거
                articles.append({"title": title, "url": link})
        else:
            print(f"Naver API error: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"Naver API request failed: {e}")
    return articles

def fetch_news_ddg(query, max_results=50, region='kr-kr', timelimit='d'):
    """
    DDGS 뉴스 검색 (예외 처리 포함)
    결과가 없거나 오류 발생 시 빈 리스트 반환
    """
    try:
        with DDGS() as ddgs:
            news = list(ddgs.news(query=query, max_results=max_results, region=region, timelimit=timelimit))
        return [{"title": item.get("title", ""), "url": item.get("url", "")} for item in news if item.get("url")]
    except (DDGSException, Exception) as e:
        print(f"DDG error for query '{query}': {e}")
        return []

def fetch_news_google(query, max_results=30):
    articles = []
    try:
        url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
        feed = feedparser.parse(url)
        for entry in feed.entries[:max_results]:
            articles.append({"title": entry.title, "url": entry.link})
    except Exception as e:
        print(f"Google News error for '{query}': {e}")
    return articles

def translate_titles(articles):
    eng_indices = [i for i, a in enumerate(articles) if not re.search(r'[가-힣]', a['title'])]
    if not eng_indices:
        return
    original_titles = [articles[i]['title'] for i in eng_indices]
    prompt = (
        "다음 영어 뉴스 제목들을 한국어로 번역해주세요.\n"
        "각 제목을 순서대로 번역하고, 번역 결과만 한 줄씩 출력하세요.\n"
        "다른 설명은 일절 추가하지 마세요.\n\n" + "\n".join(original_titles)
    )
    try:
        translated = call_gemini_with_retry(prompt).split('\n')
        for idx, i in enumerate(eng_indices):
            if idx < len(translated) and translated[idx].strip():
                articles[i]['translated_title'] = translated[idx].strip()
    except Exception as e:
        print(f"Translation error: {e}")

def analyze_category(category_name, articles, max_display=5):
    if not articles:
        return f"📌 {category_name}\n관련 뉴스가 없습니다.\n"

    titles = []
    for i, a in enumerate(articles):
        display_title = a.get('translated_title', a['title'])
        titles.append(f"{i+1}. {display_title}")

    titles_text = "\n".join(titles)

    if "증시" in category_name or "주식" in category_name:
        role = "당신은 주식 투자자에게 오늘의 핵심 이슈를 전달하는 애널리스트입니다."
        requirement = "실제로 주가에 영향을 미칠 만한 구체적인 이벤트(실적, 수급, 공시, 계약, 지수 변동 등)를 5가지 찾아주세요."
    elif "정치" in category_name:
        role = "당신은 정치부 기자입니다."
        requirement = "오늘 정치권에서 가장 중요하게 다뤄진 이슈 5가지를 찾아주세요."
    else:
        role = "당신은 뉴스 분석가입니다."
        requirement = "가장 많이 언급된 중요 주제 5개를 찾아주세요."

    prompt = f"""{role}
아래는 '{category_name}' 분야의 오늘 뉴스 제목 목록입니다.
{requirement}
각 토픽에는 해당하는 기사들의 **번호**를 모두 모아서, 반드시 아래 JSON 형식으로만 답변하세요.
다른 설명은 절대 쓰지 마세요.

[
  {{"topic": "토픽 제목 (구체적으로)", "article_ids": [1, 3, 5]}},
  ...
]

기사 목록:
{titles_text}"""

    try:
        text = call_gemini_with_retry(prompt)
        if text.startswith("```"):
            text = text.split("```")[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()
        data = json.loads(text)
    except Exception as e:
        print(f"Gemini analysis failed for {category_name}: {e}")
        fallback = f"📌 {category_name} (분석 실패 - 주요 기사 5선)\n"
        for a in articles[:max_display]:
            title = a.get('translated_title', a['title'])
            fallback += f"- {title}: {a['url']}\n"
        return fallback + "\n"

    result_str = ""
    displayed = 0
    used_ids = set()

    for topic in data:
        topic_title = topic.get("topic", "기타")
        ids = [i for i in topic.get("article_ids", []) if 1 <= i <= len(articles)]
        if not ids:
            continue

        remaining = max_display - displayed
        if remaining <= 0:
            break

        result_str += f"📌 {topic_title}\n"

        for i in ids[:remaining]:
            a = articles[i-1]
            title = a.get('translated_title', a['title'])
            result_str += f"- {title}: {a['url']}\n"
            displayed += 1
            used_ids.add(i)

        result_str += "\n"
        if displayed >= max_display:
            break

    if displayed == 0:
        for a in articles[:max_display]:
            title = a.get('translated_title', a['title'])
            result_str += f"- {title}: {a['url']}\n"
        result_str += "\n"

    if len(articles) > max_display:
        result_str += f"🔹 외 {len(articles) - max_display}건의 기사가 더 있습니다.\n\n"

    return result_str

def send_telegram(text):
    max_len = 3500
    chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)] if len(text) > max_len else [text]
    for idx, chunk in enumerate(chunks):
        if len(chunks) > 1:
            chunk = f"[{idx+1}/{len(chunks)}]\n{chunk}"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk}, timeout=10)
    print("All chunks sent successfully.")

# ===== 메인 실행 =====
if __name__ == "__main__":
    header = get_date_and_weather() + "\n\n" + get_market_indicators() + "\n"
    today = datetime.now()
    if today.weekday() in [5, 6, 0]:
        default_timelimit = 'w'
        header += "\n📌 주간 뉴스 모아보기 (최근 7일)\n"
    else:
        default_timelimit = 'd'

    # 카테고리별 수집 전략 (네이버 우선, 없으면 DDGS/Google)
    cat_articles = {}
    global_seen = set()

    # 1. 정치/시사 (네이버 → DDGS)
    cat_name = "🇰🇷 정치/시사"
    articles = []
    naver_articles = fetch_news_naver("정치", 50)
    if naver_articles:
        articles = naver_articles
        print(f"{cat_name}: Naver API {len(articles)} fetched")
    else:
        articles = fetch_news_ddg("정치", 50, region="kr-kr", timelimit=default_timelimit)
        print(f"{cat_name}: DDG {len(articles)} fetched (fallback)")
    unique = []
    for a in articles:
        if a['url'] not in global_seen:
            global_seen.add(a['url'])
            unique.append(a)
    cat_articles[cat_name] = unique
    print(f"  {cat_name}: total unique {len(unique)}")

    # 2. 한국 증시/경제 (네이버 → DDGS+Google)
    cat_name = "🇰🇷 한국 증시/경제"
    articles = []
    naver_articles = fetch_news_naver("증시", 50)
    if naver_articles:
        articles = naver_articles
        print(f"{cat_name}: Naver API {len(articles)} fetched")
    else:
        articles = fetch_news_ddg("증시", 50, region="kr-kr", timelimit=default_timelimit)
        articles.extend(fetch_news_google("증시", 30))
        print(f"{cat_name}: DDG+Google {len(articles)} fetched (fallback)")
    unique = []
    for a in articles:
        if a['url'] not in global_seen:
            global_seen.add(a['url'])
            unique.append(a)
    cat_articles[cat_name] = unique
    print(f"  {cat_name}: total unique {len(unique)}")

    # 3. 국내 돌발 (네이버 → DDGS)
    cat_name = "🚨 국내 돌발"
    articles = []
    naver_articles = fetch_news_naver("사건사고", 50)
    if naver_articles:
        articles = naver_articles
        print(f"{cat_name}: Naver API {len(articles)} fetched")
    else:
        articles = fetch_news_ddg("사건사고", 50, region="kr-kr", timelimit=default_timelimit)
        print(f"{cat_name}: DDG {len(articles)} fetched (fallback)")
    unique = []
    for a in articles:
        if a['url'] not in global_seen:
            global_seen.add(a['url'])
            unique.append(a)
    cat_articles[cat_name] = unique
    print(f"  {cat_name}: total unique {len(unique)}")

    # 4. 미국 주식 (DDGS)
    cat_name = "🇺🇸 미국 주식"
    articles = fetch_news_ddg("stock market", 50, region="us-en", timelimit=default_timelimit)
    unique = []
    for a in articles:
        if a['url'] not in global_seen:
            global_seen.add(a['url'])
            unique.append(a)
    cat_articles[cat_name] = unique
    print(f"{cat_name}: DDG {len(articles)} fetched, total unique {len(unique)}")

    # 5. 국제 뉴스 (DDGS)
    cat_name = "🌍 국제 뉴스"
    articles = fetch_news_ddg("world", 50, region="us-en", timelimit=default_timelimit)
    unique = []
    for a in articles:
        if a['url'] not in global_seen:
            global_seen.add(a['url'])
            unique.append(a)
    cat_articles[cat_name] = unique
    print(f"{cat_name}: DDG {len(articles)} fetched, total unique {len(unique)}")

    # 6. 해외 돌발 (DDGS)
    cat_name = "🚨 해외 돌발"
    articles = fetch_news_ddg("earthquake", 50, region="us-en", timelimit=default_timelimit)
    unique = []
    for a in articles:
        if a['url'] not in global_seen:
            global_seen.add(a['url'])
            unique.append(a)
    cat_articles[cat_name] = unique
    print(f"{cat_name}: DDG {len(articles)} fetched, total unique {len(unique)}")

    # 번역
    all_articles = []
    for arts in cat_articles.values():
        all_articles.extend(arts)
    translate_titles(all_articles)

    # 분석 및 리포트
    report = header + "📰 오늘의 뉴스 요약\n"
    # 원하는 순서로 출력
    for cat_name in ["🇰🇷 정치/시사", "🇰🇷 한국 증시/경제", "🇺🇸 미국 주식", "🌍 국제 뉴스", "🚨 국내 돌발", "🚨 해외 돌발"]:
        arts = cat_articles.get(cat_name, [])
        report += f"\n{cat_name}\n"
        report += analyze_category(cat_name, arts)

    send_telegram(report)
    print("Script finished.")
