import os
import json
from datetime import datetime
from pathlib import Path

import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from requests.auth import HTTPBasicAuth


# =============================
# 設定
# =============================

NEWS_SOURCES = [
    ("DVB", "https://english.dvb.no/feed/"),
    ("Mizzima", "https://mizzima.com/feed"),
]

STATE_FILE = Path("seen_articles.json")

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


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

def fetch_rss(source_name, rss_url, max_count=10):
    print(f"Fetching {source_name} RSS...")

    try:
        r = requests.get(rss_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    except Exception as e:
        print(f"RSS取得失敗（{source_name}）: {e}")
        return []

    feed = feedparser.parse(r.text)

    if not feed.entries:
        print(f"{source_name} RSS パース失敗。")
        return []

    urls = []
    for entry in feed.entries[:max_count]:
        title = entry.title if "title" in entry else "No title"
        url = entry.link
        urls.append({"source": source_name, "title": title, "url": url})

    print(f"{source_name}: {len(urls)} 件取得")
    return urls


# =============================
# 記事本文取得
# =============================

def fetch_article(url):
    print(f"Fetching article: {url}")
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    except Exception as e:
        print(f"記事取得失敗: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")

    # メイン記事コンテナ探索（DVB & Mizzima に対応）
    selectors = [
        "article",
        ".post-content",
        ".entry-content",
        ".content",
        ".node-content",
    ]

    for sel in selectors:
        block = soup.select_one(sel)
        if block:
            text = block.get_text(separator="\n", strip=True)
            if len(text) > 200:
                return text

    print("→ 専用の要素が見つからず、全文抽出もうまくいかず")
    return None


# =============================
# Gemini 要約（日本語）
# =============================

def summarize(text, source_name, title, url):
    prompt = f"""
あなたはプロの国際ニュース解説者です。
以下はミャンマーに関するニュース記事です。
これを **ですます調** で、構造化されたわかりやすい日本語要約にしてください。

要求：
- 文章量は多め（400〜700文字）
- 背景や補足文脈も説明
- 影響・今後の展望も含める
- タイトルも自然な日本語に変換する
- 途中に箇条書きも使用OK

【メディア】{source_name}  
【記事タイトル】{title}  
【記事URL】{url}  

【本文】
{text}
"""

    try:
        res = client.models.generate_content(
            model="gemini-1.5-flash-latest",
            contents=prompt
        )
        return res.text
    except Exception as e:
        print("Gemini要約失敗:", e)
        return None


# =============================
# はてなブログ投稿
# =============================

def post_to_hatena(title, content):
    USER = os.environ["HATENA_ID"]
    APIKEY = os.environ["HATENA_API_KEY"]
    BLOG_ID = os.environ["HATENA_BLOG_ID"]

    endpoint = f"https://blog.hatena.ne.jp/{USER}/{BLOG_ID}/atom/entry"

    updated = datetime.utcnow().isoformat() + "Z"

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <updated>{updated}</updated>
  <author><name>{USER}</name></author>
  <content type="text/plain">
{content}
  </content>
</entry>
"""

    headers = {"Content-Type": "application/xml"}

    r = requests.post(endpoint, data=xml.encode("utf-8"),
                      auth=HTTPBasicAuth(USER, APIKEY),
                      headers=headers)

    if r.status_code not in [200, 201]:
        print("Hatena 投稿失敗:", r.status_code)
        print(r.text)
    else:
        print("Hatena 投稿成功！")


# =============================
# メイン処理
# =============================

def main():
    seen = load_seen()
    all_new_items = []

    # ■③ DVB + ④ Mizzima から収集
    for source_name, rss in NEWS_SOURCES:
        items = fetch_rss(source_name, rss)
        for it in items:
            if it["url"] not in seen:
                all_new_items.append(it)

    print(f"New articles: {len(all_new_items)}")

    if not all_new_items:
        print("新着なし。終了します。")
        return

    # 記事ごとに本文→要約
    summaries = []
    used_urls = []

    for it in all_new_items:
        body = fetch_article(it["url"])
        if not body:
            continue

        summary = summarize(body, it["source"], it["title"], it["url"])
        if summary:
            summaries.append(f"■ **{it['source']}：{it['title']}（日本語タイトル変換済み）**\n\n{summary}\n\n---\n")
            used_urls.append(it["url"])

    # seen 更新
    if used_urls:
        seen |= set(used_urls)
        save_seen(seen)

    # はてなに投稿
    today = datetime.utcnow().strftime("%Y-%m-%d")
    post_title = f"ミャンマー情勢ニュースまとめ（DVB & Mizzima） - {today}"

    post_body = "# ミャンマー情勢ニュースまとめ\n\n" + "".join(summaries)

    post_to_hatena(post_title, post_body)


if __name__ == "__main__":
    main()
