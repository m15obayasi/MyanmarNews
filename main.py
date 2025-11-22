import os
import json
import time
import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
import markdown
import re

SEEN_FILE = "seen_articles.json"


# --------------------------------------------------
# 429 / APIエラーに強い Gemini 呼び出しラッパー
# --------------------------------------------------
def call_gemini_with_retry(client, model, prompt, max_retries=5):
    for attempt in range(1, max_retries + 1):
        try:
            res = client.models.generate_content(
                model=model,
                contents=prompt
            )
            return res.text

        except Exception as e:
            msg = str(e)

            # 429 の retry-after を拾う
            retry_time = 30
            if "RetryInfo" in msg or "retryDelay" in msg:
                # 例: retryDelay': '48s'
                m = re.search(r"retryDelay.*?(\d+)", msg)
                if m:
                    retry_time = int(m.group(1))

            print(f"[Gemini] エラー発生 ({attempt}/{max_retries}) → {retry_time} 秒スリープ")
            time.sleep(retry_time)

    raise RuntimeError("Gemini API 再試行上限に達しました。")


# --------------------------------------------------
# seen_articles.json の読み込み/保存
# --------------------------------------------------
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)


# --------------------------------------------------
# RSS 取得
# --------------------------------------------------
def fetch_rss(url, name):
    print(f"Fetching RSS: {name} ...")
    feed = feedparser.parse(url)
    if feed.bozo:
        print(f"RSS取得失敗（{name}）:", feed.bozo_exception)
        return []
    return feed.entries


# --------------------------------------------------
