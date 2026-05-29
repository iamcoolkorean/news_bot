import os
import time
import json
import requests
import yfinance as yf
from datetime import datetime, timedelta
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
        kospi_hist = kospi.history(period="2d")  # 오늘 + 어제
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

def analyze_category(category_name, articles):
    """카테고리별 Gemini 토픽 분석 (맞춤형 역할 지시)"""
    if not articles:
        return f"📌 {category_name}\n관련 뉴스가 없습니다.\n"

    titles = [f"{i+1}. {a['title']}" for i, a in enumerate(articles)]
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
                    result_str += f"- {a['title']}: {a['url']}\n"
                result_str += "\n"
                used_ids.update(valid_ids)

            unused = [a for i, a in enumerate(articles) if (i+1) not in used_ids]
            if unused:
                result_str += "📌 기타 주요 뉴스\n"
                for a in unused[:10]:
                    result_str += f"- {a['title']}: {a['url']}\n"
                result_str += "\n"

            return result_str

        except Exception as e:
            if attempt < max_retries:
                print(f"Retry {attempt+1}/{max_retries} for {category_name}: {e}")
                time.sleep(3)
            else:
                fallback = f"📌 {category_name} (분석 실패 - 전체 기사)\n"
                for a in articles:
                    fallback += f"- {a['title']}: {a['url']}\n"
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

    # 2. 카테고리 정의 (검색어 최적화)
    categories = [
        ("🇺🇸 미국 주식", "S&P 500 OR NASDAQ OR Dow Jones OR Fed OR earnings OR stock market OR Wall Street OR AI stock OR artificial intelligence stock OR AI chip OR tech stocks OR Magnificent Seven OR AAPL OR MSFT OR GOOGL OR AMZN OR NVDA OR TSLA OR META", 50, "us-en"),
        ("🇰🇷 정치/시사", "정치 OR 국회 OR 대통령 OR 외교 OR 시사 OR 북한 OR 안보", 50, "kr-kr"),
        ("🇰🇷 한국 증시/경제", "삼성전자 주가 OR SK하이닉스 주가 OR 코스피 상승 OR 코스닥 급등 OR 증시 전망 OR 실적 발표 OR 공시 OR 배당 OR 외국인 순매수 OR 기관 매매 OR AI 반도체 수주 OR HBM OR 반도체 주가", 50, "kr-kr"),
        ("🌍 국제 뉴스", "world news OR geopolitics OR IMF OR UN OR summit OR NATO OR global economy", 50, "us-en"),
        ("🚨 국내 돌발 뉴스", "속보", 3, "kr-kr"),
        ("🚨 해외 돌발 뉴스", "world breaking news", 2, "us-en")
    ]

    cat_articles = {}
    global_seen = set()

    for cat_name, query, count, region in categories:
        print(f"Fetching {cat_name} (max {count})...")
        try:
            articles = fetch_news(query, max_results=count, region=region, timelimit='d')
            unique = []
            for a in articles:
                if a['url'] not in global_seen:
                    global_seen.add(a['url'])
                    unique.append(a)
            cat_articles[cat_name] = unique
            print(f"  {cat_name}: {len(articles)} fetched, {len(unique)} unique (total: {len(global_seen)})")
        except Exception as e:
            print(f"  {cat_name} error: {e}")
            cat_articles[cat_name] = []
        time.sleep(2)

    # 3. 뉴스 토픽 리포트 생성
    report = header + "📰 오늘의 뉴스 토픽 브리핑\n"
    for cat_name, articles in cat_articles.items():
        report += f"\n{cat_name}\n"
        report += analyze_category(cat_name, articles)

    # 4. 전송
    print(f"Report length: {len(report)}")
    send_telegram(report)
    print("Script finished.")
