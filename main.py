import os
import json
from datetime import datetime
from pathlib import Path
import html

import requests
import feedparser
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

HATENA_ID = os.environ["HATENA_ID"]            # 例: m15obayasi
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
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except:
            return set()
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

1. 日本語タイトルを新しく生成（内容が分かる硬派なニュースタイトル）
2. 記事概要（ですます調 5〜10行）
3. 背景（必要なら5〜10行）
4. 今後の見通し（5〜10行）

元記事タイトル: {title}
出典: {source}
URL: {link}

※「はい、承知しました」などの前置きは禁止
※ Markdown記号(#, *, >, -)を使わないこと
※ XMLに安全な日本語テキストのみ使用すること
"""

    # 最大3回リトライ（429対策）
    for i in range(3):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
            )
            text = response.text
            break
        except Exception as e:
            print(f"Geminiエラー: {e}  retry {i+1}/3")
            if i == 2:
                raise

    lines = text.strip().split("\n")
    jp_title = lines[0].strip()
    body = "\n".join(lines[1:]).strip()

    return jp_title, body


# =====================================
# はてなブログ投稿
# =====================================

def post_to_hatena(title, body, original_url):
    print("Posting to Hatena Blog...")

    # XML用にエスケープ
    esc_title = html.escape(title)
    esc_body = html.escape(body)
    esc_link = html.escape(original_url)

    updated = datetime.utcnow().isoformat() + "Z"

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{esc_title}</title>
  <updated>{updated}</updated>
  <content type="text/plain">
{esc_body}

元記事URL: {esc_link}
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

    # ★ 1件だけ投稿（Gemini429回避 & ブログ安定運用）
    a = new_articles[0]

    print("Summarizing:", a["title"])
    jp_title, jp_body = summarize_with_gemini(a)

    ok = post_to_hatena(jp_title, jp_body, a["link"])
    if ok:
        new_seen.add(a["link"])

    # seen を更新
    save_seen(new_seen)
    print("完了しました！")


if __name__ == "__main__":
    main()
