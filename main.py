import os
import json
import requests
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
# Irrawaddy RSS を “強制修復” して取得
# --------------------------------------------------
def fetch_irrawaddy_rss():
    print("Fetching RSS: Irrawaddy ...")

    url = "https://www.irrawaddy.com/feed"

    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        raw = r.content.decode("utf-8", errors="replace")  # 壊れたエンコードを強制修復
    except Exception as e:
        print("Irrawaddy RSS取得失敗:", e)
        return []

    # XML を BeautifulSoup で解析
    soup = BeautifulSoup(raw, "xml")

    items = soup.find_all("item")
    print("RSS items:", len(items))

    articles = []
    for item in items:
        title = item.title.get_text() if item.title else ""
        link = item.link.get_text() if item.link else ""
        description = item.description.get_text() if item.description else ""
        pub = item.pubDate.get_text() if item.pubDate else ""

        if not link:
            continue

        articles.append({
            "title": title,
            "summary": BeautifulSoup(description, "html.parser").get_text(),
            "content": BeautifulSoup(description, "html.parser").get_text(),
            "link": link,
            "published": pub
        })

    return articles

# --------------------------------------------------
# Geminiで記事生成
# --------------------------------------------------
def generate_markdown(article):
    print("Generating article with Gemini AI...")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    prompt = f"""
以下の形式の Markdown のみを出力してください。
余計な説明・候補案・翻訳案・注意文は一切書かないこと。

# 日本語タイトル

元記事URL: {article["link"]}

## 概要
（ニュース内容の要点を 5〜10 行で）

## 背景
（理解に必要な文脈がある場合のみ書く）

## 今後の見通し
（推測含む将来への影響を書く）

以下が英語記事です：

英語タイトル:
{article["title"]}

概要:
{article["summary"]}

本文:
{article["content"]}
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    return response.text

# --------------------------------------------------
# はてなブログへ投稿（Markdown→HTML）
# --------------------------------------------------
def post_to_hatena(title, content_md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    content_html = markdown.markdown(content_md, extensions=["extra"])

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <content type="text/html">
{content_html}
  </content>
</entry>
"""

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"
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

    entries = fetch_irrawaddy_rss()
    print("取得件数:", len(entries))

    new_articles = [e for e in entries if e["link"] not in seen]
    print("新規記事:", len(new_articles))

    if not new_articles:
        print("新記事なし。終了します。")
        return

    for article in new_articles:
        md = generate_markdown(article)

        # 一行目（# タイトル）の # を除いてタイトル化
        first_line = md.split("\n")[0]
        title = first_line.replace("#", "").strip()

        print("投稿タイトル:", title)

        ok = post_to_hatena(title, md)

        if ok:
            seen.add(article["link"])
            save_seen(seen)

# --------------------------------------------------
if __name__ == "__main__":
    main()
