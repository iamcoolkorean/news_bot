import os
import time
import re
import json
import requests
import yfinance as yf
import feedparser
from datetime import datetime
from ddgs import DDGS
from google import genai

# --- 환경 변수 ---
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

client = genai.Client(api_key=GEMINI_KEY)

def get_date_and_weather():
    """오늘 날짜와 서울 날씨 (JSON 파싱, 섭씨)"""
    today = datetime.now().strftime("%Y년 %m월 %d일")
    weather_info = "서울 날씨 정보를 가져올 수 없습니다."
    try:
        resp = requests.get("https://wttr.in/Seoul?format=j1", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            current = data['current_condition'][0]
            today_weather = data['weather'][0]

            temp_curr = current['temp_C']
            temp_low = today_weather['mintempC']
            temp_high = today_weather['maxtempC']
            desc = current['weatherDesc'][0]['value']
            humidity = current['humidity']
            precip = today_weather['hourly'][0].get('precipMM', '0.0')

            weather_info = (
                f"서울 날씨: {desc} | "
                f"현재 {temp_curr}°C | "
                f"최저 {temp_low}°C | "
                f"최고 {temp_high}°C | "
                f"강수량 {precip}mm | "
                f"습도 {humidity}%"
            )
    except Exception as e:
        print(f"Weather error: {e}")
    return f"📅 {today}\n{weather_info}"

def get_market_indicators():
    """주요 금융 지표 (KOSPI, KOSDAQ, USD/KRW) 수집"""
    try:
        # KOSPI
        kospi = yf.Ticker("^KS11")
        kospi_hist = kospi.history(period="2d")
        if len(kospi_hist) >= 2:
            prev_close = kospi_hist['Close'].iloc[-2]
            last_close = kospi_hist['Close'].iloc[-1]
            kospi_change = ((last_close - prev_close) / prev_close) * 100
            kospi_str = f"{last_close:,.2f} ({kospi_change:+.2f}%)"
        elif len(kospi_hist) == 1:
            last_close = kospi_hist['Close'].iloc[-1]
            kospi_str = f"{last_close:,.2f}"
        else:
            kospi_str = "정보 없음"

        # KOSDAQ
        kosdaq = yf.Ticker("^KQ11")
        kosdaq_hist = kosdaq.history(period="2d")
        if len(kosdaq_hist) >= 2:
            prev_close = kosdaq_hist['Close'].iloc[-2]
            last_close = kosdaq_hist['Close'].iloc[-1]
            kosdaq_change = ((last_close - prev_close) / prev_close) * 100
            kosdaq_str = f"{last_close:,.2f} ({kosdaq_change:+.2f}%)"
        elif len(kosdaq_hist) == 1:
            last_close = kosdaq_hist['Close'].iloc[-1]
            kosdaq_str = f"{last_close:,.2f}"
        else:
            kosdaq_str = "정보 없음"

        # USD/KRW 환율
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

def fetch_news(query, max_results, region='kr-kr', timelimit='d'):
    """DDG 뉴스 검색 (제목과 URL만 수집)"""
    with DDGS() as ddgs:
        news = list(ddgs.news(
            query=query,
            max_results=max_results,
            region=region,
            timelimit=timelimit
        ))
    articles = []
    for item in news:
        url = item.get("url")
        if not url:
            continue
        articles.append({
            "title": item.get("title", "제목 없음"),
            "url": url
        })
    return articles

def fetch_google_news(query, max_results=30):
    """구글 뉴스 RSS에서 한국어 뉴스 수집 (DDGS 보완)"""
    # URL 인코딩이 필요하지만 query 자체가 간단한 경우 requests가 처리
    url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:max_results]:
            articles.append({
                "title": entry.title,
                "url": entry.link
            })
        return articles
    except Exception as e:
        print(f"Google News error: {e}")
        return []

def is_english_title(title):
    """제목이 영어인지 간단히 판별 (한글이 전혀 없으면 영어로 간주)"""
    return not bool(re.search(r'[가-힣]', title))

