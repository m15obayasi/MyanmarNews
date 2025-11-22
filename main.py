# -*- coding: utf-8 -*-
import os
import json
from datetime import datetime, timezone, timedelta
import textwrap
import requests
from xml.sax.saxutils import escape as xml_escape

# Google GenAI SDK
from google import genai


def log(msg: str) -> None:
    """シンプルなログ出力."""
    print(msg, flush=True)


def get_jst_now() -> datetime:
    """JST の現在時刻を返す."""
    return datetime.now(timezone(timedelta(hours=9)))


def build_gemini_prompt(jst_now: datetime) -> str:
    """ミャンマー情勢の解説記事を生成させるためのプロンプトを作成."""
    jst_str = jst_now.strftime("%Y-%m-%d %H:%M:%S (JST)")

    # 注意：
    # - 特定の日付・人数・金額・固有名詞などは新たに作らないように明示しています。
    # - 参照元ブロックは Gemini ではなく、こちら側で付けるので、
    #   本文には「参照元」という見出しは不要です。
    prompt = f"""
あなたはミャンマー情勢を専門とする日本語ニュースブロガーです。
以下の条件で、ブログ記事を1本だけ執筆してください。

# テーマ
- ミャンマーの最近の情勢を、日本の一般読者向けにわかりやすく解説する記事。
- 「The Irrawaddy（英語ニュースサイト）」などで継続的に報じられている話題を、
  日本語でまとめて紹介するイメージです。

# 内容の制約（重要）
- 具体的な「日付」「人数」「金額」「地名」「個人名」「組織名」などの
  **新しい固有名詞や数値情報は作らないでください。**
- すでに一般的に知られている範囲の表現にとどめてください。
  - 例: 「軍政」「民主派」「治安悪化」「インフレ」「越境貿易」など。
- 読者が誤解しそうな、細かい事実（いつ・どこで・誰が・何人）といった情報は書かないでください。
- あくまで「大まかな傾向」や「構造的な課題」「今後の注目ポイント」にフォーカスしてください。

# 記事構成
- 冒頭に短いリード文（2〜4文程度）を書いてください。
- そのあとに「### 見出し」を3〜4個付けて、セクションごとに整理してください。
- 各セクションでは、背景 → 現状の課題 → 住民・周辺国・国際社会への影響、
  といった流れで、筋の通った説明を心がけてください。
- 記事の最後に「今後のミャンマーウォッチのポイント」を、
  箇条書きか短い段落で2〜3個まとめてください。

# 文体
- です・ます調。
- 専門家ではない読者にも伝わるように、難しい用語には必ず一言で説明を添えてください。
- 不必要に煽らず、しかし「遠い国の話ではない」という距離感を大事にしてください。

# メタ情報
- 執筆日時（日本時間）: {jst_str}

# 出力フォーマット
- Markdown で出力してください。
- 一番最初の行は「# タイトル」の形式で、記事全体のタイトルを書いてください。
- それ以外に余計な説明やコメントは入れず、ブログ本文だけを出力してください。
"""
    # インデントを綺麗に落とす
    return textwrap.dedent(prompt).strip()


def generate_article_with_gemini() -> str:
    """Gemini を使って記事本文（Markdown）を生成."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY が環境変数に設定されていません。")

    client = genai.Client(api_key=api_key)

    jst_now = get_jst_now()
    prompt = build_gemini_prompt(jst_now)

    log("Generating article with Gemini...")
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
        config={"max_output_tokens": 2048},
    )

    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini から空のレスポンスが返されました。")

    return text


def split_title_and_body(markdown_text: str):
    """
    Markdown からタイトル行（# ...）と本文を分離する。
    タイトル行が見つからない場合はデフォルトタイトルを使う。
    """
    lines = markdown_text.splitlines()
    title = "ミャンマー情勢の最近の動き（自動投稿）"
    body_lines = lines

    for idx, line in enumerate(lines):
        if line.strip().startswith("#"):
            # 先頭の # を全部削ってタイトルに
            title = line.lstrip("#").strip()
            body_lines = lines[idx + 1 :]
            break

    body = "\n".join(body_lines).strip()
    return title, body


def build_hatena_entry_xml(title: str, body_markdown: str) -> str:
    """
    はてなブログの Atom API に送る XML を組み立てる。
    body_markdown は [markdown]〜[/markdown] でラップして送る。
    """
    # 参照元ブロックを本文の先頭に追加
    source_block = textwrap.dedent(
        """\
        参照元  
        The Irrawaddy（英語ニュースサイト）  
        https://www.irrawaddy.com/

        ---
        """
    ).strip()

    body_with_source = f"{source_block}\n\n{body_markdown}".strip()
    hatena_body = "[markdown]\n" + body_with_source + "\n[/markdown]"

    # XML エスケープ
    title_xml = xml_escape(title)
    content_xml = xml_escape(hatena_body)

    now_utc = datetime.now(timezone.utc)
    updated = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    entry_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<entry xmlns="http://www.w3.org/2005/Atom"
       xmlns:app="http://www.w3.org/2007/app">
  <title>{title_xml}</title>
  <updated>{updated}</updated>
  <author>
    <name>{xml_escape(os.environ.get("HATENA_ID", ""))}</name>
  </author>
  <content type="text/x-markdown">{content_xml}</content>
  <category term="ミャンマー情勢" />
  <app:control>
    <app:draft>no</app:draft>
  </app:control>
</entry>
"""
    return entry_xml


def post_to_hatena(title: str, body_markdown: str) -> None:
    """はてなブログに記事を投稿."""
    hatena_id = os.environ.get("HATENA_ID")
    api_key = os.environ.get("HATENA_API_KEY")
    blog_id = os.environ.get("HATENA_BLOG_ID")

    if not hatena_id or not api_key or not blog_id:
        raise RuntimeError(
            "HATENA_ID / HATENA_API_KEY / HATENA_BLOG_ID のいずれかが未設定です。"
        )

    endpoint = f"https://blog.hatena.ne.jp/{hatena_id}/{blog_id}/atom/entry"

    entry_xml = build_hatena_entry_xml(title, body_markdown)

    log("Posting entry to Hatena Blog...")
    resp = requests.post(
        endpoint,
        data=entry_xml.encode("utf-8"),
        headers={"Content-Type": "application/xml; charset=utf-8"},
        auth=(hatena_id, api_key),
        timeout=30,
    )

    log(f"Hatena response status: {resp.status_code}")
    # デバッグ用に一部だけ表示
    snippet = (resp.text or "")[:300]
    log(f"Hatena response body (head): {snippet}")

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Hatena Blog への投稿に失敗しました (status={resp.status_code})"
        )


def write_seen_file(title: str) -> None:
    """
    GitHub Actions の add-and-commit 用に、seen_articles.json を必ず作る。
    （この記事の重複チェックには使っていない）
    """
    data = {
        "last_title": title,
        "last_posted_at": get_jst_now().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": "This file is used only to make GitHub Actions commit succeed.",
    }
    with open("seen_articles.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log("seen_articles.json を作成 / 更新しました。")


def main():
    log("==== Myanmar News Auto Poster (A: RSS無し・生成+投稿のみ) ====")

    # 1. Gemini で記事を生成
    article_md = generate_article_with_gemini()
    title, body_md = split_title_and_body(article_md)

    log(f"Generated title: {title}")

    # 2. はてなブログに投稿
    post_to_hatena(title, body_md)

    # 3. seen_articles.json を書き出し（GitHub Actions のため）
    write_seen_file(title)

    log("投稿処理が完了しました。")


if __name__ == "__main__":
    main()
