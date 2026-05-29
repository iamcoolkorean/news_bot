import os
import time
import json
import requests
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
    """
    해당 카테고리의 기사 제목들을 Gemini에게 보내서
    5개의 중요 주제(토픽)를 추출하고, 각 토픽에 해당하는 기사 번호를 JSON으로 받음.
    실패 시 전체 기사 리스트를 반환.
    """
    if not articles:
        return f"📌 {category_name}\n관련 뉴스가 없습니다.\n"

    # 번호가 붙은 제목 목록 생성
    titles = [f"{i+1}. {a['title']}" for i, a in enumerate(articles)]
    titles_text = "\n".join(titles)

    prompt = f"""당신은 뉴스 분석가입니다. 아래는 '{category_name}' 분야의 오늘 뉴스 기사 제목 목록입니다.
이 제목들을 분석하여 **가장 많이 언급되었거나 중요한 주제(토픽) 5개**를 찾아주세요.
각 토픽에는 해당하는 기사들의 **번호**를 모두 모아서, 반드시 아래 JSON 형식으로만 답변하세요.
다른 설명은 절대 쓰지 마세요.

[
  {{
    "topic": "토픽 제목 (예: 연준 금리 인상)",
    "article_ids": [1, 3, 5, 12]
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
            # 코드 블록 제거
            if text.startswith("```"):
                text = text.split("```")[1].strip()
                if text.startswith("json"):
                    text = text[4:].strip()
            data = json.loads(text)

            # 토픽별 기사 조립
            result_str = ""
            used_ids = set()
            for topic in data:
                topic_title = topic.get("topic", "기타")
                ids = topic.get("article_ids", [])
                # 유효한 번호만 필터링
                valid_ids = [i for i in ids if 1 <= i <= len(articles)]
                if not valid_ids:
                    continue
                result_str += f"📌 {topic_title}\n"
                for i in valid_ids:
                    a = articles[i-1]
                    result_str += f"- {a['title']}: {a['url']}\n"
                result_str += "\n"
                used_ids.update(valid_ids)

            # 어느 토픽에도 포함되지 않은 기사는 '기타'로 추가
            unused = [a for i, a in enumerate(articles) if (i+1) not in used_ids]
            if unused:
                result_str += "📌 기타 주요 뉴스\n"
                for a in unused[:10]:  # 너무 많으면 10개만
                    result_str += f"- {a['title']}: {a['url']}\n"
                result_str += "\n"

            return result_str

        except Exception as e:
            if attempt < max_retries:
                print(f"Retry {attempt+1}/{max_retries} for {category_name}: {e}")
                time.sleep(3)
            else:
                # 실패 시 전체 리스트 반환
                fallback = f"📌 {category_name} (토픽 분석 실패 - 전체 기사)\n"
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
    # 카테고리 정의: 각 50개씩 수집
    categories = [
        ("🇺🇸 미국 주식", "S&P 500 OR NASDAQ OR Dow Jones OR Fed OR earnings OR stock market OR Wall Street OR AI stock OR artificial intelligence stock OR AI chip OR tech stocks OR Magnificent Seven OR AAPL OR MSFT OR GOOGL OR AMZN OR NVDA OR TSLA OR META", 50, "us-en"),
        ("🇰🇷 정치/시사", "정치 OR 국회 OR 대통령 OR 외교 OR 시사 OR 북한 OR 안보", 50, "kr-kr"),
        ("🇰🇷 한국 증시/경제", "코스피 OR 코스닥 OR 증권 OR 주식 OR 경제 OR 금리 OR AI 주식 OR 인공지능 주식 OR AI 반도체 OR 삼성전자 OR SK하이닉스", 50, "kr-kr"),
        ("🌍 국제 뉴스", "world news OR geopolitics OR IMF OR UN OR summit OR NATO OR global economy", 50, "us-en"),
        ("🚨 국내 돌발 뉴스", "속보", 3, "kr-kr"),
        ("🚨 해외 돌발 뉴스", "world breaking news", 2, "us-en")
    ]

    # 1단계: 카테고리별 기사 수집 (중복 URL은 카테고리 내에서만 제거)
    cat_articles = {}
    global_seen = set()

    for cat_name, query, count, region in categories:
        print(f"Fetching {cat_name} (max {count})...")
        try:
            articles = fetch_news(query, max_results=count, region=region, timelimit='d')
            # 카테고리 내 중복 제거 + 전역 중복 제거
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

    # 2단계: 리포트 생성
    report = get_date_and_weather() + "\n\n📰 오늘의 뉴스 토픽 브리핑\n"

    for cat_name, articles in cat_articles.items():
        report += f"\n{cat_name}\n"
        analyzed = analyze_category(cat_name, articles)
        report += analyzed

    # 3단계: 전송
    print(f"Report length: {len(report)}")
    send_telegram(report)
    print("Script finished.")