def translate_titles(articles):
    """
    영어 제목을 한국어로 번역 (Gemini 사용)
    articles: [{'title': ..., 'url': ...}] 리스트
    번역이 성공하면 'translated_title' 필드를 추가하고, 실패하면 원어 유지
    """
    # 영어 제목 인덱스 수집
    eng_indices = [i for i, a in enumerate(articles) if is_english_title(a['title'])]
    if not eng_indices:
        return  # 번역할 것이 없음

    # 번역할 제목 리스트
    original_titles = [articles[i]['title'] for i in eng_indices]

    prompt = (
        "다음 영어 뉴스 제목들을 한국어로 번역해주세요.\n"
        "각 제목을 순서대로 번역하고, 번역 결과만 한 줄씩 출력하세요.\n"
        "다른 설명은 일절 추가하지 마세요.\n\n"
        + "\n".join(original_titles)
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        translated = response.text.strip().split('\n')
        # 번역 결과 매핑 (길이가 다를 수 있으니 최대한 매칭)
        for idx, i in enumerate(eng_indices):
            if idx < len(translated) and translated[idx].strip():
                articles[i]['translated_title'] = translated[idx].strip()
            else:
                articles[i]['translated_title'] = articles[i]['title']  # 실패 시 원어
    except Exception as e:
        print(f"Translation error: {e}")
        # 번역 실패 시 모든 영어 제목에 대해 원어 유지 (별도 처리 없음)

def analyze_category(category_name, articles):
    """카테고리별 Gemini 토픽 분석 (맞춤형 역할 지시)"""
    if not articles:
        return f"📌 {category_name}\n관련 뉴스가 없습니다.\n"

    titles = []
    for i, a in enumerate(articles):
        # 표시 제목: 번역본이 있으면 사용, 없으면 원본
        display_title = a.get('translated_title', a['title'])
        titles.append(f"{i+1}. {display_title}")

    titles_text = "\n".join(titles)

    # 카테고리별 역할 및 요구사항 설정
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
  {{
    "topic": "토픽 제목 (구체적으로)",
    "article_ids": [1, 3, 5]
  }},
  ...
]

