def create_topic_report(all_articles):
    if not all_articles:
        return "📰 오늘 수집된 뉴스가 없습니다."

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
