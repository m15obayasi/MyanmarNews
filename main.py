import os
import json
import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
import markdown

SEEN_FILE = "seen_articles.json"


# --------------------------------------------------
# 既存記事ID管理
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
# RSS取得（Irrawaddyの文字コード問題に対応）
# --------------------------------------------------
def fetch_rss(url, name):
    print(f"Fetching RSS: {name} ...")

    try:
        # まず requests で強制UTF-8として取得
        res = requests.get(url, timeout=20)
        text = res.content.decode("utf-8", errors="ignore")
        feed = feedparser.parse(text)
    except Exception as e:
        print(f"RSS取得失敗（{name}）:", e)
        return []

    if feed.bozo:
        print(f"RSS取得失敗（{name}）:", feed.bozo_exception)
        return []

    return feed.entries


# --------------------------------------------------
# Geminiで記事生成
# --------------------------------------------------
def generate_markdown(article, need_guess):
    print("Generating article with Gemini AI...")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    # 情報不足なら「推測」セクションを使う
    if need_guess:
        background_header = "## 推測：このニュースが与える影響"
        outlook_header = ""
    else:
        background_header = "## 背景"
        outlook_header = "## 今後の見通し"

    prompt = f"""
【絶対遵守】
次の Markdown 構造 **のみ** を出力してください。
余計な翻訳案・候補案・注意書き・解説は禁止。

# 日本語タイトル

## 概要
（要約と重要ポイント）

{background_header}
（時系列・政治状況・関係勢力の説明、または推測による影響分析）

{outlook_header}
（今後予測される展開・国際的影響。推測モードでは空でよい）

元記事URL: {article["link"]}

--- 記事情報 ---
英語タイトル:
{article["title"]}

概要:
{article["summary"]}

本文（英語抜粋・サマリ）:
{article["content"]}
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    md = response.text

    # 安全策：元記事URLが無い場合は強制付与
    if "元記事URL" not in md:
        md += f"\n\n元記事URL: {article['link']}"

    return md


# --------------------------------------------------
# はてなブログ投稿（Markdown → HTML）
# --------------------------------------------------
def post_to_hatena(title, content_md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    # Markdown → HTML（改行と見出しに完全対応）
    content_html = markdown.markdown(
        content_md,
        extensions=["extra", "nl2br", "sane_lists"]
    )

    # はてなブログ AtomPub
    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app">

  <title>{title}</title>

  <content type="text/html">
<![CDATA[
{content_html}
]]>
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

    RSS_SOURCES = [
        ("Irrawaddy", "https://www.irrawaddy.com/feed"),
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
            content_raw = e.get("content", [{"value": summary}])[0]["value"]
            content = BeautifulSoup(content_raw, "html.parser").get_text()

            # 情報不足判定（超簡易ロジック）
            need_guess = len(content.split()) < 80 or len(summary.split()) < 40

            new_articles.append({
                "id": link,
                "title": e.get("title", ""),
                "summary": summary,
                "content": content,
                "link": link,
                "need_guess": need_guess
            })

    print("新規記事:", len(new_articles))

    if not new_articles:
        print("新記事なし。終了します。")
        return

    for article in new_articles:
        md = generate_markdown(article, article["need_guess"])

        # タイトル行（# ...）を本文から削除
        lines = md.split("\n")
        title_line = lines[0].replace("#", "").strip()
        body_md = "\n".join(lines[1:]).strip()

        print("投稿タイトル:", title_line)

        ok = post_to_hatena(title_line, body_md)

        if ok:
            seen.add(article["id"])
            save_seen(seen)


# --------------------------------------------------
if __name__ == "__main__":
    main()
