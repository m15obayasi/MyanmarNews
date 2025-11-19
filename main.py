import os
import json
import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
import markdown

SEEN_FILE = "seen_articles.json"

# --------------------------------------------------
# 読み込んだ記事ID管理
# --------------------------------------------------
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)


# --------------------------------------------------
# RSSを取得
# --------------------------------------------------
def fetch_rss(url, name):
    print(f"Fetching RSS: {name} ...")
    feed = feedparser.parse(url)
    if feed.bozo:
        print(f"RSS取得失敗（{name}）:", feed.bozo_exception)
        return []
    return feed.entries


# --------------------------------------------------
# Geminiで記事生成
# --------------------------------------------------
def generate_markdown(article):
    print("Generating article with Gemini AI...")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    # Gemini が余計な翻訳案などを絶対に書かないよう強めの制約
    prompt = f"""
【重要】
以下の形式以外は一切出力しないでください。
「翻訳案」「候補案」「注意書き」「解説」「補足」「他の形式」は絶対に書かないでください。

必ず次の Markdown 構造のみで出力してください：

# 日本語タイトル

## 概要
（要約と重要ポイント）

## 背景
（時系列・政治状況・地理関係・登場勢力の説明）

## 今後の見通し
（今後予測される展開・国際的影響）

元記事URL: {article["link"]}


--- 記事情報 ---
英語タイトル:
{article["title"]}

概要:
{article["summary"]}

本文（英語の抜粋・サマリ）:
{article["content"]}

以上を踏まえて、日本語で記事を生成してください。
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    return response.text


# --------------------------------------------------
# はてなブログへ投稿（Markdown→HTML変換つき）
# --------------------------------------------------
def post_to_hatena(title, content_md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    # Markdown → HTML変換
    content_html = markdown.markdown(content_md, extensions=["extra"])

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app">

  <title>{title}</title>

  <content type="text/html">
{content_html}
  </content>

</entry>
"""

    headers = {"Content-Type": "application/xml"}

    print("Posting to Hatena Blog...")

    r = requests.post(url, data=xml.encode("utf-8"), auth=(hatena_id, api_key))
    if r.status_code not in [200, 201]:
        print("Hatena投稿失敗:", r.status_code, r.text)
        return False

    print("Hatena投稿成功")
    return True


# --------------------------------------------------
# メイン処理
# --------------------------------------------------
def main():
    print("==== Myanmar News Auto Poster ====")

    seen = load_seen()

    # 対象RSS
    RSS_SOURCES = [
        ("Irrawaddy", "https://www.irrawaddy.com/feed"),
        # Myanmar Now は 403 出るので後で代理サーバーを挟む対策が必要
    ]

    new_articles = []

    for name, url in RSS_SOURCES:
        entries = fetch_rss(url, name)
        print("取得件数:", len(entries))

        for e in entries:
            link = e.get("link")
            if not link or link in seen:
                continue

            summary = BeautifulSoup(e.get("summary", ""), "html.parser").get_text()
            content = e.get("content", [{"value": summary}])[0]["value"]

            new_articles.append({
                "id": link,
                "title": e.get("title", ""),
                "summary": summary,
                "content": BeautifulSoup(content, "html.parser").get_text(),
                "link": link
            })

    print("新規記事:", len(new_articles))

    if not new_articles:
        print("新記事なし。終了します。")
        return

    for article in new_articles:
        md = generate_markdown(article)

        # Markdown の最初の行をタイトルにする
        lines = md.split("\n")
        safe_title = lines[0].replace("#", "").strip()

        print("投稿タイトル:", safe_title)

        ok = post_to_hatena(safe_title, md)

        if ok:
            seen.add(article["id"])
            save_seen(seen)


# --------------------------------------------------
if __name__ == "__main__":
    main()
