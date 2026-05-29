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

# Gemini 클라이언트
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

def generate_category_summary(category_name, articles):
    """카테고리별 기사 제목을 바탕으로 한 줄 요약 생성"""
    if not articles:
        return ""

    titles = [a['title'] for a in articles]
    titles_text = "\n".join(f"- {t}" for t in titles)

    prompt = f"""다음은 오늘 "{category_name}" 카테고리의 주요 뉴스 제목 목록입니다.
이 제목들을 바탕으로 오늘 이 분야의 가장 중요한 흐름이나 이슈를 **한 문장**으로 간결하게 요약해주세요.
별다른 설명 없이 요약 문장만 출력해주세요.

뉴스 제목:
{titles_text}
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        print(f"Summary generation failed for {category_name}: {e}")
        return ""

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
    # 카테고리 정의
    categories = [
        ("🇺🇸 미국 주식", "S&P 500 OR NASDAQ OR Dow Jones OR Fed OR earnings OR stock market OR Wall Street OR AI stock OR artificial intelligence stock OR AI chip OR tech stocks OR Magnificent Seven OR AAPL OR MSFT OR GOOGL OR AMZN OR NVDA OR TSLA OR META", 25, "us-en"),
        ("🇰🇷 정치/시사", "정치 OR 국회 OR 대통령 OR 외교 OR 시사 OR 북한 OR 안보", 25, "kr-kr"),
        ("🇰🇷 한국 증시/경제", "코스피 OR 코스닥 OR 증권 OR 주식 OR 경제 OR 금리 OR AI 주식 OR 인공지능 주식 OR AI 반도체 OR 삼성전자 OR SK하이닉스", 25, "kr-kr"),
        ("🌍 국제 뉴스", "world news OR geopolitics OR IMF OR UN OR summit OR NATO OR global economy", 25, "us-en")
    ]

    # 1단계: 카테고리별 기사 수집 + 중복 제거
    cat_articles = {}  # 카테고리명 -> 기사 리스트
    seen_urls = set()

    for cat_name, query, count, region in categories:
        print(f"Fetching {cat_name}...")
        try:
            articles = fetch_news(query, max_results=count, region=region, timelimit='d')
            # 중복 URL 제거
            unique = []
            for a in articles:
                if a['url'] not in seen_urls:
                    seen_urls.add(a['url'])
                    unique.append(a)
            cat_articles[cat_name] = unique
            print(f"  {cat_name}: {len(articles)} fetched, {len(unique)} unique (total so far: {len(seen_urls)})")
        except Exception as e:
            print(f"  {cat_name} error: {e}")
            cat_articles[cat_name] = []
        time.sleep(2)  # DDG 요청 간격

    # 2단계: 리포트 생성 (헤더 + 각 카테고리별 요약 + 기사 목록)
    report = get_date_and_weather() + "\n\n📰 오늘의 뉴스 브리핑\n"

    for cat_name, articles in cat_articles.items():
        report += f"\n{cat_name}\n"
        # 요약 생성
        summary = generate_category_summary(cat_name, articles)
        if summary:
            report += f"{summary}\n"
        # 기사 리스트
        if articles:
            for a in articles:
                report += f"- {a['title']}: {a['url']}\n"
        else:
            report += "관련 기사를 찾을 수 없습니다.\n"

    # 3단계: 텔레그램 전송
    print(f"Report generated. Length: {len(report)}")
    send_telegram(report)
    print("Script finished.")
