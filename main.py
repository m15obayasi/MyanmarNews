import os
import json
import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
import markdown

SEEN_FILE = "seen_articles.json"


# ======================================================
# seen_articles.json 読み書き
# ======================================================
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)


# ======================================================
# RSS FEED 取得
# ======================================================
def fetch_rss(url, name):
    print(f"Fetching RSS: {name} ...")
    feed = feedparser.parse(url)
    if feed.bozo:
        print(f"RSS取得失敗（{name}）:", feed.bozo_exception)
        return []
    return feed.entries


# ======================================================
# Gemini による記事生成（Markdown）
# ======================================================
def generate_markdown(article):
    print("Generating article with Gemini...")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    # 🔥 強めのプロンプト（翻訳案・候補など絶対禁止）
    prompt = f"""
以下の制約を厳守して日本語記事を Markdown で生成してください。

【絶対条件】
・翻訳案、別タイトル案、候補、注意書き、補足は一切出力しない
・本文の最初にタイトルを絶対に重複させない
・Markdown を正しく生成すること
・(情報不足のため記述できません) は使わない
・背景・見通しが不足する場合は「## 推測」を出して、影響や広い文脈を推測で説明する

【最終アウトプット構造】

# 日本語タイトル

**元記事URL**: {article["link"]}

## 概要
（ニュース内容の要点）

## 背景
（必要に応じて）

## 今後の見通し
（予測できる場合のみ）

## 推測
（背景/見通しが不十分な場合のみ。影響や文脈を説明）

---

【元記事情報】
英語タイトル: {article["title"]}

概要: {article["summary"]}

本文: {article["content"]}

以上を踏まえて、日本語記事だけを Markdown で生成する。
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    return response.text


# ======================================================
# はてなブログへ投稿（HTML）※はてな仕様完全対応
# ======================================================
def post_to_hatena(title, content_md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    # Markdown → HTML
    content_html = markdown.markdown(content_md, extensions=["extra"])

    # ⚠ はてなブログ仕様
    #   ・<content> 内はインデント禁止（行頭に空白があるとプレーンテキスト扱いになる）
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">

<title>{title}</title>

<content type="text/html">
{content_html}
</content>

</entry>
"""

    print("Posting to Hatena Blog...")

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    headers = {"Content-Type": "application/xml"}

    r = requests.post(url, data=xml.encode("utf-8"), auth=(hatena_id, api_key))

    if r.status_code not in [200, 201]:
        print("Hatena投稿失敗:", r.status_code, r.text)
        return False

    print("Hatena投稿成功")
    return True


# ======================================================
# メイン処理
# ======================================================
def main():
    print("==== Myanmar News Auto Poster ====")

    seen = load_seen()

    RSS_SOURCES = [
        ("Irrawaddy", "https://www.irrawaddy.com/feed"),
        # ("Myanmar Now", "https://myanmar-now.org/en/feed/")  # 403 回避のためオフ
    ]

    new_articles = []

    # RSS 全部読む
    for name, url in RSS_SOURCES:
        entries = fetch_rss(url, name)
        print("取得件数:", len(entries))

        for e in entries:
            link = e.get("link")
            if not link or link in seen:
                continue

            summary = BeautifulSoup(e.get("summary", ""), "html.parser").get_text()
            content = e.get("content", [{"value": summary}])[0]["value"]
            content = BeautifulSoup(content, "html.parser").get_text()

            new_articles.append({
                "id": link,
                "title": e.get("title", ""),
                "summary": summary,
                "content": content,
                "link": link
            })

    print("新規記事:", len(new_articles))

    if not new_articles:
        print("新記事なし。終了します。")
        return

    # 新規記事を順番に投稿
    for article in new_articles:
        md = generate_markdown(article)
        lines = md.split("\n")

        # Markdown の先頭行をタイトルに
        safe_title = lines[0].replace("#", "").strip()

        print("投稿タイトル:", safe_title)

        ok = post_to_hatena(safe_title, md)

        if ok:
            seen.add(article["id"])
            save_seen(seen)


# ======================================================
if __name__ == "__main__":
    main()
