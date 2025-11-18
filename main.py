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
        urls.append(entry.link)

    print(f"RSSから {len(urls)} 件取得")
    return urls


# =============================
# 記事本文取得
# =============================

def fetch_article(url):
    print(f"Fetching article: {url}")

    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(url, timeout=20, headers=headers)
        r.raise_for_status()
    except Exception as e:
        print(f"記事取得失敗: {e}")
        return None, None

    soup = BeautifulSoup(r.text, "html.parser")

    # タイトル
    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else url

    # 本文（pタグを抽出）
    paragraphs = [
        p.get_text(strip=True)
        for p in soup.find_all("p")
        if len(p.get_text(strip=True)) > 40
    ]

    if not paragraphs:
        return title, None

    body = "\n".join(paragraphs)
    return title, body


# =============================
# GPT要約
# =============================

def summarize(title, body):
    prompt = f"""
以下の英語ニュース記事を日本語でまとめてください。

1. 概要（3〜6文）
2. 今後の展望（2〜4文）

---
TITLE: {title}
ARTICLE:
{body}
---
""".strip()

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )

    return res.choices[0].message.content


# =============================
# はてなブログ投稿
# =============================

def post_to_hatena(title, html):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    endpoint = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    xml = f"""
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <content type="text/html"><![CDATA[
  {html}
  ]]></content>
  <updated>{datetime.utcnow().isoformat()}Z</updated>
  <author><name>{hatena_id}</name></author>
  <category term="Myanmar News"/>
</entry>
""".strip()

    r = requests.post(
        endpoint,
        data=xml.encode("utf-8"),
        auth=HTTPBasicAuth(hatena_id, api_key),
        headers={"Content-Type": "application/xml"}
    )

    if r.status_code not in (200, 201):
        raise Exception(f"HATENA POST FAILED: {r.status_code}\n{r.text}")

    print("はてなブログに投稿しました！")


# =============================
# MAIN
# =============================

def main():
    today = datetime.now().strftime("%Y-%m-%d")

    seen = load_seen()
    links = extract_links()

    new_links = [u for u in links if u not in seen]
    print(f"New articles: {len(new_links)}")

    if not new_links:
        print("新着なし。終了します。")
        return

    html = f"<h1>Myanmar Now ニュースまとめ ({today})</h1><hr>"

    for url in new_links:
        title, body = fetch_article(url)
        if not body:
            continue

        summary = summarize(title, body)

        html += f"<h2>{title}</h2>"
        html += f'<p><a href="{url}" target="_blank">{url}</a></p>'
        html += f"<pre>{summary}</pre><hr>"

        seen.add(url)

    save_seen(seen)

    post_to_hatena(f"Myanmar Now ニュースまとめ ({today})", html)


# =============================
# 実行
# =============================

if __name__ == "__main__":
    main()
