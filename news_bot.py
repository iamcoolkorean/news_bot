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
    """오늘 날짜와 서울 날씨 (JSON 파싱으로 정확한 최고/최저 기온 추출)"""
    today = datetime.now().strftime("%Y년 %m월 %d일")
    weather_info = "서울 날씨 정보를 가져올 수 없습니다."
    try:
        # JSON 형식으로 요청하여 현재 온도, 최고/최저 온도, 날씨 상태 파싱
        resp = requests.get("https://wttr.in/Seoul?format=j1", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            current = data['current_condition'][0]
            today_weather = data['weather'][0]  # 오늘 날씨 정보

            # 섭씨 온도 추출
            temp_curr = current['temp_C']
            temp_low = today_weather['mintempC']
            temp_high = today_weather['maxtempC']
            desc = current['weatherDesc'][0]['value']
            humidity = current['humidity']
            precip = today_weather['hourly'][0].get('precipMM', '0.0')  # 강수량

            weather_info = (
                f"서울 날씨: {desc} | "
                f"현재 {temp_curr}°C | "
                f"최저 {temp_low}°C | "
                f"최고 {temp_high}°C | "
                f"강수량 {precip}mm | "
                f"습도 {humidity}%"
            )
    except Exception as e:
        print(f"Weather fetch error: {e}")
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

def create_topic_report(all_articles):
    """전체 기사를 7개의 주요 토픽으로 분류 (각 토픽 4~6개 기사 포함)"""
    if not all_articles:
        return "📰 오늘 수집된 뉴스가 없습니다."

    titles = [f"{i+1}. {a['title']}" for i, a in enumerate(all_articles)]
    title_list = "\n".join(titles)

    prompt = f"""다음은 오늘 수집한 뉴스 제목 목록입니다. 주요 주제 **7개**를 찾아주세요.
각 토픽에는 **반드시 4~6개의 기사**를 포함시켜 주세요.

📌 [토픽 제목]
[1,3,7,15,22]  ← 최소 4개, 최대 6개 번호를 적어주세요

**중요 규칙**:
- 각 기사는 하나의 토픽에만 포함 (중복 불가)
- 각 토픽에는 **반드시 4~6개**의 기사를 배정해주세요.
- 만약 특정 토픽에 묶을 기사가 부족하면 토픽을 6개로 줄여도 좋습니다. 단, 남은 토픽들은 4~6개를 유지하세요.
- 설명 없이 번호 목록만 출력

기사 목록:
{title_list}
"""
    max_retries = 2
    raw_result = None
    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            raw_result = response.text
            break
        except Exception as e:
            if attempt < max_retries:
                print(f"Topic extraction retry {attempt+1}/{max_retries}: {e}")
                time.sleep(3)
            else:
                fallback = "📰 오늘의 뉴스 (토픽 분류 실패)\n\n"
                for a in all_articles:
                    fallback += f"- {a['title']}: {a['url']}\n"
                return fallback

    report = "📰 오늘의 뉴스 토픽 요약\n\n"
    try:
        lines = [l.strip() for l in raw_result.split('\n') if l.strip()]
        current_topic = None
        for line in lines:
            if line.startswith('📌'):
                current_topic = line.replace('📌 ', '').strip()
                report += f"📌 {current_topic}\n"
            elif line.startswith('[') and line.endswith(']'):
                numbers = [int(x.strip()) for x in line[1:-1].split(',')]
                for num in numbers:
                    if 1 <= num <= len(all_articles):
                        a = all_articles[num-1]
                        report += f"- {a['title']}: {a['url']}\n"
                report += "\n"
    except:
        report = raw_result
    return report

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

# ===== 메인 실행 =====
if __name__ == "__main__":
    # 수집 개수: 모든 카테고리를 25개로 상향 조정 (정치 뉴스 대폭 보강)
    categories = [
        ("미국 주식", "S&P 500 OR NASDAQ OR Dow Jones OR Fed OR earnings OR stock market OR Wall Street OR AI stock OR artificial intelligence stock OR AI chip OR tech stocks OR Magnificent Seven OR AAPL OR MSFT OR GOOGL OR AMZN OR NVDA OR TSLA OR META", 25, "us-en"),
        ("정치/시사", "정치 OR 국회 OR 대통령 OR 외교 OR 시사 OR 북한 OR 안보", 25, "kr-kr"),
        ("한국 증시/경제", "코스피 OR 코스닥 OR 증권 OR 주식 OR 경제 OR 금리 OR AI 주식 OR 인공지능 주식 OR AI 반도체 OR 삼성전자 OR SK하이닉스", 25, "kr-kr"),
        ("국제 뉴스", "world news OR geopolitics OR IMF OR UN OR summit OR NATO OR global economy", 25, "us-en")
    ]

    all_articles = []
    seen_urls = set()

    for cat_name, query, count, region in categories:
        print(f"Fetching {cat_name} (max {count})...")
        try:
            articles = fetch_news(query, max_results=count, region=region, timelimit='d')
            added = 0
            for a in articles:
                if a['url'] not in seen_urls:
                    seen_urls.add(a['url'])
                    all_articles.append(a)
                    added += 1
            print(f"{cat_name}: {len(articles)} fetched, {added} added (total: {len(all_articles)})")
        except Exception as e:
            print(f"{cat_name} error: {e}")
        time.sleep(2)  # 요청 간격

    header = get_date_and_weather()
    report = create_topic_report(all_articles)
    print("Report length:", len(report))

    final_message = f"{header}\n\n{report}"
    send_telegram(final_message)
    print("Script finished.")
