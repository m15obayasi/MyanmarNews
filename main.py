import os
import json
import time
import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
import markdown
import re

SEEN_FILE = "seen_articles.json"


# --------------------------------------------------
# 429 / APIエラーに強い Gemini 呼び出しラッパー
# --------------------------------------------------
def call_gemini_with_retry(client, model, prompt, max_retries=5):
    for attempt in range(1, max_retries + 1):
        try:
            res = client.models.generate_content(
                model=model,
                contents=prompt
            )
            return res.text

        except Exception as e:
            msg = str(e)

            # 429 の retry-after を拾う
            retry_time = 30
            if "RetryInfo" in msg or "retryDelay" in msg:
                # 例: retryDelay': '48s'
                m = re.search(r"retryDelay.*?(\d+)", msg)
                if m:
                    retry_time = int(m.group(1))

            print(f"[Gemini] エラー発生 ({attempt}/{max_retries}) → {retry_time} 秒スリープ")
            time.sleep(retry_time)

    raise RuntimeError("Gemini API 再試行上限に達しました。")


# --------------------------------------------------
# seen_articles.json の読み込み/保存
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
# RSS 取得
# --------------------------------------------------
def fetch_rss(url, name):
    print(f"Fetching RSS: {name} ...")
    feed = feedparser.parse(url)
    if feed.bozo:
        print(f"RSS取得失敗（{name}）:", feed.bozo_exception)
        return []
    return feed.entries


# --------------------------------------------------
# タイトル翻訳（429耐久版）
# --------------------------------------------------
def translate_title(client, title_en):
    prompt = f"""
以下の英語ニュースタイトルを自然な日本語タイトルに1つだけ翻訳してください。
- 箇条書き禁止
- 複数案提示禁止
- 補足説明禁止
- 余計な文を絶対に書かない

英語タイトル:
{title_en}

出力は1行の日本語タイトルのみ。
"""

    print("Translating title...")
    text = call_gemini_with_retry(client, "gemini-2.0-flash", prompt)

    # 複数案が返ってきた場合などを防御
    text = text.strip()
    text = text.split("\n")[0]  # 1行のみ
    text = re.sub(r"^\W+", "", text)  # 記号削除

    print("日本語タイトル:", text)
    return text


# --------------------------------------------------
# 本文生成（429耐久版）
# --------------------------------------------------
def generate_markdown(client, article):
    print("Generating article with Gemini...")

    prompt = f"""
# 指示
以下のニュース記事データを元に、次の構造の日本語記事を Markdown で作成してください。

# 出力フォーマット（順番厳守）

元記事URL: {article["link"]}

## 概要
（要約）

## 背景
（背景説明。無い場合は空でよい）

## 今後の見通し
（予測不可の場合はこの見出しを省略し、代わりに下記を出す）

## 推測
（もし背景情報が不足する場合、このニュースがもたらす可能性のある影響を考察）

# 禁止
- タイトルを出力しない
- 複数案、補足、注意書き、翻訳案などは一切禁止
- 指示文をそのまま書き写すのは禁止

--- 元記事情報 ---
英語タイトル:
{article["title"]}

概要:
{article["summary"]}

本文（英語の抜粋・サマリ）:
{article["content"]}
"""

    text = call_gemini_with_retry(client, "gemini-2.0-flash", prompt)

    # タイトル行（# ...）を誤出力した場合は削除
    lines = text.split("\n")
    if lines[0].startswith("#"):
        lines = lines[1:]

    text = "\n".join(lines).lstrip()

    # 余計な空行の整理
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text


# --------------------------------------------------
# はてなブログ投稿（Markdown → HTML）
# --------------------------------------------------
def post_to_hatena(title, content_md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

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
    print("==== Myanmar News Auto Poster (耐久版) ====")

    seen = load_seen()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    RSS_SOURCES = [
        ("Irrawaddy", "https://www.irrawaddy.com/feed"),
    ]

    new_articles = []

    # RSS取得
    for name, url in RSS_SOURCES:
        entries = fetch_rss(url, name)
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

    # すべての記事を429に耐えながら投稿
    for article in new_articles:
        # タイトル翻訳
        jp_title = translate_title(client, article["title"])

        # 本文生成
        md = generate_markdown(client, article)

        # 投稿
        ok = post_to_hatena(jp_title, md)

        if ok:
            seen.add(article["id"])
            save_seen(seen)


# --------------------------------------------------
if __name__ == "__main__":
    main()
