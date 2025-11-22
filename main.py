import os
import json
import time
import requests
from bs4 import BeautifulSoup
from lxml import etree
from google import genai
import markdown
import re

# --------------------------------------------------
# 定数
# --------------------------------------------------

SEEN_FILE = "seen_articles.json"

RSS_SOURCES = [
    ("Irrawaddy", "https://www.irrawaddy.com/feed"),
    # ("Myanmar Now", "https://myanmar-now.org/en/feed/"), 403 → 対策後に追加
]

MODEL = "gemini-2.0-flash"


# --------------------------------------------------
# seen 記録
# --------------------------------------------------

def load_seen():
    if os.path.exists(SEEN_FILE):
        return set(json.loads(open(SEEN_FILE, "r", encoding="utf-8").read()))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)


# --------------------------------------------------
# RSS 取得（feedparser 完全排除・安定版）
# --------------------------------------------------

def fetch_rss_xml(url, source_name):
    print(f"Fetching RSS: {source_name}")

    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(url, timeout=20, headers=headers)
    except:
        print(f"接続エラー → {source_name}")
        return []

    if r.status_code != 200:
        print(f"RSS取得失敗({source_name}): http {r.status_code}")
        return []

    # 文字コード問題があるため強制 UTF-8 デコード
    xml_text = r.content.decode("utf-8", errors="replace")

    try:
        root = etree.fromstring(xml_text.encode("utf-8"))
    except Exception as e:
        print(f"XMLパース失敗({source_name}):", e)
        return []

    items = root.xpath("//item")

    articles = []

    for item in items:
        get = lambda xp: item.xpath(xp)[0].text if item.xpath(xp) else ""

        link = get("link")
        if not link:
            continue

        title = get("title")
        description = get("description")

        articles.append({
            "source": source_name,
            "title": title.strip(),
            "summary": BeautifulSoup(description, "html.parser").get_text().strip(),
            "content": BeautifulSoup(description, "html.parser").get_text().strip(),
            "link": link.strip(),
        })

    print(f"取得件数({source_name}): {len(articles)}")
    return articles


# --------------------------------------------------
# Gemini：日本語タイトル + 記事本文生成
# --------------------------------------------------

def generate_article(article):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    # 英語タイトルを自然な日本語タイトルに変換する
    title_prompt = f"""
次の英語ニュースタイトルを、日本語の自然で硬派な「報道記事タイトル」に変換してください。
・過剰翻訳しない
・日本語ニュースの語順に整える
・15〜40文字で
・余計な文は書かない

英語タイトル：
{article["title"]}
"""
    jp_title = client.models.generate_content(
        model=MODEL,
        contents=title_prompt
    ).text.strip()

    # ---- 本文生成 ----
    body_prompt = f"""
あなたは日本語の報道記事編集者です。
以下のニュースをもとに、日本語で Markdown 記事を生成してください。

必ず次の形式のみで出力：

元記事URL: {article["link"]}

## 概要
（ニュースのポイントを4〜6文で）

## 背景
（政治状況・地理・関係勢力の説明。情報が少なければ短くて良い）

## 今後の見通し
（予測が難しい場合は書かず、代わりに下記を出力）

## 推測
（このニュースの波及効果、国際的影響を論理的に推測）

以下は元情報：

英語タイトル:
{article["title"]}

概要:
{article["summary"]}

本文(抜粋):
{article["content"]}

※ 注意：指示文・翻訳案・候補案は絶対に書かない
"""

    body = client.models.generate_content(
        model=MODEL,
        contents=body_prompt
    ).text

    # ---- Markdown の整形 ----
    body = clean_markdown(body)

    return jp_title, body


# --------------------------------------------------
# Markdown 整形（改行・見出し修復）
# --------------------------------------------------

def clean_markdown(md):
    # 連続空行を1つに
    md = re.sub(r"\n{3,}", "\n\n", md)

    # 見出し前に強制改行
    md = re.sub(r"\n*##", "\n\n##", md)

    # 余計な空白除去
    md = md.strip()

    return md


# --------------------------------------------------
# はてなブログ投稿
# --------------------------------------------------

def post_hatena(title, md):
    hatena_id = os.environ["HATENA_ID"]
    api_key = os.environ["HATENA_API_KEY"]
    blog_id = os.environ["HATENA_BLOG_ID"]

    html = markdown.markdown(md, extensions=["extra"])

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{title}</title>
  <content type="text/html">
{html}
  </content>
</entry>"""

    url = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    r = requests.post(
        url,
        data=xml.encode("utf-8"),
        auth=(hatena_id, api_key),
        headers={"Content-Type": "application/xml"}
    )

    if r.status_code in [200, 201]:
        print("はてな投稿：成功")
        return True
    else:
        print("はてな投稿：失敗", r.status_code, r.text)
        return False


# --------------------------------------------------
# メイン処理
# --------------------------------------------------

def main():
    print("==== Myanmar News Auto Poster ====")

    seen = load_seen()
    new_seen = set(seen)

    all_articles = []
    for name, url in RSS_SOURCES:
        all_articles.extend(fetch_rss_xml(url, name))

    # 新規抽出
    fresh = [a for a in all_articles if a["link"] not in seen]

    print("新規記事数:", len(fresh))

    if not fresh:
        print("新しい記事なし → 終了")
        return

    # 1件ずつ記事生成 → 投稿
    for a in fresh:
        print("\n=== 記事生成 ===")
        jp_title, md_body = generate_article(a)

        print("投稿タイトル:", jp_title)

        ok = post_hatena(jp_title, md_body)
        if ok:
            new_seen.add(a["link"])
            save_seen(new_seen)
            time.sleep(4)  # API 過負荷回避


if __name__ == "__main__":
    main()
