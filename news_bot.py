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

def fetch_news(query, max_results=3):
    """DDG 뉴스 검색 + Jina Reader 본문 추출"""
    with DDGS() as ddgs:
        news = list(ddgs.news(query, max_results=max_results))
    contents = []
    for item in news:
        url = item.get("url")
        if not url:
            continue
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

def summarize_category(category, articles):
    if not articles:
        return f"📌 {category}\n오늘 주요 뉴스가 없습니다.\n"
    prompt = f"{category} 뉴스 요약:\n"
    for i, a in enumerate(articles):
        prompt += f"{i+1}. 제목: {a['title']}\n내용: {a['content']}\n\n"
    prompt += "\n위 기사들을 3~5줄로 간결하게 요약해주세요."
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return f"📌 {category}\n{response.text}\n"
    except Exception as e:
        return f"📌 {category}\n요약 실패: {e}\n"

def send_telegram(text):
    """텔레그램으로 메시지 전송"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text[:4000]}
    resp = requests.post(url, json=payload, timeout=10)
    if resp.status_code == 200:
        print("Telegram message sent successfully.")
    else:
        print(f"Telegram send failed: {resp.text}")

# ===== 메인 실행 =====
if __name__ == "__main__":
    categories = {
        "정치": "정치",
        "연예": "연예",
        "증권": "증권"
    }
    final_report = "📰 오늘의 뉴스 요약\n\n"
    for cat, query in categories.items():
        print(f"Fetching {cat}...")
        try:
            articles = fetch_news(query, max_results=3)
            print(f"{cat} articles count: {len(articles)}")  # 추가
            summary = summarize_category(cat, articles)
        except Exception as e:
            summary = f"📌 {cat}\n뉴스 수집 중 오류: {e}\n"
        final_report += summary + "\n"
        time.sleep(2)

    print("Final report length:", len(final_report))  # 추가
    print(final_report)
    print("Attempting to send via Telegram...")  # 추가
    send_telegram(final_report)
    print("Script finished.")  # 추가
