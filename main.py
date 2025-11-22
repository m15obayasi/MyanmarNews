import os
import json
import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
import markdown
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote

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
# RSSを取得（encodingエラー対策込み）
# --------------------------------------------------
def fetch_rss(url, name):
    print(f"Fetching RSS: {name} ...")
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"RSS取得失敗（{name}）: {e}")
        return []

    # バイト列から feedparser に渡すことで、変な encoding 宣言を回避
    feed = feedparser.parse(resp.content)
    if feed.bozo:
        print(f"RSS解析エラー（{name}）:", feed.bozo_exception)
        return []

    return feed.entries


# --------------------------------------------------
# Geminiクライアント生成（1回だけ）
# --------------------------------------------------
def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY が設定されていません。")
    return genai.Client(api_key=api_key)


# --------------------------------------------------
# Geminiで個別記事Markdown生成
# --------------------------------------------------
def generate_markdown_for_article(client, article):
    print("Generating article with Gemini AI...")

    prompt = f"""
あなたはミャンマー情勢専門の日本語ニュース編集者です。

【絶対ルール】
- 出力は **日本語のMarkdown** のみ。
- 指示文・候補案・注意書き・解説などは一切書かない。
- 下記の構造と見出しだけを使うこと。

【出力フォーマット】

# 日本語タイトル

元記事URL: {article["link"]}

## 概要
記事全体の要点を2〜4文でまとめてください。

## 背景
- 関係する勢力（民族武装勢力、国軍、民主派など）
- 地理的な位置関係（州・地域など）
- 過去の経緯や今回に至るまでの流れ
を、事実に基づいて簡潔に説明してください。

## 今後の見通し
- 事実情報から読み取れる「今後起こりそうな展開」や
  国際社会・周辺地域への影響を、慎重な言い回しで述べてください。
- もし事実情報が乏しく、ほとんどが推測になってしまう場合は、
  このセクションは **書かず**、代わりに次のセクション見出しを使ってください。

## 推測
- 事実が不足している場合のみ、この見出しを使ってください。
- 「〜と考えられます」「〜となる可能性があります」など、
  推測であることが分かる形で、本ニュースが与え得る影響を説明してください。
- 「情報不足のため、記述できません」のような文章は書かないでください。
  あくまで一般的な知識から見た推測を書いてください。

### 要点まとめ
- 本文の中で最も重要なポイントを3〜5個、箇条書きでまとめてください。
- それぞれ1行〜2行程度で簡潔に。

### キーワード
- 記事の内容をよく表す日本語キーワードを3〜5個、箇条書きで書いてください。
- 固有名詞（組織名・地名・人物名）を優先してください。
- 箇条書きは「- キーワード」という形式で書いてください。

【元記事情報】

英語タイトル:
{article["title"]}

概要（英語）:
{article["summary"]}

本文（英語の抜粋・サマリ）:
{article["content"]}

以上を踏まえて、日本語記事を生成してください。
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    return response.text


# --------------------------------------------------
# キーワード箇条書きを内部リンクに変換
#   - 「### キーワード」配下の
#     "- カレン民族同盟" → "- [カレン民族同盟](/search?q=カレン民族同盟)"
# --------------------------------------------------
def add_internal_links_from_keywords(content_md):
    lines = content_md.split("\n")
    result = []

    in_keywords_section = False

    for i, line in enumerate(lines):
        stripped = line.lstrip()

        # 見出し検出
        if stripped.startswith("### キーワード"):
            in_keywords_section = True
            result.append(line)
            continue

        if in_keywords_section:
            # 空行や次の見出しで終わり
            if stripped == "" or stripped.startswith("#"):
                in_keywords_section = False
                result.append(line)
                continue

            # 箇条書きなら内部リンクに変換
            if stripped.startswith("- "):
                keyword = stripped[2:].strip()
                if keyword:
                    url = f"/search?q={quote(keyword)}"
                    result.append(f"- [{keyword}]({url})")
                else:
                    result.append(line)
                continue

        # 通常行
        result.append(line)

    return "\n".join(result)


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
# 日次まとめ記事の生成
#   - 1回の実行で「実際に投稿に成功した記事」だけをまとめる
# --------------------------------------------------
def generate_daily_summary_markdown(client, posted_articles):
    if not posted_articles:
        return None

    # 日付は日本時間で
    jst = ZoneInfo("Asia/Tokyo")
    today = datetime.now(jst).strftime("%Y年%m月%d日")

    # ニュース一覧をプロンプト用に整形
    article_blocks = []
    for i, art in enumerate(posted_articles, start=1):
        block = f"""
