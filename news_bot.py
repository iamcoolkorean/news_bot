import os
import time
import requests
from ddgs import DDGS
from google import genai

# --- 환경 변수 ---
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Gemini 초기화
client = genai.Client(api_key=GEMINI_KEY)

def fetch_news(query, max_results, region='kr-kr', timelimit='d'):
    """DDG 뉴스 검색 (제목과 URL만 수집, 본문은 무시)"""
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
    전체 기사 제목을 번호와 함께 Gemini에 보내서 5개 주요 토픽으로 분류.
    반환된 기사 번호를 바탕으로 제목과 링크를 조립해 리포트 생성.
    """
    if not all_articles:
        return "📰 오늘 수집된 뉴스가 없습니다."

    # 번호를 붙인 제목 목록 만들기
    titles = []
    for i, a in enumerate(all_articles):
        titles.append(f"{i+1}. {a['title']}")

    title_list = "\n".join(titles)

    prompt = f"""다음은 오늘 수집한 뉴스 기사의 제목 목록입니다 (번호와 함께 제공).
이 기사들을 분석하여 **가장 두드러진 주요 주제(토픽) 5개**를 찾아주세요.
각 토픽에는 해당하는 기사의 **번호들만** 아래 형식으로 정리해주세요.

📌 [토픽 제목]
[1,3,7,15,22]  ← 예시

**중요**:
- 각 기사는 반드시 하나의 토픽에만 포함되어야 합니다 (중복 불가).
- 모든 기사를 빠짐없이 처리하고, 어디에도 속하지 않는 기사는 '기타' 토픽에 넣어도 됩니다.
- 한 토픽에 너무 많은 기사가 몰리면 중요도 순으로 최대 7개까지 선택하세요.
- 번호 목록 외에 다른 설명은 일절 쓰지 마세요.

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
        # 응답을 줄 단위로 파싱
        lines = [l.strip() for l in raw_result.split('\n') if l.strip()]
        current_topic = None
        for line in lines:
            if line.startswith('📌'):
                current_topic = line.replace('📌 ', '').strip()
                report += f"📌 {current_topic}\n"
            elif line.startswith('[') and line.endswith(']'):
                # 번호 리스트 파싱
                numbers = [int(x.strip()) for x in line[1:-1].split(',')]
                for num in numbers:
                    if 1 <= num <= len(all_articles):
                        a = all_articles[num-1]
                        report += f"- {a['title']}: {a['url']}\n"
                report += "\n"
    except Exception as e:
        # 파싱 실패 시 원시 응답을 그대로 전송
        report = raw_result

    return report

def send_telegram(text):
    """텔레그램 메시지 전송 (길면 분할)"""
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

# ===== 메인 =====
if __name__ == "__main__":
    # 카테고리 정의: (표시명, 검색어, 최대 기사 수, 지역)
    # 전체 약 50개 수집을 위해 각 카테고리당 13개씩 (총 52)
    PER_CATEGORY = 13

    categories = [
        ("정치/시사", "정치 OR 국회 OR 대통령 OR 외교 OR 시사", PER_CATEGORY, "kr-kr"),
        ("한국 증시/경제", "코스피 OR 코스닥 OR 증권 OR 주식 OR 경제 OR 금리 OR AI 주식 OR 인공지능 주식 OR AI 반도체 OR AI 관련주 OR 삼성전자 OR SK하이닉스", PER_CATEGORY, "kr-kr"),
        ("미국 주식", "S&P 500 OR NASDAQ OR Dow Jones OR Fed OR earnings OR stock market OR AI stock OR artificial intelligence stock OR AI chip OR tech stocks OR Magnificent Seven OR AAPL OR MSFT OR GOOGL OR AMZN OR NVDA OR TSLA OR META", PER_CATEGORY, "us-en"),
        ("국제 뉴스", "world news OR geopolitics OR IMF OR UN OR summit", PER_CATEGORY, "us-en")
    ]

    all_articles = []
    seen_urls = set()

    for cat_name, query, count, region in categories:
        print(f"Fetching {cat_name} (max {count})...")
        try:
            articles = fetch_news(query, max_results=count, region=region, timelimit='d')
            # 중복 제거
            for a in articles:
                if a['url'] not in seen_urls:
                    seen_urls.add(a['url'])
                    all_articles.append(a)
            print(f"{cat_name}: {len(articles)} fetched, total unique: {len(all_articles)}")
        except Exception as e:
            print(f"{cat_name} fetch error: {e}")
        time.sleep(2)  # 요청 간격

    # 토픽 분류 리포트 생성
    report = create_topic_report(all_articles)
    print("Report generated. Length:", len(report))

    # 텔레그램 전송
    send_telegram(report)
    print("Script finished.")
