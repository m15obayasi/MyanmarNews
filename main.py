import os
import json
from datetime import datetime
from pathlib import Path

import requests
import feedparser
from bs4 import BeautifulSoup
from openai import OpenAI
from requests.auth import HTTPBasicAuth

# =============================
# 設定
# =============================

RSS_URL = "https://myanmar-now.org/en/rss"
STATE_FILE = Path("seen_articles.json")

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


# =============================
# seen 記録の読み書き
# =============================

def load_seen():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()

def save_seen(urls):
    STATE_FILE.write_text(json.dumps(sorted(urls), ensure_ascii=False))


# =============================
# RSS取得
# =============================

def extract_links(max_count=10):
    print("Fetching Myanmar Now RSS...")

    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(RSS_URL, timeout=20, headers=headers)
        r.raise_for_status()
    except Exception as e:
        print(f"RSS取得失敗: {e}")
        return []

    feed = feedparser.parse(r.text)

    if not feed.entries:
        print("RSSパース失敗。終了します。")
        return []

    urls = []
    for entry in feed.entries[:max_count]:
        urls.appe
