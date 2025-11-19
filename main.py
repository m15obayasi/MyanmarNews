import os
import json
from datetime import datetime
from pathlib import Path

import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
from requests.auth import HTTPBasicAuth
import markdown

# =====================================
# 設定
# =====================================

RSS_SOURCES = [
    ("Myanmar Now", "https://myanmar-now.org/en/feed/"),
    ("Irrawaddy", "https://www.irrawaddy.com/feed"),
]

STATE_FILE = Path("seen_articles.json")

HATENA_ID = os.environ["HATENA_ID"]
HATENA_API_KEY = os.environ["HATENA_API_KEY"]
HATENA_BLOG_ID = os.environ["HATENA_BLOG_ID"]

# Gemini 初期化
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL = "gemini-2.0-flash"

# =====================================
# seen 記録
# =====================================

def load_seen():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()

def save_seen(urls):
    STATE_FILE.write_text(json.dumps(sorted(list(urls)), ensure_ascii=False))


# =====================================
# RSS取得
# =====================================

def fetch_rss(name, url):
    print(f"Fetching RSS: {name} ...")
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(url, timeout=20, headers=headers)
        r.raise_for_status()
    except Exception as e:
        print(f"RSS取得失敗（{name}）: {e}")
        return []

    feed = feedparser.parse(r.text)

    if not feed.entries:
        print(f"RSSパース失敗（{name}）")
        return []

    articles = []
    for entry in feed.entries:
        link = entry.get("link")
        if not link:
            continue

        title = entry.get("title", "")
        summary = entry.get("summary", "")
        published = entry.get("published", "")

        articles.append({
            "source": name,
            "title": title,
            "summary": summary,
            "link": link,
            "published": published,
        })

    return articles


# =====================================
# Gemini 要約
# =====================================

def summarize_with_gemini(article):
    link = article["link"]
    title = article["title"]
    source = article["source"]

    prompt = f"""
以下はミャンマーに関するニュースです。
日本語で、以下の4点を満たす形でまとめてください。

1. 日本語タイトルを新しく生成（硬派で内容が分かる）
2. 記事概要（ですます調 5〜10行）
3. 背景（5〜10行）
4. 今後の見通し（5〜10行）

出力フォーマット（絶対にこの形式で出力）：

# タイトル

## 概要
本文…

## 背景
本文…

## 今後の見通し
本文…

元記事URL: <URL>

元記事タイトル: {title}
出典: {source}
URL: {link}

※「はい、承知しました」などの前置き禁止
※見出しは必ず Markdown の #, ## を使う
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt
    )

    return response.text.strip()


# =====================================
# Markdown → HTML 変換（重要）
# =====================================

def md_to_html(md_text: str) -> str:
    """
    必ず <h1> <h2> <p> に変換されるよう Markdown を HTML へ。
    """
    html = markdown.markdown(
        md_text,
        extensions=["extra"]
    )
    return html


# =====================================
# はてなブログ投稿
# =====================================

def post_to_hatena(title, html_body, original_url):
    print("Posting to Hatena Blog...")

    updated = datetime.utcnow().isoformat() + "Z"

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <updated>{updated}</updated>
  <content type="text/html">
<![CDATA[
{html_body}

<p>元記事URL: <a href="{original_url}">{original_url}</a></p>
]]>
  </content>
</entry>
"""

    url = f"https://blog.hatena.ne.jp/{HATENA_ID}/{HATENA_BLOG_ID}/atom/entry"

    auth = HTTPBasicAuth(HATENA_ID, HATENA_API_KEY)
    headers = {"Content-Type": "application/xml"}

    r = requests.post(url, data=xml.encode("utf-8"), headers=headers, auth=auth)

    if r.status_code in [200, 201]:
        print("Hatena投稿成功！")
        return True

    print("Hatena投稿失敗:", r.status_code, r.text)
    return False


# =====================================
# メイン処理
# =====================================

def main():
    print("==== Myanmar News Auto Poster ====")

    seen = load_seen()
    new_seen = set(seen)

    all_articles = []

    # RSS全部取得
    for name, url in RSS_SOURCES:
        items = fetch_rss(name, url)
        all_articles.extend(items)

    print("取得件数:", len(all_articles))

    # 新記事のみ抽出
    new_articles = [a for a in all_articles if a["link"] not in seen]
    print("新規記事:", len(new_articles))

    if not new_articles:
        print("新記事なし。終了します。")
        return

    # 新記事を1つずつ投稿
    for a in new_articles:
        print("Summarizing:", a["title"])

        md_text = summarize_with_gemini(a)
        html_body = md_to_html(md_text)

        # Markdown先頭のタイトル行を取得
        first_line = md_text.split("\n")[0].replace("#", "").strip()

        ok = post_to_hatena(first_line, html_body, a["link"])
        if ok:
            new_seen.add(a["link"])

    save_seen(new_seen)
    print("完了しました！")


if __name__ == "__main__":
    main()