【ニュース {i}】
英語タイトル: {art["title"]}
概要（英語）: {art["summary"]}
本文（英語サマリ）: {art["content"]}
元記事URL: {art["link"]}
"""
        article_blocks.append(block)

    articles_text = "\n".join(article_blocks)

    prompt = f"""
あなたはミャンマー情勢専門の日本語ニュース編集者です。
以下の複数ニュースをもとに、「日次まとめ記事」を日本語Markdownで作成してください。

【絶対ルール】
- 出力は日本語のMarkdownのみ。
- 指示文や注意書きは書かない。
- 下記の構造と見出しだけを使うこと。

【出力フォーマット】

# {today} ミャンマー情勢まとめ

## 本日の主なニュース
- 各ニュースを箇条書きで1〜3行ずつ日本語で要約してください。

## 全体像と分析
- 複数のニュースをまたいだ流れや共通点、
  今後の軍事バランス・民主化プロセス・人道状況などへの影響を論じてください。
- 「〜と考えられます」「〜の可能性があります」など慎重な言い回しを使ってください。

## 注目ポイント
- 投資・国際関係・人道支援・周辺国への波及など、
  読者が特に押さえておくべきポイントを3〜5個、箇条書きで示してください。

### キーワード
- 本日のニュース群を象徴する日本語キーワードを3〜7個、箇条書きで書いてください。
- 固有名詞を優先してください。

--- 元記事一覧（英語） ---
- ここでは、箇条書きで「日本語タイトル風の簡易タイトル（元記事URL）」を列挙してください。

【ニュース一覧】
{articles_text}

以上を踏まえて、日本語で日次まとめ記事を生成してください。
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )

    return response.text


# --------------------------------------------------
# メイン処理
# --------------------------------------------------
def main():
    print("==== Myanmar News Auto Poster ====")

    seen = load_seen()
    client = get_gemini_client()

    RSS_SOURCES = [
        ("Irrawaddy", "https://www.irrawaddy.com/feed"),
        # 将来ほかのソースを足す場合はここに追記
    ]

    new_articles = []

    # RSS取得
    for name, url in RSS_SOURCES:
        entries = fetch_rss(url, name)
        print("取得件数:", len(entries))

        for e in entries:
            link = e.get("link")
            if not link or link in seen:
                continue

            summary_html = e.get("summary", "")
            summary = BeautifulSoup(summary_html, "html.parser").get_text()

            content_val = e.get("content", [{"value": summary_html}])[0]["value"]
            content_text = BeautifulSoup(content_val, "html.parser").get_text()

            new_articles.append({
                "id": link,
                "title": e.get("title", ""),
                "summary": summary,
                "content": content_text,
                "link": link
            })

    print("新規記事:", len(new_articles))

    if not new_articles:
        print("新記事なし。終了します。")
        return

    posted_articles_for_summary = []

    # 記事ごとに生成＆投稿
    for article in new_articles:
        md = generate_markdown_for_article(client, article)

        # キーワードから内部リンクを生成
        md = add_internal_links_from_keywords(md)

        # Markdown の最初の行をタイトルにする
        lines = md.split("\n")
        if not lines:
            print("生成結果が空でした。スキップします。")
            continue

        safe_title = lines[0].replace("#", "").strip()
        print("投稿タイトル:", safe_title)

        ok = post_to_hatena(safe_title, md)

        if ok:
            seen.add(article["id"])
            save_seen(seen)
            posted_articles_for_summary.append(article)

    # 日次まとめ記事の生成＆投稿
    if posted_articles_for_summary:
        print("日次まとめ記事を生成します...")
        summary_md = generate_daily_summary_markdown(client, posted_articles_for_summary)

        if summary_md:
            # 内部リンク化（キーワードセクションがあれば）
            summary_md = add_internal_links_from_keywords(summary_md)

            lines = summary_md.split("\n")
            if lines:
                summary_title = lines[0].replace("#", "").strip()
                print("投稿タイトル（日次まとめ）:", summary_title)
                post_to_hatena(summary_title, summary_md)


# --------------------------------------------------
if __name__ == "__main__":
    main()
