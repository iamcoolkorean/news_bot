import os
import time
import requests
from datetime import datetime
from ddgs import DDGS
from google import genai

# --- 환경 변수 ---
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Gemini 초기화
client = genai.Client(api_key=GEMINI_KEY)

def get_date_and_weather():
    """오늘 날짜와 서울의 상세 날씨를 문자열로 반환"""
    today = datetime.now().strftime("%Y년 %m월 %d일")
    weather_info = "서울 날씨 정보를 가져올 수 없습니다."
    try:
        # %C: 날씨 상태, %t: 현재 온도, %l: 최저, %h: 최고, %p: 강수량, %m: 습도
        resp = requests.get(
            "https://wttr.in/Seoul?format=%C+|+현재+%t+|+최저+%l+|+최고+%h+|+강수+%p+|+습도+%m",
            timeout=5
        )
        if resp.status_code == 200:
            weather_info = f"서울 날씨: {resp.text.strip()}"
    except:
        pass
    return f"📅 {today}\n{weather_info}"

def fetch_news(query, max_results, region='kr-kr', timelimit='d'):
    """DDG 뉴스 검색 (제목과 URL만 수집, 본문은 사용하지 않음)"""
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
    """
    전체 기사 제목을 Gemini에 보내서 5개 주요 토픽으로 분류.
    각 토픽에는 최소 3~5개의 기사가 포함되도록 지시.
    """
    if not all_articles:
        return "📰 오늘 수집된 뉴스가 없습니다."

    # 번호를 붙인 제목 목록 생성
    titles = [f"{i+1}. {a['title']}" for i, a in enumerate(all_articles)]
    title_list = "\n".join(titles)

    prompt = f"""다음은 오늘 수집한 뉴스 제목 목록입니다. 주요 주제 **5개**를 찾아주세요.
각 토픽에는 **반드시 3~5개의 기사를 포함**시켜야 합니다.

📌 [토픽 제목]
[1,3,7,15]  ← 최소 3개, 최대 5개 번호를 적어주세요

**중요 규칙**:
- 각 기사는 하나의 토픽에만 포함 (중복 불가)
- 각 토픽에는 최소 3개 이상의 기사를 배정해주세요.
- 만약 특정 토픽에 묶을 기사가 부족하면, 토픽을 4개로 줄여도 좋습니다. 하지만 **각 토픽의 기사 개수는 3~5개**를 지켜주세요.
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
                # 실패 시 전체 기사 제목+링크 단순 나열
                fallback = "📰 오늘의 뉴스 (토픽 분류 실패)\n\n"
                for a in all_articles:
                    fallback += f"- {a['title']}: {a['url']}\n"
                return fallback

    # Gemini 응답 파싱 (토픽 제목과 번호 리스트 추출)
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
        # 파싱 실패 시 원시 응답을 그대로 전송
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
    print("Telegram message(s) sent successfully.")

# ===== 메인 실행 =====
if __name__ == "__main__":
    # 수집 순서: 미국 주식을 가장 먼저! (중복 제거 시 우선권 확보)
    categories = [
        ("미국 주식", "S&P 500 OR NASDAQ OR Dow Jones OR Fed OR earnings OR stock market OR Wall Street OR AI stock OR artificial intelligence stock OR AI chip OR tech stocks OR Magnificent Seven OR AAPL OR MSFT OR GOOGL OR AMZN OR NVDA OR TSLA OR META", 25, "us-en"),
        ("정치/시사", "정치 OR 국회 OR 대통령 OR 외교 OR 시사", 13, "kr-kr"),
        ("한국 증시/경제", "코스피 OR 코스닥 OR 증권 OR 주식 OR 경제 OR 금리 OR AI 주식 OR 인공지능 주식 OR AI 반도체 OR 삼성전자 OR SK하이닉스", 13, "kr-kr"),
        ("국제 뉴스", "world news OR geopolitics OR IMF OR UN OR summit", 13, "us-en")
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
            print(f"{cat_name}: {len(articles)} fetched, {added} added (total unique: {len(all_articles)})")
        except Exception as e:
            print(f"{cat_name} error: {e}")
        time.sleep(2)  # 요청 간격

    # 헤더 (날짜 + 날씨)
    header = get_date_and_weather()

    # 토픽 리포트 생성
    report = create_topic_report(all_articles)
    print("Report generated. Length:", len(report))

    # 최종 메시지 조합 및 전송
    final_message = f"{header}\n\n{report}"
    send_telegram(final_message)
    print("Script finished.")
