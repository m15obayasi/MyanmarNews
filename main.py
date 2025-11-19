import os
import json
import html
from datetime import datetime
from pathlib import Path

import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
from markdown import markdown
from requests.auth import HTTPBasicAuth


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

1. 日本語タイトルを新しく生成（内容が分かる、硬派なニュースタイトル）
2. 記事概要（です・ます調、5〜10行）
3. 背景（必要なら5〜10行）
4. 今後の見通し（5〜10行）

元記事タイトル: {title}
出典: {source}
URL: {link}

※「はい、承知しました」などの前置きは絶対に入れない
※適切に段落を改行すること
※Markdown形式で出力すること（## 見出し を含む）
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt
    )

    text = response.text.strip()

    return text   # Markdown のまま返す


# =====================================
# はてなブログ投稿（Markdown → HTML対応）
# =====================================

def post_to_hatena(md_text, original_url):
    print("Posting to Hatena Blog...")

    # Markdown → HTML
    html_body = markdown(md_text)

    # HTML内の元記事リンク追加
    html_body += f'<p>元記事URL：<a href="{html.escape(original_url)}">{html.escape(original_url)}</a></p>'

    # 最初の <h1> または 最初の行をタイトル扱いにする
    soup = BeautifulSoup(html_body, "lxml")

    # タイトル抽出
    h1 = soup.find("h1")
    if h1:
        title = h1.text.strip()
        h1.decompose()  # h1を本文から削除
    else:
        # h1 がない場合は最初の行
        title = md_text.split("\n")[0].strip().replace("#", "").strip()

    esc_title = html.escape(title)

    updated = datetime.utcnow().isoformat() + "Z"

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{esc_title}</title>
  <updated>{updated}</updated>
  <content type="text/html">
{html_body}
  </content>
</entry>
"""

    # 投稿先URL
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

    # RSS 全部取得
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

    # 新記事を1件ずつ投稿
    for a in new_articles:
        print("Summarizing:", a["title"])

        md_text = summarize_with_gemini(a)

        ok = post_to_hatena(md_text, a["link"])
        if ok:
            new_seen.add(a["link"])

    save_seen(new_seen)
    print("完了しました！")


if __name__ == "__main__":
    main()