기사 목록:
{titles_text}"""

    max_retries = 2
    for attempt in range(max_retries + 1):
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
            data = json.loads(text)

            result_str = ""
            used_ids = set()
            for topic in data:
                topic_title = topic.get("topic", "기타")
                ids = topic.get("article_ids", [])
                valid_ids = [i for i in ids if 1 <= i <= len(articles)]
                if not valid_ids:
                    continue
                result_str += f"📌 {topic_title}\n"
                for i in valid_ids:
                    a = articles[i-1]
                    # 최종 출력 시에도 번역 제목 사용
                    display_title = a.get('translated_title', a['title'])
                    result_str += f"- {display_title}: {a['url']}\n"
                result_str += "\n"
                used_ids.update(valid_ids)

            unused = [a for i, a in enumerate(articles) if (i+1) not in used_ids]
            if unused:
                result_str += "📌 기타 주요 뉴스\n"
                for a in unused[:10]:
                    display_title = a.get('translated_title', a['title'])
                    result_str += f"- {display_title}: {a['url']}\n"
                result_str += "\n"

            return result_str

        except Exception as e:
            if attempt < max_retries:
                print(f"Retry {attempt+1}/{max_retries} for {category_name}: {e}")
                time.sleep(3)
            else:
                fallback = f"📌 {category_name} (분석 실패 - 전체 기사)\n"
                for a in articles:
                    display_title = a.get('translated_title', a['title'])
                    fallback += f"- {display_title}: {a['url']}\n"
                return fallback + "\n"

def send_telegram(text):
    """텔레그램 메시지 전송 (길면 자동 분할)"""
    max_len = 3500
    if len(text) <= max_len:
        chunks = [text]
    else:
        chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]

    for idx, chunk in enumerate(chunks):
        if len(chunks) > 1:
            chunk = f"[{idx+1}/{len(chunks)}]\n{chunk}"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk}
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"Telegram send failed: {resp.text}")
        else:
            print(f"Chunk {idx+1}/{len(chunks)} sent.")
    print("All chunks sent successfully.")

# ===== 메인 실행 =====
if __name__ == "__main__":
    # 1. 날짜, 날씨, 금융 지표
    header = get_date_and_weather() + "\n\n" + get_market_indicators() + "\n"

    # 2. 카테고리 정의 (검색어 최적화 + 구글 뉴스 보완)
    # 일반 뉴스 카테고리는 DDGS만 사용, 한국 증시는 구글 뉴스 RSS 추가 수집
    categories = [
        ("🇺🇸 미국 주식", "S&P 500 OR NASDAQ OR Dow Jones OR Fed OR earnings OR stock market OR Wall Street OR AI stock OR artificial intelligence stock OR AI chip OR tech stocks OR Magnificent Seven OR AAPL OR MSFT OR GOOGL OR AMZN OR NVDA OR TSLA OR META", 50, "us-en", False),
        ("🇰🇷 정치/시사", "정치 OR 국회 OR 대통령 OR 외교 OR 시사 OR 북한 OR 안보", 50, "kr-kr", False),
        ("🇰🇷 한국 증시/경제", "코스피 OR 코스닥 OR 증시 OR 주식 시장 OR 증권 OR 시황 OR 전망 OR 이슈 OR 분석 OR 특징주 OR 실적 OR 공시 OR 외국인 OR 기관 OR AI 반도체 OR HBM OR 삼성전자 OR SK하이닉스", 50, "kr-kr", True),  # 구글 뉴스 보완
        ("🌍 국제 뉴스", "world news OR geopolitics OR IMF OR UN OR summit OR NATO OR global economy", 50, "us-en", False),
        ("🚨 국내 돌발", "사건사고 OR 재난 OR 지진 OR 화재 OR 테러 OR 대규모 정전 OR 전염병 OR 경찰 긴급 OR 소방 당국", 5, "kr-kr", False),
        ("🚨 해외 돌발", "earthquake OR terror attack OR plane crash OR major explosion OR natural disaster OR pandemic OR coup", 5, "us-en", False)
    ]

    cat_articles = {}
    global_seen = set()

    for cat_name, query, count, region, use_google in categories:
        print(f"Fetching {cat_name} (max {count})...")
        articles = []
        try:
            # 1) DDGS 수집
            ddg_articles = fetch_news(query, max_results=count, region=region, timelimit='d')
            articles.extend(ddg_articles)
            print(f"  DDG: {len(ddg_articles)} fetched")
        except Exception as e:
            print(f"  DDG error for {cat_name}: {e}")

        # 2) 구글 뉴스 RSS 보완 (한국 증시/경제 카테고리만)
        if use_google:
            try:
                google_articles = fetch_google_news(query, max_results=30)
                articles.extend(google_articles)
                print(f"  Google News: {len(google_articles)} fetched")
            except Exception as e:
                print(f"  Google News error: {e}")

        # 중복 제거 (URL 기준)
        unique = []
        for a in articles:
            if a['url'] not in global_seen:
                global_seen.add(a['url'])
                unique.append(a)
        cat_articles[cat_name] = unique
        print(f"  {cat_name}: total unique {len(unique)} (global total: {len(global_seen)})")
        time.sleep(2)  # API 호출 간격

    # 3. 전체 기사 번역 (영어 → 한국어)
    all_articles = []
    for articles in cat_articles.values():
        all_articles.extend(articles)
    print("Translating English titles...")
    translate_titles(all_articles)
    print("Translation completed.")

    # 4. 뉴스 토픽 리포트 생성
    report = header + "📰 오늘의 뉴스 토픽 브리핑\n"
    for cat_name, articles in cat_articles.items():
        report += f"\n{cat_name}\n"
        report += analyze_category(cat_name, articles)

    # 5. 전송
    print(f"Report length: {len(report)}")
    send_telegram(report)
    print("Script finished.")
