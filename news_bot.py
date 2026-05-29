import os
import time
import re
import requests
from collections import Counter
from datetime import datetime
from ddgs import DDGS

# --- 환경 변수 ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

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

def extract_keywords_from_titles(titles):
    """모든 기사 제목에서 핵심 단어(2글자 이상 한글, 영문)를 추출하여 빈도 계산"""
    stopwords = [
        '오늘', '뉴스', '기사', '속보', '단독', '이유', '가운데', '관련', '전망', '분석', '확인',
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should',
        'may', 'might', 'can', 'shall', 'you', 'i', 'he', 'she', 'it', 'we', 'they',
        'this', 'that', 'these', 'those', 'in', 'on', 'at', 'to', 'for', 'of', 'with',
        'by', 'from', 'up', 'about', 'into', 'through', 'during', 'before', 'after',
        'above', 'below', 'between', 'out', 'off', 'over', 'under', 'again', 'further',
        'then', 'once', 'here', 'there', 'when', 'where', 'why', 'how', 'all', 'both',
        'each', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not',
        'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just', 'now'
    ]
    words = []
    for title in titles:
        clean = re.sub(r'[^a-zA-Z0-9가-힣\s]', ' ', title)
        tokens = clean.split()
        for token in tokens:
            if token.isalpha() and len(token) >= 2:
                if token.lower() not in stopwords:
                    words.append(token.lower())
    return Counter(words)

def build_issue_sections(all_articles, top_n=5):
    """키워드 빈도 기반으로 이슈 섹션 구축 (Gemini 없이 순수 파이썬)"""
    titles = [a['title'] for a in all_articles]
    if not titles:
        return ""
    
    keyword_counts = extract_keywords_from_titles(titles)
    top_keywords = [k for k, v in keyword_counts.most_common(top_n)]
    
    sections = []
    for keyword in top_keywords:
        matched_articles = [a for a in all_articles if keyword in a['title'].lower()]
        if not matched_articles:
            continue
        
        section = f"📌 오늘의 핵심 키워드: '{keyword}' (총 {len(matched_articles)}건)\n"
        for a in matched_articles[:5]:
            section += f"- {a['title']}: {a['url']}\n"
        sections.append(section)
    
    if not sections:
        return ""
    
    return "🔥 오늘의 주요 이슈 (키워드 빈도 분석)\n\n" + "\n".join(sections)

def build_category_sections(cat_articles):
    """카테고리별 기사 리스트 생성"""
    report = ""
    for cat_name, articles in cat_articles.items():
        report += f"\n{cat_name}\n"
        if articles:
            for a in articles:
                report += f"- {a['title']}: {a['url']}\n"
        else:
            report += "관련 기사를 찾을 수 없습니다.\n"
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
    print("All chunks sent successfully.")

# ===== 메인 실행 =====
if __name__ == "__main__":
    # 카테고리 정의
    categories = [
        ("🇺🇸 미국 주식", "S&P 500 OR NASDAQ OR Dow Jones OR Fed OR earnings OR stock market OR Wall Street OR AI stock OR artificial intelligence stock OR AI chip OR tech stocks OR Magnificent Seven OR AAPL OR MSFT OR GOOGL OR AMZN OR NVDA OR TSLA OR META", 25, "us-en"),
        ("🇰🇷 정치/시사", "정치 OR 국회 OR 대통령 OR 외교 OR 시사 OR 북한 OR 안보", 25, "kr-kr"),
        ("🇰🇷 한국 증시/경제", "코스피 OR 코스닥 OR 증권 OR 주식 OR 경제 OR 금리 OR AI 주식 OR 인공지능 주식 OR AI 반도체 OR 삼성전자 OR SK하이닉스", 25, "kr-kr"),
        ("🌍 국제 뉴스", "world news OR geopolitics OR IMF OR UN OR summit OR NATO OR global economy", 25, "us-en"),
        ("🚨 국내 돌발 뉴스", "속보", 3, "kr-kr"),  # 국내 돌발 3건
        ("🚨 해외 돌발 뉴스", "world breaking news", 2, "us-en")  # 해외 돌발 2건
    ]

    # 1단계: 카테고리별 기사 수집 + 중복 제거
    cat_articles = {}
    seen_urls = set()

    for cat_name, query, count, region in categories:
        print(f"Fetching {cat_name}...")
        try:
            articles = fetch_news(query, max_results=count, region=region, timelimit='d')
            unique = []
            for a in articles:
                if a['url'] not in seen_urls:
                    seen_urls.add(a['url'])
                    unique.append(a)
            cat_articles[cat_name] = unique
            print(f"  {cat_name}: {len(articles)} fetched, {len(unique)} unique (total: {len(seen_urls)})")
        except Exception as e:
            print(f"  {cat_name} error: {e}")
            cat_articles[cat_name] = []
        time.sleep(2)

    # 2단계: 전체 기사 리스트 생성 (빈도 분석용)
    all_articles = []
    for articles in cat_articles.values():
        all_articles.extend(articles)

    # 3단계: 리포트 생성
    report = get_date_and_weather() + "\n\n"
    
    # 오늘의 주요 이슈 (빈도 기반)
    issue_section = build_issue_sections(all_articles, top_n=5)
    if issue_section:
        report += issue_section + "\n\n"
    
    # 카테고리별 상세 뉴스
    report += "📰 카테고리별 뉴스\n"
    report += build_category_sections(cat_articles)

    # 4단계: 텔레그램 전송
    print(f"Report generated. Length: {len(report)}")
    send_telegram(report)
    print("Script finished.")
