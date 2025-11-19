import os
import json
from datetime import datetime
from pathlib import Path

import requests
import feedparser
from google import genai
from requests.auth import HTTPBasicAuth

# =============================
# 設定
# =============================

RSS_SOURCES = {
    "Myanmar Now": "https://myanmar-now.org/en/rss",
    "Irrawaddy": "https://www.irrawaddy.com/feed",
    "Eleven Myanmar": "https://elevenmyanmar.com/rss.xml",
    "Myanmar News": "https://www.myanmarnews.net/rss/",
}

STATE_FILE = Path("seen_articles.json")

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

HATENA_ID = os.environ["HATENA_ID"]
HATENA_API_KEY = os.environ["HATENA_API_KEY"]
HATENA_BLOG_ID = os.environ["HATENA_BLOG_ID"]  # 例: myblog.hatenablog.com


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
# 複数RSSのまとめ取得
# =============================

def fetch_rss():
    print("==== Fetching All RSS Sources ====")
    articles = []

    headers = {"User-Agent": "Mozilla/5.0"}

    for name, url in RSS_SOURCES.items():
        print(f"Fetching {name} RSS...")

        try:
            r = requests.get(url, timeout=20, headers=headers)
            r.raise_for_status()
            feed = feedparser.parse(r.text)

            if not feed.entries:
                print(f"RSSパース失敗（{name}）")
                continue

            for entry in feed.entries[:8]:  # 各サイト最大8件
                articles.append({
                    "source": name,
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", "")
                })

        except Exception as e:
            print(f"RSS取得失敗（{name}）: {e}")

    print(f"合計取得記事数: {len(articles)}")
    return articles


# =============================
# Gemini 要約
# =============================

def summarize_with_gemini(article):
    prompt = f"""
以下はミャンマーのニュース記事です。内容を踏まえ、
「ですます調」で、読みやすく分かりやすく、
・記事の要点
・背景
・今後どうなりそうか（展望）
を日本語でまとめてください。

◆記事タイトル（英語）
{article["title"]}

◆概要
{article["summary"]}

◆URL
{article["link"]}

文章は長め・丁寧めでお願いします。
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    return response.text


# =============================
# はてなブログ投稿
# =============================

def post_to_hatena(title, body):
    print("Posting to Hatena Blog...")

    url = f"https://blog.hatena.ne.jp/{HATENA_ID}/{HATENA_BLOG_ID}/atom/entry"

    entry_xml = f"""
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <content type="text/plain"><![CDATA[{body}]]></content>
  <updated>{datetime.utcnow().isoformat()}Z</updated>
  <author><name>{HATENA_ID}</name></author>
</entry>
"""

    resp = requests.post(
        url,
        data=entry_xml.encode("utf-8"),
        auth=HTTPBasicAuth(HATENA_ID, HATENA_API_KEY),
        headers={"Content-Type": "application/xml"}
    )

    if resp.status_code not in (200, 201):
        print("Hatena 投稿失敗:", resp.status_code, resp.text)
    else:
        print("Hatena投稿成功！")


# =============================
# メイン処理
# =============================

def main():
    seen = load_seen()
    all_articles = fetch_rss()

    new_articles = [a for a in all_articles if a["link"] not in seen]

    print(f"New articles: {len(new_articles)}")
    if not new_articles:
        print("新着なし。終了します。")
        return

    digest_text = "【ミャンマー主要ニュースまとめ】\n\n"

    # 最大5記事だけ要約
    for a in new_articles[:5]:
        print(f"Summarizing: {a['title']}")
        summary = summarize_with_gemini(a)

        digest_text += f"■{a['title']}\n（出典: {a['source']}）\n"
        digest_text += summary + "\n\n"
        digest_text += f"URL: {a['link']}\n\n"
        seen.add(a["link"])

    # はてなブログ投稿
    today = datetime.now().strftime("%Y-%m-%d")
    post_to_hatena(f"ミャンマー主要ニュースまとめ（{today}）", digest_text)

    save_seen(seen)


# =============================
# 実行
# =============================

if __name__ == "__main__":
    main()
