import os
import time
import requests
from datetime import datetime
from ddgs import DDGS
from google import genai

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

client = genai.Client(api_key=GEMINI_KEY)

def get_date_and_weather():
    today = datetime.now().strftime("%Y년 %m월 %d일")
    weather_info = "서울 날씨 정보를 가져올 수 없습니다."
    try:
        # wttr.in 형식: %C(날씨), %t(현재온도), %l(최저), %h(최고), %p(강수량), %m(습도)
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
    with DDGS() as ddgs:
        news = list(ddgs.news(query=query, max_results=max_results, region=region, timelimit=timelimit))
    articles = []
    for item in news:
        url = item.get("url")
        if not url:
            continue
        articles.append({"title": item.get("title", "제목 없음"), "url": url})
    return articles

def create_topic_report(all_articles):
    if not all_articles:
        return "📰 오늘 수집된 뉴스가 없습니다."

    titles = [f"{i+1}. {a['title']}" for i, a in enumerate(all_articles)]
    title_list = "\n".join(titles)

    prompt = f"""다음은 오늘 수집한 뉴스 제목 목록입니다. 주요 주제 5개를 찾아주세요.
각 토픽에는 해당 기사의 번호만 아래 형식으로 적어주세요.

📌 [토픽 제목]
[1,3,7,15,22]

- 모든 기사는 하나의 토픽에만 포함 (중복 불가)
- 최대 7개까지만 선택
- 설명 없이 번호 목록만 출력

기사 목록:
{title_list}
"""
    max_retries = 2
    raw_result = None
    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            raw_result = response.text
            break
        except Exception as e:
            if attempt < max_retries:
                print(f"Retry {attempt+1}/{max_retries}: {e}")
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
    max_len = 3500
    chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]
    for idx, chunk in enumerate(chunks):
        if len(chunks) > 1:
            chunk = f"[{idx+1}/{len(chunks)}]\n{chunk}"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk}
        requests.post(url, json=payload, timeout=10)

if __name__ == "__main__":
    # 순서 변경: 미국 주식을 가장 먼저! (중복 제거 시 우선권 확보)
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
        time.sleep(2)

    header = get_date_and_weather()
    report = create_topic_report(all_articles)
    final_message = f"{header}\n\n{report}"
    send_telegram(final_message)
    print("Script finished.")
