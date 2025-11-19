import os
import json
from datetime import datetime
from pathlib import Path

import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
from requests.auth import HTTPBasicAuth


# =====================================
# 設定
# =====================================

RSS_SOURCES = [
    ("Myanmar Now", "https://myanmar-now.org/en/feed/"),
    ("Irrawaddy", "https://www.irrawaddy.com/feed"),
]

STATE_FILE = Path("seen_articles.json")

HATENA_ID = os.environ["HATENA_ID"]          # 例: m15obayasi
HATENA_API_KEY = os.environ["HATENA_API_KEY"]
HATENA_BLOG_ID = os.environ["HATENA_BLOG_ID"]  # 例: yangon.hateblo.jp

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
            "summary": BeautifulSoup(summary, "html.parser").get_text(),
            "link": link,
            "published": published,
        })

    return articles


# =====================================
# Gemini 要約（A案仕様）
# =====================================

def summarize_with_gemini(article):
    link = article["link"]
    title = article["title"]
    source = article["source"]

    prompt = f"""
以下はミャンマーに関するニュースです。
日本語で、以下の形式に従ってまとめてください。

# 日本語タイトル（内容が分かる硬派なタイトル）
※ 新しく生成すること（元記事タイトルの直訳でも意訳でも可）

## 記事概要（です・ます調で5〜10行）
- 重要ポイントを整理して書く
- 読みやすく改行を入れる

## 背景（必要なら5〜10行）
- 歴史・政治・軍事情勢など記事理解に必要な情報を書く

## 今後の見通し（5〜10行）
- 予測や影響などを落ち着いた口調で書く

元記事タイトル: {title}
出典: {source}
URL: {link}

※ 前置きの「承知しました」「この記事は〜」は絶対に書かない
※ Markdown形式で整形すること
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt
    )

    text = response.text.strip()
    return text  # Markdownとしてそのまま利用


# =====================================
# はてなブログ投稿
# =====================================

def post_to_hatena(jp_md_text, original_url):
    print("Posting to Hatena Blog...")

    # Markdownからタイトル抽出（最初の行）
    first_line = jp_md_text.split("\n")[0]
    title = first_line.replace("#", "").strip()

    updated = datetime.utcnow().isoformat() + "Z"

    # 本文末尾に元URL追記
    body = jp_md_text + f"\n\n---\n元記事URL: {original_url}\n"

    # AtomPub形式
    xml = f"""
<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <updated>{updated}</updated>
  <content type="text/markdown">
{body}
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

        ok = post_to_hatena(md_text, a["link"])
        if ok:
            new_seen.add(a["link"])

    # seen を更新
    save_seen(new_seen)
    print("完了しました！")


if __name__ == "__main__":
    main()
