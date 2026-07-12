import os
import re
import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

def send_telegram(text):
    lines = text.split('\n')
    chunks, cur = [], ""
    for line in lines:
        if len(cur) + len(line) + 1 > 3800:
            if cur: chunks.append(cur)
            cur = line
        else: cur = (cur + "\n" + line) if cur else line
    if cur: chunks.append(cur)

    for idx, chunk in enumerate(chunks):
        if len(chunks) > 1: chunk = f"[{idx+1}/{len(chunks)}]\n{chunk}"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"}
        resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json=payload, timeout=10)
        if resp.status_code != 200:
            plain = re.sub(r'<.*?>', '', chunk)
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": plain}, timeout=10)
    print("All chunks sent successfully.")

if __name__ == "__main__":
    with open("report.txt", "r", encoding="utf-8") as f:
        report = f.read()
    send_telegram(report)
