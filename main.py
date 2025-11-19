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

RSS_URL = "https://myanmar-now.org/en/rss"
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

def extract_links(max_count=10):
    print("Fetching Myanmar Now RSS...")

    try:
        r = requests.get(RSS_URL, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"RSS取得失敗: {e}")
        return []

    feed = feedparser.parse(r.text)

    if not feed.entries:
        print("RSSパース失敗。終了します。")
        return []

    print(f"RSSから {len(feed.entries[:max_count])} 件取得")

    urls = []
    for entry in feed.entries[:max_count]:
        urls.append(entry.link)

    return urls


# =============================
# 記事本文の取得（完全対応版）
# =============================

def fetch_article(url):
    print(f"Fetching article: {url}")

    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    except Exception as e:
        print(f"記事取得失敗: {e}")
        return None, None

    soup = BeautifulSoup(r.text, "html.parser")

    # --- タイトル取得 ---
    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else "No Title"

    # --- 本文候補の複数パターン ---
    selectors = [
        "div.content-body",                       # 最近の Myanmar Now の本文
        'div[property="content:encoded"]',        # 別の本文パターン
        "div.field-item.even",                    # Drupalベースの本文
    ]

    body = ""

    for selector in selectors:
        section = soup.select_one(selector)
        if section:
            paragraphs = [p.get_text(strip=True) for p in section.find_all("p")]
            body = "\n".join(paragraphs)
            if body.strip():
                break

    # --- フォールバック（最終手段） ---
    if not body.strip():
        print("→ 専用クラスで本文を取得できず。フォールバックとして <article> 全文を探索します")
        article = soup.find("article")
        if article:
            paragraphs = [p.get_text(strip=True) for p in article.find_all("p")]
            body = "\n".join(paragraphs)

    # --- それでもダメなら空のまま ---
    if not body.strip():
        print("本文取得失敗（どのパターンでも見つからず）")

    return title, body


# =============================
# Gemini 要約
# =============================

def summarize(title, body):
    if not body:
        return "本文が取得できませんでした。"

    prompt = f"""
あなたはプロの国際情勢アナリストです。
以下のミャンマー関連ニュースを **日本語で要約** し、
最後に **今後の展望を3〜5点、箇条書き** で書いてください。

---
記事タイトル:
{title}

本文:
{body}
---

出力フォーマット：

【要点要約】
・……

【今後の展望】
・……
"""

    try:
        res = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt
        )
        return res.text
    except Exception as e:
        print(f"Gemini要約失敗: {e}")
        return "要約生成に失敗しました。"


# =============================
# はてなブログ投稿
# =============================

def post_to_hatena(title, content):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]  # 例: yangon.tokyo

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    headers = {
        "Content-Type": "application/xml",
    }

    xml = f"""
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <content type="text/plain"><![CDATA[{content}]]></content>
</entry>
"""

    r = requests.post(
        url,
        data=xml.encode("utf-8"),
        headers=headers,
        auth=HTTPBasicAuth(hatena_id, api_key)
    )

    if r.status_code in (200, 201):
        print("Hatena 投稿成功！")
    else:
        print(f"Hatena 投稿失敗: {r.status_code}")
        print(r.text)


# =============================
# メイン
# =============================

def main():
    seen = load_seen()

    # 新しいRSSリンク取得
    links = extract_links()
    if not links:
        print("リンクなし。終了")
        return

    # 差分だけ処理
    new_links = [url for url in links if url not in seen]
    print(f"New articles: {len(new_links)}")

    if not new_links:
        print("新着なし。終了します。")
        return

    all_summaries = ""

    for url in new_links:
        title, body = fetch_article(url)
        summary = summarize(title, body)

        all_summaries += f"## {title}\n\n{summary}\n\n---\n\n"

    # はてな投稿
    today = datetime.now().strftime("%Y-%m-%d")
    post_to_hatena(
        f"Myanmar Now ニュースまとめ（{today}）",
        all_summaries
    )

    # 記録更新
    seen.update(new_links)
    save_seen(seen)


if __name__ == "__main__":
    main()
