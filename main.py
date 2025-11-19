import os
import json
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from google import genai
from google.genai import types

# ==============================
# RSS Sources (B案: Irrawaddyのみ)
# ==============================
RSS_SOURCES = [
    ("Irrawaddy", "https://www.irrawaddy.com/feed"),
]

SEEN_FILE = "seen_articles.json"


# ==============================
# Load / Save seen articles
# ==============================
def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except:
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)


# ==============================
# Fetch RSS
# ==============================
def fetch_rss():
    articles = []
    for name, url in RSS_SOURCES:
        print(f"Fetching RSS: {name} ...")
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                title = e.get("title", "").strip()
                link = e.get("link", "").strip()
                summary = BeautifulSoup(e.get("summary", ""), "lxml").get_text(" ", strip=True)
                published = e.get("published", "")
                articles.append({
                    "source": name,
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "published": published,
                })
        except Exception as e:
            print(f"RSS取得失敗（{name}）:", e)

    return articles


# ==============================
# Gemini Summary Generator
# ==============================
def summarize_with_gemini(article):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
次の記事について、日本語で以下の構成を守ってブログ記事用のMarkdownを生成してください。

【生成ルール】
- 必ず **Markdown形式**（# 見出し、## 小見出し、箇条書き）を使う
- 「概要 → 背景 → 今後の見通し」の3セクションを必ず作る
- ニュースの重要性・地域情勢・国際関係なども補足して文章量を増やす
- 語尾は「です／ます」で統一する
- 段落の間には必ず空行を入れる
- Markdown を壊さないようにする
- 最後に **元記事URL** を掲載する

【対象記事】
タイトル: {article["title"]}
URL: {article["link"]}
概要: {article["summary"]}

それでは Markdown を生成してください。
"""

    res = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[prompt],
        config=types.GenerateContentConfig(
            max_output_tokens=1500,
            temperature=0.3,
        )
    )

    return res.text


# ==============================
# Generate Japanese title
# ==============================
def generate_japanese_title(article):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
次のニュース記事タイトルを、日本語で自然なニュース記事タイトルに翻訳してください。

元のタイトル:
{article["title"]}

条件:
- 不自然な直訳にしない
- 報道記事として自然な文体にする
- 30〜60字程度
"""

    res = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[prompt],
        config=types.GenerateContentConfig(
            max_output_tokens=100,
            temperature=0.2,
        )
    )

    return res.text.strip()


# ==============================
# Post to Hatena Blog
# ==============================
def post_to_hatena(title, content_md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app">
  <title>{title}</title>
  <content type="text/markdown">
{content_md}
  </content>
</entry>
"""

    headers = {
        "Content-Type": "application/xml",
    }

    print("Posting to Hatena Blog...")

    r = requests.post(url, data=xml.encode("utf-8"), auth=(hatena_id, api_key))
    if r.status_code not in [200, 201]:
        print("Hatena投稿失敗:", r.status_code, r.text)
        return False

    print("Hatena投稿成功")
    return True


# ==============================
# Main
# ==============================
def main():
    print("==== Myanmar News Auto Poster ====")

    seen = load_seen()
    articles = fetch_rss()

    print(f"取得件数: {len(articles)}")

    new_articles = [a for a in articles if a["link"] not in seen]

    print(f"新規記事: {len(new_articles)}")

    if not new_articles:
        print("新記事なし。終了します。")
        return

    for a in new_articles:
        print(f"\nSummarizing: {a['title']}")

        jp_title = generate_japanese_title(a)
        md_text = summarize_with_gemini(a)

        # 投稿
        post_to_hatena(jp_title, md_text)

        # 記録
        seen.add(a["link"])
        save_seen(seen)


if __name__ == "__main__":
    main()
