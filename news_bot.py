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

def fetch_news(query, max_results, region='kr-kr'):
    """
    DDG 뉴스 검색 + Jina Reader 본문 추출
    region: 'kr-kr' (한국), 'us-en' (미국/국제)
    """
    with DDGS() as ddgs:
        news = list(ddgs.news(query=query, max_results=max_results, region=region))
    contents = []
    for item in news:
        url = item.get("url")
        if not url:
            continue
        # Jina Reader로 본문 일부 추출 (실패해도 기사 제목/URL은 저장)
        try:
            resp = requests.get(f"https://r.jina.ai/{url}", timeout=15)
            content = resp.text[:2000]
        except:
            content = ""
        contents.append({
            "title": item.get("title", "제목 없음"),
            "url": url,
            "content": content
        })
    return contents

def summarize_category(category, articles, max_retries=2):
    """
    카테고리별 뉴스 요약 시도. 실패하면 제목 리스트만 반환.
    항상 (text_summary_or_titles, links_list) 튜플을 반환.
    """
    links = [f"- {a['title']}: {a['url']}" for a in articles]

    if not articles:
        return f"📌 {category}\n오늘 주요 뉴스가 없습니다.\n", []

    prompt = f"{category} 뉴스 요약:\n"
    for i, a in enumerate(articles):
        prompt += f"{i+1}. 제목: {a['title']}\n내용: {a['content']}\n\n"
    prompt += "\n위 각 기사를 **한 문장**으로 요약해주세요."

    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            summary_text = f"📌 {category}\n{response.text}\n"
            return summary_text, links
        except Exception as e:
            if attempt < max_retries:
                print(f"{category} retry {attempt+1}/{max_retries} due to error: {e}")
                time.sleep(3)
            else:
                # 요약 실패 시 제목과 링크만 제공
                fallback = f"📌 {category}\n요약을 생성하지 못했지만, 주요 기사 제목과 링크입니다.\n"
                return fallback, links

def send_telegram(text):
    """텔레그램 메시지 전송"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text[:4000]}
    resp = requests.post(url, json=payload, timeout=10)
    if resp.status_code == 200:
        print("Telegram message sent successfully.")
    else:
        print(f"Telegram send failed: {resp.text}")

# ===== 메인 =====
if __name__ == "__main__":
    # 카테고리 정의: (이름, 검색어, 개수, region)
    categories = [
        ("정치", "정치", 5, "kr-kr"),
        ("사회", "사회", 5, "kr-kr"),
        ("증권/경제", "증권 경제", 10, "kr-kr"),
        ("국제", "world news", 5, "us-en")
    ]

    seen_urls = set()  # 중복 방지용 URL 저장소
    final_report = "📰 오늘의 뉴스 요약\n\n"

    for cat_name, query, count, region in categories:
        print(f"Fetching {cat_name}...")
        try:
            articles = fetch_news(query, max_results=count, region=region)

            # 중복 URL 제거
            unique_articles = []
            for a in articles:
                if a['url'] not in seen_urls:
                    seen_urls.add(a['url'])
                    unique_articles.append(a)
            print(f"{cat_name} unique articles count: {len(unique_articles)}")

            summary, links = summarize_category(cat_name, unique_articles)
            final_report += summary
            if links:
                final_report += "🔗 관련 기사:\n" + "\n".join(links) + "\n"
            final_report += "\n"
        except Exception as e:
            final_report += f"📌 {cat_name}\n뉴스 수집 중 오류: {e}\n\n"
        time.sleep(2)  # 요청 간격 유지

    # 텔레그램 메시지가 너무 길면 나눠서 보내기 (간단히 3500자씩 자르기)
    if len(final_report) > 3500:
        chunks = [final_report[i:i+3500] for i in range(0, len(final_report), 3500)]
        for idx, chunk in enumerate(chunks):
            send_telegram(f"[{idx+1}/{len(chunks)}]\n{chunk}")
    else:
        send_telegram(final_report)

    print("Script finished.")
