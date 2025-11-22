# main.py
# モードA：RSSを一切使わず、
# Geminiでミャンマー情勢の記事を生成して
# はてなブログに投稿するだけのシンプル版

import os
import sys
from datetime import datetime, timezone, timedelta

import requests
from markdown import markdown
from google import genai


# ====== 設定・環境変数 ======

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
HATENA_ID = os.environ.get("HATENA_ID")
HATENA_API_KEY = os.environ.get("HATENA_API_KEY")
HATENA_BLOG_ID = os.environ.get("HATENA_BLOG_ID")

if not GEMINI_API_KEY:
    print("ERROR: GEMINI_API_KEY が設定されていません。", file=sys.stderr)
    sys.exit(1)

if not (HATENA_ID and HATENA_API_KEY and HATENA_BLOG_ID):
    print("ERROR: HATENA_ID / HATENA_API_KEY / HATENA_BLOG_ID が不足しています。", file=sys.stderr)
    sys.exit(1)

# google-genai 公式ドキュメント準拠の初期化
# https://ai.google.dev/api/generate-content
client = genai.Client(api_key=GEMINI_API_KEY)

JST = timezone(timedelta(hours=9))


# ====== ユーティリティ ======

def escape_xml(text: str) -> str:
    """Atom XML 内で使うための最低限のエスケープ"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# ====== Geminiで記事生成 ======

def generate_article_with_gemini():
    """Gemini にミャンマー情勢記事を書かせる（Markdown本文）"""

    today = datetime.now(JST)
    date_str = today.strftime("%Y年%m月%d日")
    title = f"ミャンマー情勢まとめ（{date_str}）"

    # 本文だけを書かせる。タイトルはPython側で固定生成。
    prompt = f"""
あなたはミャンマー情勢に詳しい日本語のニュースライターです。
{date_str}時点のミャンマーに関する国際ニュースや日本語で報じられている内容を前提知識として、
以下の条件で **記事本文（Markdown）だけ** を書いてください。

# 出力フォーマット
- 見出しや箇条書きを含んだ Markdown テキスト
- 先頭にタイトル行（# や見出し）は書かないでください
- こちらでタイトルは付けるので、「タイトル：〜」も書かないでください

# 想定読者
- 日本のビジネスパーソン・投資家・在日ミャンマー人
- ミャンマー情勢に関心はあるが、専門家ではない人

# 文体
- です・ます調
- 感情的になりすぎず、冷静で丁寧なトーン

# セクション構成（必須）
以下の見出し構成で書いてください。

1. 導入（見出しは「## 導入」）
   - 3〜4文で、今日時点の全体像と重要ポイントを要約してください。
2. 政治・軍事の動き（見出しは「## 政治・軍事の動き」）
3. 経済・ビジネスの動き（見出しは「## 経済・ビジネスの動き」）
4. 市民生活・人道状況（見出しは「## 市民生活・人道状況」）
5. 国際社会の反応（見出しは「## 国際社会の反応」）
6. 今後の見通し（見出しは「## 今後の見通し」）

# 表現ルール
- 陰謀論や裏取りできない情報には触れず、公的機関や大手メディアなどで扱われている情報のみを前提としてください。
- 不確実な情報は、
  - 「〜とみられています」
  - 「〜との見方もあります」
  など、慎重な言い回しを使ってください。
- 読者が不必要に不安にならないよう、
  - 現状
  - 課題
  - それに対して取られている対応
  をバランスよく説明してください。

# 固有名詞
- 可能な範囲で、国名・都市名・組織名・政党名などの固有名詞を具体的に書いてください。
- ただし、個人名については、公人やニュースで繰り返し取り上げられている人物に限定してください。

# 参照元の書き方（重要）
本文の末尾に、必ず次の形式でセクションを追加してください。

## 参照元

- 国際ニュース各紙（英語）
- 日本語ニュース各紙
- オープンソース情報（現地ジャーナリスト・市民団体の公開レポート など）

※具体的なURLは書かないでください。
※個々の記事のタイトルも書かなくて構いません。情報源の種類だけを列挙してください。

以上の条件に従って、Markdown本文のみを出力してください。
"""

    print("Generating article with Gemini...")

    response = client.models.generate_content(
        model="gemini-2.0-flash",  # 最新の推奨モデル
        contents=prompt,
    )

    article_md = (response.text or "").strip()

    if not article_md:
        print("ERROR: Gemini から本文が返ってきませんでした。", file=sys.stderr)
        sys.exit(1)

    return title, article_md


# ====== はてなブログ投稿 ======

def post_to_hatena(title: str, content_md: str) -> bool:
    """Markdown本文をHTMLに変換して、はてなブログに AtomPub で投稿"""

    # Markdown -> HTML
    content_html = markdown(content_md, extensions=["extra"])

    updated_utc = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    endpoint = (
        f"https://blog.hatena.ne.jp/{HATENA_ID}/{HATENA_BLOG_ID}/atom/entry"
    )

    xml_body = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <title>{escape_xml(title)}</title>
  <author><name>{escape_xml(HATENA_ID)}</name></author>
  <updated>{updated_utc}</updated>
  <category term="ミャンマー" />
  <category term="ニュース" />
  <content type="text/html">
    <![CDATA[
{content_html}
    ]]>
  </content>
</entry>
"""

    headers = {
        "Content-Type": "application/xml; charset=utf-8",
    }

    print("Posting to Hatena Blog...")
    resp = requests.post(
        endpoint,
        data=xml_body.encode("utf-8"),
        headers=headers,
        auth=(HATENA_ID, HATENA_API_KEY),
        timeout=30,
    )

    if 200 <= resp.status_code < 300:
        print(f"Hatena Blog 投稿成功 (status={resp.status_code})")
        return True
    else:
        print(
            f"Hatena Blog 投稿失敗 (status={resp.status_code})",
            file=sys.stderr,
        )
        try:
            print(resp.text, file=sys.stderr)
        except Exception:
            pass
        return False


# ====== メイン ======

def main():
    print("==== Myanmar News Auto Poster (A: RSS無し・生成+投稿のみ) ====")

    title, article_md = generate_article_with_gemini()
    print(f"Generated title: {title}")

    ok = post_to_hatena(title, article_md)

    if not ok:
        sys.exit(1)

    print("Done.")


if __name__ == "__main__":
    main()
