import os
import json
import requests
from bs4 import BeautifulSoup
from google import genai
import markdown

SEEN_FILE = "seen_articles.json"

# --------------------------------------------------
# seen_articles.json の読み書き
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
# RSSを requests + BeautifulSoup で安全に取得（Irrawaddy対応）
# --------------------------------------------------
def fetch_rss(url, name):
    print(f"Fetching RSS: {name} ...")

    try:
        res = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        res.raise_for_status()
    except Exception as e:
        print(f"RSS取得失敗（{name}）:", e)
        return []

    # Irrawaddy は XML 内に encoding が書かれておらず US-ASCII 扱い → 強制UTF-8で読み直す
    text = res.content.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(text, "xml")

    items = soup.find_all("item")
    articles = []

    for item in items:
        title = item.title.get_text() if item.title else ""
        link = item.link.get_text() if item.link else ""

        # description / content:encoded / summary のどれかがある
        description = ""
        if item.find("content:encoded"):
            description = item.find("content:encoded").get_text()
        elif item.description:
            description = item.description.get_text()
        else:
            description = ""

        clean_summary = BeautifulSoup(description, "html.parser").get_text()

        articles.append({
            "title": title,
            "link": link,
            "summary": clean_summary,
            "content": clean_summary
        })

    return articles


# --------------------------------------------------
# Gemini で記事生成（Markdown）—翻訳案禁止をさらに強化
# --------------------------------------------------
def generate_markdown(article):
    print("Generating article with Gemini AI...")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
【絶対遵守事項】
以下の Markdown フォーマット「のみ」を出力してください。
他の文章（翻訳案・候補案・注意書き・説明文・補足・別解釈）は一切書かないこと。
出力は記事本文だけ。

# 日本語タイトル

## 概要
（要約と重要ポイント）

## 背景
（時系列・政治状況・地理関係・登場勢力を整理）

## 今後の見通し
（予測される展開・国際的影響・地域リスク）

元記事URL: {article["link"]}

--- 記事情報 ---
英語タイトル:
{article["title"]}

概要:
{article["summary"]}

本文（英語の簡易抜粋）:
{article["content"]}

以上を踏まえ、日本語で記事を Markdown 形式で生成せよ。
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    return response.text


# --------------------------------------------------
# はてなブログ投稿（Markdown → HTML 変換）
# --------------------------------------------------
def post_to_hatena(title, md_text):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    # Markdown → HTML
    content_html = markdown.markdown(md_text, extensions=["extra"])

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <content type="text/html">
{content_html}
  </content>
</entry>
"""

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

    RSS_SOURCES = [
        ("Irrawaddy", "https://www.irrawaddy.com/feed")
    ]

    new_articles = []

    for name, url in RSS_SOURCES:
        entries = fetch_rss(url, name)
        print("取得件数:", len(entries))

        for e in entries:
            link = e["link"]
            if not link or link in seen:
                continue

            new_articles.append(e)

    print("新規記事:", len(new_articles))

    if not new_articles:
        print("新記事なし。終了します。")
        return

    # 新しいものから順に処理
    for article in new_articles:
        md = generate_markdown(article)

        # 先頭行をタイトルにする
        first_line = md.split("\n")[0]
        safe_title = first_line.replace("#", "").strip()

        print("投稿タイトル:", safe_title)

        ok = post_to_hatena(safe_title, md)
        if ok:
            seen.add(article["link"])
            save_seen(seen)


if __name__ == "__main__":
    main()
