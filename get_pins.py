"""
Slack ピン留めメッセージ一覧取得スクリプト（HPHero-taskタスク専用）
+ #nicehero / #knowledge の直近1週間のスレッド取得

事前準備:
1. https://api.slack.com/apps で「Create New App」→「From scratch」
2. App名とワークスペースを選択して作成
3. 「OAuth & Permissions」で以下のUser Token Scopesを追加:
   - pins:read           — ピン留めメッセージの読み取り
   - channels:read       — パブリックチャンネル一覧の取得
   - channels:history    — チャンネルメッセージの読み取り
   - groups:read         — プライベートチャンネル一覧の取得（必要に応じて）
   - users:read          — ユーザー名の解決（必要に応じて）
4. 「Install to Workspace」でインストールし、User OAuth Token (xoxp-...) を取得

使用方法:
  export SLACK_USER_TOKEN="xoxp-..."
  python get_pins.py                              # コンソール出力
  python get_pins.py --output pinned_messages.md   # Markdown出力
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()

HPHERO_TASK_BOT_ID = "B06G6150AV6"
EXCLUDED_STATUSES = {"完了", "クローズ"}
THREAD_CHANNELS = ["nicehero", "knowledge"]
THREAD_CHANNEL_EMOJI = {
    "nicehero": "hero-blue",
    "knowledge": "教えて先生",
}
THREAD_DAYS = 7
STATUS_ORDER = {"対応中": 0, "保留": 1, "未着手": 2}


def get_client() -> WebClient:
    token = os.environ.get("SLACK_USER_TOKEN")
    if not token:
        print("エラー: 環境変数 SLACK_USER_TOKEN を設定してください。", file=sys.stderr)
        sys.exit(1)
    return WebClient(token=token)


def fetch_channels(client: WebClient) -> list[dict]:
    """参加中の全チャンネルを取得する（ページネーション対応）。"""
    channels = []
    cursor = None
    while True:
        resp = client.conversations_list(
            types="public_channel,private_channel",
            exclude_archived=True,
            limit=200,
            cursor=cursor,
        )
        channels.extend(resp["channels"])
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(0.5)
    return [ch for ch in channels if ch.get("is_member")]


def fetch_pins(client: WebClient, channel_id: str) -> list[dict]:
    """チャンネルのピン留めメッセージを取得する。"""
    try:
        resp = client.pins_list(channel=channel_id)
        return resp.get("items", [])
    except SlackApiError as e:
        if e.response["error"] == "not_in_channel":
            return []
        raise


def parse_task_info(pin: dict) -> dict | None:
    """HPHero-taskのピンからタスク情報を抽出する。"""
    msg = pin.get("message", {})

    if msg.get("bot_id") != HPHERO_TASK_BOT_ID:
        return None

    text = msg.get("text", "")
    match = re.search(r"【(.+?)】", text)
    task_name = match.group(1) if match else text

    status = ""
    description = ""
    assignee = ""
    due_date = ""

    for attachment in msg.get("attachments", []):
        for block in attachment.get("blocks", []):
            if block.get("block_id") == "desc":
                raw = block.get("text", {}).get("text", "")
                description = re.sub(r"^\*説明:\*\s*", "", raw)

            for field in block.get("fields", []):
                field_text = field.get("text", "")
                if "*ステータス:*" in field_text:
                    status = field_text.replace("*ステータス:*", "").strip()
                elif "*期日:*" in field_text:
                    due_date = field_text.replace("*期日:*", "").strip()

            if block.get("block_id") == "assignee":
                raw = block.get("text", {}).get("text", "")
                assignee = re.sub(r"^\*担当者:\*\s*", "", raw)

    if status in EXCLUDED_STATUSES:
        return None

    return {
        "task_name": task_name,
        "status": status or "不明",
        "description": description,
        "assignee": assignee,
        "due_date": due_date,
        "ts": msg.get("ts", ""),
        "permalink": msg.get("permalink", ""),
    }


def fetch_recent_threads(
    client: WebClient, channel_id: str, channel_name: str, days: int
) -> list[dict]:
    """直近N日間のスレッド（親メッセージ+返信）を取得する。"""
    oldest = str((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    threads = []

    # チャンネルの直近メッセージを取得
    cursor = None
    messages = []
    while True:
        resp = client.conversations_history(
            channel=channel_id,
            oldest=oldest,
            limit=200,
            cursor=cursor,
        )
        messages.extend(resp.get("messages", []))
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(1)

    print(
        f"    #{channel_name}: {len(messages)} 件のメッセージを取得",
        file=sys.stderr,
    )

    # スレッドの親メッセージ（reply_count > 0 または thread_ts == ts）の返信を取得
    for msg in messages:
        thread_ts = msg.get("thread_ts")
        ts = msg.get("ts")

        # 親メッセージのみ対象（返信メッセージはスキップ）
        if thread_ts and thread_ts != ts:
            continue

        reply_count = msg.get("reply_count", 0)
        parent_text = msg.get("text", "")
        parent_user = msg.get("user", "")
        parent_ts = ts

        replies = []
        if reply_count > 0:
            try:
                resp = client.conversations_replies(
                    channel=channel_id, ts=parent_ts, limit=200
                )
                # 最初のメッセージは親なのでスキップ
                replies = resp.get("messages", [])[1:]
            except SlackApiError:
                pass
            time.sleep(1.5)

        threads.append(
            {
                "parent_text": parent_text,
                "parent_user": parent_user,
                "parent_ts": parent_ts,
                "replies": replies,
                "raw_parent": msg,
                "is_forwarded": False,
                "original_thread": None,
                "stamp_users": [],
            }
        )

    # 古い順にソート
    threads.sort(key=lambda t: float(t["parent_ts"]))
    return threads


def summarize_thread(parent_text: str) -> str:
    """親メッセージの先頭80文字を取得し、要約として返す。"""
    text = parent_text.replace("\n", " ").strip()
    if len(text) <= 80:
        return text
    return text[:80] + "..."


def parse_slack_permalink(text: str) -> tuple[str, str] | None:
    """SlackのパーマリンクからチャンネルIDとメッセージtsを抽出する。"""
    match = re.search(
        r"https://[^/\s>]+/archives/(C[A-Z0-9]+)/p(\d{10})(\d{6})", text
    )
    if not match:
        return None
    channel_id = match.group(1)
    ts = match.group(2) + "." + match.group(3)
    return channel_id, ts


def detect_forwarded_source(msg: dict, current_channel_id: str) -> tuple[str, str] | None:
    """メッセージが他チャンネルからの転送かを判定し、元のチャンネルIDとtsを返す。"""
    # テキスト内のパーマリンク
    text = msg.get("text", "")
    result = parse_slack_permalink(text)
    if result and result[0] != current_channel_id:
        return result

    # attachments内のURLをチェック
    for att in msg.get("attachments", []):
        for key in ("from_url", "title_link", "fallback"):
            url = att.get(key, "")
            if url:
                result = parse_slack_permalink(url)
                if result and result[0] != current_channel_id:
                    return result
        ch_id = att.get("channel_id")
        att_ts = att.get("ts")
        if ch_id and att_ts and ch_id != current_channel_id:
            return ch_id, att_ts

    return None


def build_original_thread_summary(
    original: dict, replace_mentions_fn
) -> str:
    """元スレッドの内容から要約を構築する。"""
    parent = original["parent"]
    replies = original["replies"]
    ch_name = original.get("channel_name", "")

    parts = []

    parent_text = replace_mentions_fn(parent.get("text", ""))
    if len(parent_text) > 200:
        parent_text = parent_text[:200] + "..."
    parts.append(parent_text)

    if replies:
        parts.append(f"--- 返信{len(replies)}件 ---")
        for r in replies[:5]:
            r_text = replace_mentions_fn(r.get("text", ""))
            r_user = replace_mentions_fn(f"<@{r.get('user', '')}>")
            if len(r_text) > 100:
                r_text = r_text[:100] + "..."
            parts.append(f"{r_user}: {r_text}")
        if len(replies) > 5:
            parts.append(f"...他{len(replies) - 5}件")

    return "\n".join(parts)


def format_stamp_users(stamp_users: list[tuple[str, str]], replace_mentions_fn) -> str:
    """スタンプを押したユーザーをフォーマットする。"""
    names = []
    for _emoji, uid in stamp_users:
        name = replace_mentions_fn(f"<@{uid}>")
        if name not in names:
            names.append(name)
    return ", ".join(names)


def format_timestamp(ts: str) -> str:
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M (UTC)")


def collect_user_ids(*texts: str) -> set[str]:
    """テキスト群からユーザーIDを抽出する。"""
    combined = " ".join(texts)
    return set(re.findall(r"<@(U[A-Z0-9]+)>", combined))


def resolve_users(client: WebClient, user_ids: set[str]) -> dict[str, str]:
    """ユーザーIDを表示名に解決する。"""
    mapping = {}
    for uid in user_ids:
        try:
            resp = client.users_info(user=uid)
            user = resp["user"]
            mapping[uid] = (
                user.get("real_name")
                or user.get("profile", {}).get("display_name")
                or user.get("name", uid)
            )
        except SlackApiError:
            mapping[uid] = uid
        time.sleep(0.3)
    return mapping


def markdown_to_notion_blocks(text: str) -> list[dict]:
    """Markdown文字列をNotionブロックのリストに変換する。"""
    blocks = []
    for line in text.split("\n"):
        # 空行はスキップ
        if not line.strip():
            continue

        # 水平線
        if line.strip() == "---":
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            continue

        # 見出し
        if line.startswith("### "):
            blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": [{"type": "text", "text": {"content": line[4:]}}]},
            })
            continue
        if line.startswith("## "):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]},
            })
            continue
        if line.startswith("# "):
            blocks.append({
                "object": "block",
                "type": "heading_1",
                "heading_1": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]},
            })
            continue

        # 箇条書き（- で始まる行）
        if line.startswith("- "):
            content = line[2:]
            rich_text = _parse_inline_markdown(content)
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": rich_text},
            })
            continue

        # 引用（> で始まる行、インデント付き含む）
        stripped = line.lstrip()
        if stripped.startswith("> ") or stripped == ">":
            content = stripped[2:] if stripped.startswith("> ") else ""
            blocks.append({
                "object": "block",
                "type": "quote",
                "quote": {"rich_text": [{"type": "text", "text": {"content": content}}]},
            })
            continue

        # それ以外は段落
        rich_text = _parse_inline_markdown(line)
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": rich_text},
        })

    return blocks


def _parse_inline_markdown(text: str) -> list[dict]:
    """インラインのMarkdown（**太字**）をNotionのrich_textに変換する。"""
    parts = []
    pattern = re.compile(r"\*\*(.+?)\*\*")
    last_end = 0
    for m in pattern.finditer(text):
        if m.start() > last_end:
            parts.append({
                "type": "text",
                "text": {"content": text[last_end:m.start()]},
            })
        parts.append({
            "type": "text",
            "text": {"content": m.group(1)},
            "annotations": {"bold": True},
        })
        last_end = m.end()
    if last_end < len(text):
        parts.append({
            "type": "text",
            "text": {"content": text[last_end:]},
        })
    return parts if parts else [{"type": "text", "text": {"content": text}}]


def _notion_api(token: str, method: str, path: str, body: dict | None = None) -> dict:
    """Notion API を直接呼び出す。"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    url = f"https://api.notion.com/v1/{path}"
    if method == "POST":
        resp = httpx.post(url, headers=headers, json=body, timeout=30)
    elif method == "PATCH":
        resp = httpx.patch(url, headers=headers, json=body, timeout=30)
    else:
        resp = httpx.get(url, headers=headers, timeout=30)
    if resp.status_code >= 400:
        print(f"  Notion API エラー: {resp.status_code} {path}", file=sys.stderr)
        print(f"  レスポンス: {resp.text[:500]}", file=sys.stderr)
    resp.raise_for_status()
    return resp.json()


def _make_toggle_heading(text: str) -> dict:
    """トグル見出し（heading_2, is_toggleable=True）ブロックを生成する。"""
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": text}}],
            "is_toggleable": True,
        },
    }


def _append_toggle_heading(token: str, parent_id: str, text: str) -> str:
    """トグル見出しを追加し、そのブロックIDを返す。"""
    result = _notion_api(token, "PATCH", f"blocks/{parent_id}/children", {
        "children": [_make_toggle_heading(text)],
    })
    return result["results"][0]["id"]


def _find_previous_page_id(token: str, parent_page_id: str, current_page_id: str) -> str | None:
    """親ページの子ページ一覧から、現在ページの一つ前のページIDを返す。"""
    children = _notion_api(token, "GET", f"blocks/{parent_page_id}/children?page_size=100")
    child_pages = [
        b for b in children.get("results", [])
        if b["type"] == "child_page" and not b.get("in_trash")
    ]
    # created_time降順でソート
    child_pages.sort(key=lambda b: b["created_time"], reverse=True)
    current_clean = current_page_id.replace("-", "")
    found_current = False
    for page in child_pages:
        page_clean = page["id"].replace("-", "")
        if page_clean == current_clean:
            found_current = True
            continue
        if found_current:
            return page["id"]
    return None


def _copy_progress_from_previous(token: str, parent_page_id: str, current_page_id: str, toggle_id: str) -> bool:
    """過去ページから「プロジェクト進捗」を探してコピーする。最大5ページ遡る。"""
    children = _notion_api(token, "GET", f"blocks/{parent_page_id}/children?page_size=100")
    child_pages = [
        b for b in children.get("results", [])
        if b["type"] == "child_page" and not b.get("in_trash")
    ]
    child_pages.sort(key=lambda b: b["created_time"], reverse=True)
    current_clean = current_page_id.replace("-", "")

    # 現在ページより前のページを最大5件チェック
    found_current = False
    checked = 0
    progress_toggle_id = None
    for page in child_pages:
        if page["id"].replace("-", "") == current_clean:
            found_current = True
            continue
        if not found_current:
            continue
        checked += 1
        if checked > 5:
            break

        prev_blocks = _notion_api(token, "GET", f"blocks/{page['id']}/children?page_size=100")
        for b in prev_blocks.get("results", []):
            if b["type"] in ("heading_1", "heading_2"):
                txt = "".join(t.get("plain_text", "") for t in b[b["type"]].get("rich_text", []))
                if "プロジェクト進捗" in txt:
                    progress_toggle_id = b["id"]
                    print(f"  {page['child_page'].get('title','')} からプロジェクト進捗を検出", file=sys.stderr)
                    break
        if progress_toggle_id:
            break

    if not progress_toggle_id:
        print("  過去ページにプロジェクト進捗セクションが見つかりません。", file=sys.stderr)
        return False

    # トグル内の子ブロックを取得
    prev_children = _notion_api(token, "GET", f"blocks/{progress_toggle_id}/children?page_size=100")
    blocks_to_copy = []

    for b in prev_children.get("results", []):
        if b["type"] == "table" and b.get("has_children"):
            # テーブルの行を取得してコピー
            table_rows = _notion_api(token, "GET", f"blocks/{b['id']}/children?page_size=100")
            rows = []
            for row in table_rows.get("results", []):
                if row["type"] == "table_row":
                    rows.append({
                        "type": "table_row",
                        "table_row": {"cells": row["table_row"]["cells"]},
                    })
            blocks_to_copy.append({
                "type": "table",
                "table": {
                    "table_width": b["table"]["table_width"],
                    "has_column_header": b["table"]["has_column_header"],
                    "has_row_header": b["table"].get("has_row_header", False),
                    "children": rows,
                },
            })
        elif b["type"] == "paragraph":
            blocks_to_copy.append({
                "type": "paragraph",
                "paragraph": {"rich_text": b["paragraph"].get("rich_text", [])},
            })

    if blocks_to_copy:
        _notion_api(token, "PATCH", f"blocks/{toggle_id}/children", {
            "children": blocks_to_copy,
        })
        print("  前回ページからプロジェクト進捗をコピーしました。", file=sys.stderr)
        return True

    return False


def export_to_notion(
    tasks_by_channel: list[tuple[str, list[dict]]],
    threads_by_channel: list[tuple[str, list[dict]]],
    replace_mentions_fn,
    channel_map_by_id_rev: dict[str, str] | None = None,
) -> str:
    """子ページを作成し、タスクをDBテーブルとして書き込む。ページURLを返す。"""
    if channel_map_by_id_rev is None:
        channel_map_by_id_rev = {}
    token = os.environ.get("NOTION_TOKEN")
    page_id = os.environ.get("NOTION_PAGE_ID")
    if not token:
        print("エラー: 環境変数 NOTION_TOKEN を設定してください。", file=sys.stderr)
        sys.exit(1)
    if not page_id:
        print("エラー: 環境変数 NOTION_PAGE_ID を設定してください。", file=sys.stderr)
        sys.exit(1)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 1. 子ページを作成（タイトル = 日付）
    child_page = _notion_api(token, "POST", "pages", {
        "parent": {"page_id": page_id},
        "properties": {
            "title": [{"type": "text", "text": {"content": today}}],
        },
    })
    child_page_id = child_page["id"]
    print(f"  子ページ作成: {today}", file=sys.stderr)

    # 2. 「共有事項」トグル見出し（空）
    _append_toggle_heading(token, child_page_id, "共有事項")

    # 3. 「直近スレッド」見出し + チャンネルごとのDB
    _notion_api(token, "PATCH", f"blocks/{child_page_id}/children", {
        "children": [{
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": f"直近{THREAD_DAYS}日間のスレッド"}}]},
        }],
    })

    # threads_by_channel を dict に変換（チャンネル名 → スレッド一覧）
    threads_dict: dict[str, list[dict]] = {}
    for channel_name, threads in (threads_by_channel or []):
        threads_dict[channel_name] = threads

    total_thread_count = 0
    for ch_name in THREAD_CHANNELS:
        threads = threads_dict.get(ch_name, [])

        # チャンネル見出しを追加
        _notion_api(token, "PATCH", f"blocks/{child_page_id}/children", {
            "children": [{
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": [{"type": "text", "text": {"content": f"#{ch_name}"}}]},
            }],
        })

        if not threads:
            _notion_api(token, "PATCH", f"blocks/{child_page_id}/children", {
                "children": [{
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": "なし"}}]},
                }],
            })
            print(f"  #{ch_name}: スレッドなし", file=sys.stderr)
            continue

        # チャンネルごとにDBを作成
        thread_db = _notion_api(token, "POST", "databases", {
            "parent": {"type": "page_id", "page_id": child_page_id},
            "title": [{"type": "text", "text": {"content": f"#{ch_name} スレッド"}}],
            "is_inline": True,
            "properties": {
                "投稿者": {"title": {}},
                "内容リンク": {"url": {}},
                "要約": {"rich_text": {}},
                "元チャンネル": {"select": {}},
                "日時": {"rich_text": {}},
            },
        })
        thread_db_id = thread_db["id"]
        print(f"  #{ch_name} スレッドDB作成", file=sys.stderr)

        thread_ch = channel_map_by_id_rev.get(ch_name)

        for th in threads:
            parent_text = replace_mentions_fn(th["parent_text"])
            poster = replace_mentions_fn(th["parent_user"])
            ts_str = format_timestamp(th["parent_ts"])
            summary = summarize_thread(parent_text)
            orig_channel = ""

            content_link = None
            if th.get("is_forwarded") and th.get("original_thread"):
                orig = th["original_thread"]
                orig_ts_raw = orig["parent"].get("ts", "").replace(".", "")
                content_link = f"https://app.slack.com/archives/{orig['channel_id']}/p{orig_ts_raw}"
            elif thread_ch:
                ts_raw = th["parent_ts"].replace(".", "")
                content_link = f"https://app.slack.com/archives/{thread_ch}/p{ts_raw}"

            if th.get("is_forwarded") and th.get("original_thread"):
                orig_channel = th["original_thread"].get("channel_name", "")
                if th.get("stamp_users"):
                    poster = format_stamp_users(
                        th["stamp_users"], replace_mentions_fn
                    )
                summary = build_original_thread_summary(
                    th["original_thread"], replace_mentions_fn
                )
                if len(summary) > 2000:
                    summary = summary[:1997] + "..."

            properties = {
                "投稿者": {"title": [{"text": {"content": poster}}]},
                "要約": {"rich_text": [{"text": {"content": summary}}]},
                "日時": {"rich_text": [{"text": {"content": ts_str}}]},
                "内容リンク": {"url": content_link},
            }
            if orig_channel:
                properties["元チャンネル"] = {"select": {"name": f"#{orig_channel}"}}
            else:
                properties["元チャンネル"] = {"select": None}

            _notion_api(token, "POST", "pages", {
                "parent": {"database_id": thread_db_id},
                "properties": properties,
            })
            total_thread_count += 1
            time.sleep(0.3)

        print(f"  #{ch_name}: {total_thread_count} 件のスレッドをDBに追加", file=sys.stderr)

    print(f"  合計 {total_thread_count} 件のスレッドを追加", file=sys.stderr)

    # 4. 「プロジェクト進捗」トグル見出し（前回ページからコピー）
    progress_toggle_id = _append_toggle_heading(token, child_page_id, "プロジェクト進捗")
    _copy_progress_from_previous(token, page_id, child_page_id, progress_toggle_id)

    # 5. 「ピン留めタスク一覧」見出し + DB
    _notion_api(token, "PATCH", f"blocks/{child_page_id}/children", {
        "children": [{
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "ピン留めタスク一覧"}}]},
        }],
    })

    db = _notion_api(token, "POST", "databases", {
        "parent": {"type": "page_id", "page_id": child_page_id},
        "title": [{"type": "text", "text": {"content": "ピン留めタスク一覧"}}],
        "is_inline": True,
        "properties": {
            "タスク名": {"title": {}},
            "進捗": {"select": {"options": [
                {"name": "\U0001f7e2 問題なし", "color": "green"},
                {"name": "\U0001f7e1 少し注意", "color": "yellow"},
                {"name": "\U0001f534 ヘルプ必要", "color": "red"},
            ]}},
            "進捗説明": {"rich_text": {}},
            "チャンネル": {"select": {}},
            "ステータス": {"select": {}},
            "担当者": {"rich_text": {}},
            "期日": {"rich_text": {}},
            "説明": {"rich_text": {}},
            "リンク": {"url": {}},
        },
    })
    db_id = db["id"]
    print(f"  タスクDB作成完了", file=sys.stderr)

    # タスクをステータス順にソート
    all_tasks = []
    for channel_name, tasks in tasks_by_channel:
        for t in tasks:
            all_tasks.append((channel_name, t))
    all_tasks.sort(key=lambda x: STATUS_ORDER.get(x[1]["status"], 99), reverse=True)

    row_count = 0
    for channel_name, t in all_tasks:
        assignee = replace_mentions_fn(t["assignee"])
        description = replace_mentions_fn(t["description"])
        if len(description) > 2000:
            description = description[:1997] + "..."

        properties = {
            "タスク名": {"title": [{"text": {"content": t["task_name"]}}]},
            "チャンネル": {"select": {"name": f"#{channel_name}"}},
            "ステータス": {"select": {"name": t["status"]}},
            "担当者": {"rich_text": [{"text": {"content": assignee}}]} if assignee else {"rich_text": []},
            "期日": {"rich_text": [{"text": {"content": t["due_date"]}}]} if t["due_date"] else {"rich_text": []},
            "説明": {"rich_text": [{"text": {"content": description}}]} if description else {"rich_text": []},
            "リンク": {"url": t["permalink"]} if t["permalink"] else {"url": None},
        }
        _notion_api(token, "POST", "pages", {
            "parent": {"database_id": db_id},
            "properties": properties,
        })
        row_count += 1
        if row_count % 10 == 0:
            print(f"  {row_count} 件追加...", file=sys.stderr)
        time.sleep(0.3)

    print(f"  合計 {row_count} 件のタスクをDBに追加しました", file=sys.stderr)

    # 6. 「困ったことや質問」トグル見出し + 空パラグラフ
    help_toggle_id = _append_toggle_heading(
        token, child_page_id, "困ったことや質問、ヘルプ、確認待ちで連絡滞っているところ"
    )
    _notion_api(token, "PATCH", f"blocks/{help_toggle_id}/children", {
        "children": [{
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": []},
        }],
    })

    page_url = f"https://www.notion.so/{child_page_id.replace('-', '')}"
    return page_url


def _sanitize_markdown_for_canvas(text: str) -> str:
    """Slack Canvasで非対応のMarkdown構文を変換する。

    リスト項目内の引用ブロック（`  > ...`）はCanvasでサポートされないため、
    インデント付きテキストに置換する。
    """
    lines = []
    for line in text.split("\n"):
        # リスト内引用: "  > ..." → "    ..."
        if re.match(r"^(\s+)> (.*)$", line):
            m = re.match(r"^(\s+)> (.*)$", line)
            lines.append(f"{m.group(1)}  {m.group(2)}")
        else:
            lines.append(line)
    return "\n".join(lines)


def _build_canvas_markdown(
    tasks_by_channel: list[tuple[str, list[dict]]],
    threads_by_channel: list[tuple[str, list[dict]]],
    replace_mentions_fn,
    get_user_name_fn,
    channel_map_by_name: dict[str, str] | None = None,
) -> str:
    """Canvas用のテーブル形式Markdownを生成する。"""
    if channel_map_by_name is None:
        channel_map_by_name = {}
    lines = []
    now_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M (JST)")
    lines.append(f"取得日時: {now_str}")
    lines.append("")

    # --- タスクテーブル（ステータス順にソート） ---
    all_tasks = []
    for channel_name, tasks in tasks_by_channel:
        for t in tasks:
            all_tasks.append((channel_name, t))
    all_tasks.sort(key=lambda x: STATUS_ORDER.get(x[1]["status"], 99), reverse=True)

    lines.append("# ピン留めタスク一覧")
    lines.append("")
    lines.append("| チャンネル | タスク名 | ステータス | 担当者 | 期日 | 進捗 | 進捗説明 |")
    lines.append("|-----------|---------|----------|--------|-----|------|---------|")
    for channel_name, t in all_tasks:
        assignee = replace_mentions_fn(t["assignee"])
        task_name = t["task_name"].replace("|", "/")
        status = t["status"]
        due = t["due_date"] or "-"
        lines.append(f"| #{channel_name} | {task_name} | {status} | {assignee} | {due} | | |")
    lines.append("")

    # --- 直近スレッド（テーブル形式） ---
    if threads_by_channel:
        lines.append("---")
        lines.append("")
        lines.append(f"# 直近{THREAD_DAYS}日間のスレッド")
        lines.append("")
        for channel_name, threads in threads_by_channel:
            lines.append(f"## #{channel_name}")
            lines.append("")
            lines.append("| 投稿者 | 要約 | スレッドリンク | 日時 |")
            lines.append("|--------|------|---------------|------|")
            for th in threads:
                user_name = get_user_name_fn(th["parent_user"])
                ts_str = format_timestamp(th["parent_ts"])
                parent_text = replace_mentions_fn(th["parent_text"])

                if th.get("is_forwarded") and th.get("original_thread"):
                    if th.get("stamp_users"):
                        user_name = format_stamp_users(
                            th["stamp_users"], replace_mentions_fn
                        )
                    summary = build_original_thread_summary(
                        th["original_thread"], replace_mentions_fn
                    )
                else:
                    summary = parent_text

                # 要約をテーブルセル用に整形（改行→スペース、パイプをエスケープ、80文字制限）
                summary_clean = summary.replace("\n", " ").replace("|", "/").strip()
                if len(summary_clean) > 80:
                    summary_clean = summary_clean[:80] + "..."

                # スレッドリンク生成
                thread_link = ""
                if th.get("is_forwarded") and th.get("original_thread"):
                    orig = th["original_thread"]
                    orig_ts_raw = orig["parent"].get("ts", "").replace(".", "")
                    thread_link = f"https://app.slack.com/archives/{orig['channel_id']}/p{orig_ts_raw}"
                else:
                    ch_obj = channel_map_by_name.get(channel_name)
                    if ch_obj:
                        ts_raw = th["parent_ts"].replace(".", "")
                        thread_link = f"https://app.slack.com/archives/{ch_obj}/p{ts_raw}"

                user_name_clean = user_name.replace("|", "/")
                link_cell = f"[リンク]({thread_link})" if thread_link else "-"
                lines.append(f"| {user_name_clean} | {summary_clean} | {link_cell} | {ts_str} |")
            lines.append("")

    # --- MTG用セクション ---
    lines.append("---")
    lines.append("")
    lines.append("# 困ったことや質問、ヘルプ、確認待ちで連絡滞っているところ")
    lines.append("")
    lines.append("")
    lines.append("# その他")
    lines.append("")
    lines.append("")

    return "\n".join(lines)


def export_to_slack_canvas(
    client: WebClient,
    parent_canvas_id: str,
    tasks_by_channel: list[tuple[str, list[dict]]],
    threads_by_channel: list[tuple[str, list[dict]]],
    replace_mentions_fn,
    get_user_name_fn,
    channel_map_by_name: dict[str, str] | None = None,
) -> str:
    """新しいCanvasを作成し、親Canvasの末尾にリンクを追記する。新Canvas IDを返す。"""
    now = datetime.now(timezone(timedelta(hours=9)))  # JST
    title = f"{now.strftime('%y/%-m/%-d')}_HPH定例"

    canvas_md = _build_canvas_markdown(
        tasks_by_channel, threads_by_channel,
        replace_mentions_fn, get_user_name_fn,
        channel_map_by_name=channel_map_by_name,
    )
    canvas_md = _sanitize_markdown_for_canvas(canvas_md)

    # 1. 新しいCanvasを作成
    resp = client.canvases_create(
        title=title,
        document_content={"type": "markdown", "markdown": canvas_md},
    )
    new_canvas_id = resp["canvas_id"]
    print(f"  Canvas作成完了: {new_canvas_id} ({title})", file=sys.stderr)

    # 2. 親Canvasの末尾にリンクを追記
    team_id = client.auth_test()["team_id"]
    canvas_url = f"https://nextstageinc.slack.com/docs/{team_id}/{new_canvas_id}"
    link_md = f"\n[{title}]({canvas_url})\n"
    client.canvases_edit(
        canvas_id=parent_canvas_id,
        changes=[{
            "operation": "insert_at_end",
            "document_content": {"type": "markdown", "markdown": link_md},
        }],
    )
    print(f"  親Canvas {parent_canvas_id} にリンクを追記しました", file=sys.stderr)

    return new_canvas_id


def main():
    parser = argparse.ArgumentParser(description="Slack ピン留めタスク一覧 + 直近スレッド取得")
    parser.add_argument("--output", "-o", help="Markdown出力先ファイルパス")
    parser.add_argument("--notion", action="store_true", help="Notionページに出力する")
    parser.add_argument("--public-only", action="store_true", help="プライベートチャンネルを除外する")
    parser.add_argument("--exclude-assignees", nargs="*", default=[], help="除外する担当者名のリスト")
    parser.add_argument("--slack-canvas", metavar="CANVAS_ID", help="親Canvas IDを指定して新規Canvasを作成・リンク追記")
    args = parser.parse_args()

    client = get_client()

    print("チャンネル一覧を取得中...", file=sys.stderr)
    channels = fetch_channels(client)
    if args.public_only:
        channels = [ch for ch in channels if not ch.get("is_private")]
    channels.sort(key=lambda ch: ch["name"])
    channel_map = {ch["name"]: ch for ch in channels}
    print(f"{len(channels)} チャンネルを取得しました。", file=sys.stderr)

    # ===== ピン留めタスク取得 =====
    tasks_by_channel: list[tuple[str, list[dict]]] = []

    for i, ch in enumerate(channels):
        print(
            f"  [{i + 1}/{len(channels)}] #{ch['name']} のピンを取得中...",
            file=sys.stderr,
        )
        pins = fetch_pins(client, ch["id"])
        channel_tasks = []
        for pin in pins:
            task = parse_task_info(pin)
            if task:
                channel_tasks.append(task)
        if channel_tasks:
            tasks_by_channel.append((ch["name"], channel_tasks))
        time.sleep(3)

    total = sum(len(tasks) for _, tasks in tasks_by_channel)
    print(f"\n合計 {total} 件のタスクを取得しました。", file=sys.stderr)

    # ===== 直近スレッド取得 (#nicehero, #knowledge) =====
    threads_by_channel: list[tuple[str, list[dict]]] = []

    for ch_name in THREAD_CHANNELS:
        ch = channel_map.get(ch_name)
        if not ch:
            print(f"  警告: #{ch_name} が見つかりません。スキップします。", file=sys.stderr)
            continue
        print(f"\n#{ch_name} の直近{THREAD_DAYS}日間のスレッドを取得中...", file=sys.stderr)
        try:
            threads = fetch_recent_threads(client, ch["id"], ch_name, THREAD_DAYS)
            if threads:
                threads_by_channel.append((ch_name, threads))
        except SlackApiError as e:
            print(f"  スキップ: #{ch_name} ({e.response['error']})", file=sys.stderr)

    # ===== 転送メッセージの元スレッド取得 =====
    channel_map_by_id = {ch["id"]: ch["name"] for ch in channels}

    for ch_name, threads in threads_by_channel:
        ch = channel_map.get(ch_name)
        if not ch:
            continue
        current_ch_id = ch["id"]

        for th in threads:
            raw = th.get("raw_parent", {})
            source = detect_forwarded_source(raw, current_ch_id)
            if not source:
                continue

            orig_ch_id, orig_ts = source
            orig_ch_name = channel_map_by_id.get(orig_ch_id)
            if not orig_ch_name:
                try:
                    info = client.conversations_info(channel=orig_ch_id)
                    orig_ch_name = info["channel"]["name"]
                    channel_map_by_id[orig_ch_id] = orig_ch_name
                except SlackApiError:
                    orig_ch_name = orig_ch_id
            print(
                f"    転送元を取得中: #{orig_ch_name}...",
                file=sys.stderr,
            )

            try:
                resp = client.conversations_replies(
                    channel=orig_ch_id, ts=orig_ts, limit=200
                )
                orig_messages = resp.get("messages", [])
            except SlackApiError:
                orig_messages = []
            time.sleep(1.5)

            if orig_messages:
                orig_parent = orig_messages[0]
                orig_replies = orig_messages[1:]
                th["is_forwarded"] = True
                th["original_thread"] = {
                    "parent": orig_parent,
                    "replies": orig_replies,
                    "channel_id": orig_ch_id,
                    "channel_name": orig_ch_name,
                }

                # スタンプを押したユーザーを特定（対象絵文字のみ）
                target_emoji = THREAD_CHANNEL_EMOJI.get(ch_name)
                reactions = orig_parent.get("reactions", [])
                stamp_users = []
                for reaction in reactions:
                    emoji = reaction.get("name", "")
                    if target_emoji and emoji != target_emoji:
                        continue
                    for uid in reaction.get("users", []):
                        stamp_users.append((emoji, uid))
                th["stamp_users"] = stamp_users

    # ===== ユーザー名解決 =====
    print("\nユーザー名を解決中...", file=sys.stderr)
    all_texts = []
    for _, tasks in tasks_by_channel:
        for t in tasks:
            all_texts.extend([t["assignee"], t["description"]])
    for _, threads in threads_by_channel:
        for th in threads:
            all_texts.append(th["parent_text"])
            all_texts.append(th["parent_user"])
            for r in th["replies"]:
                all_texts.append(r.get("text", ""))
                all_texts.append(r.get("user", ""))
            # 転送元スレッドのユーザーIDも収集
            if th.get("original_thread"):
                orig = th["original_thread"]
                all_texts.append(orig["parent"].get("text", ""))
                all_texts.append(orig["parent"].get("user", ""))
                for r in orig["replies"]:
                    all_texts.append(r.get("text", ""))
                    all_texts.append(r.get("user", ""))
            for _emoji, uid in th.get("stamp_users", []):
                all_texts.append(f"<@{uid}>")

    user_ids = collect_user_ids(*all_texts)
    user_map = resolve_users(client, user_ids)

    def replace_mentions(text: str) -> str:
        for uid, name in user_map.items():
            text = text.replace(f"<@{uid}>", name)
        return text

    def get_user_name(uid: str) -> str:
        return user_map.get(uid, uid)

    # ===== 担当者フィルタ =====
    if args.exclude_assignees:
        excluded = set(args.exclude_assignees)
        filtered = []
        for channel_name, tasks in tasks_by_channel:
            kept = [t for t in tasks if replace_mentions(t["assignee"]) not in excluded]
            if kept:
                filtered.append((channel_name, kept))
        tasks_by_channel = filtered
        total = sum(len(tasks) for _, tasks in tasks_by_channel)
        print(f"担当者フィルタ後: {total} 件のタスク", file=sys.stderr)

    # ===== 出力組み立て =====
    lines = []
    lines.append("# Slack ピン留めタスク一覧（HPHero-task）")
    lines.append("")
    lines.append(f"取得日時: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M (UTC)')}")
    lines.append("※ ステータスが「完了」「クローズ」のタスクは除外しています")
    lines.append("")

    for channel_name, tasks in tasks_by_channel:
        lines.append(f"## #{channel_name}")
        lines.append("")
        for t in tasks:
            assignee = replace_mentions(t["assignee"])
            description = replace_mentions(t["description"])
            lines.append(f"### {t['task_name']}")
            lines.append("")
            lines.append(f"- **ステータス:** {t['status']}")
            if assignee:
                lines.append(f"- **担当者:** {assignee}")
            if t["due_date"]:
                lines.append(f"- **期日:** {t['due_date']}")
            if t["permalink"]:
                lines.append(f"- **リンク:** {t['permalink']}")
            if description:
                lines.append("- **説明:**")
                for line in description.split("\n"):
                    lines.append(f"  > {line}")
            lines.append("")

    # --- 直近スレッドセクション ---
    lines.append("---")
    lines.append("")
    lines.append(f"# 直近{THREAD_DAYS}日間のスレッド")
    lines.append("")

    for channel_name, threads in threads_by_channel:
        lines.append(f"## #{channel_name}")
        lines.append("")
        for th in threads:
            user_name = get_user_name(th["parent_user"])
            ts_str = format_timestamp(th["parent_ts"])
            parent_text = replace_mentions(th["parent_text"])

            # 転送メッセージの場合
            if th.get("is_forwarded") and th.get("original_thread"):
                if th.get("stamp_users"):
                    user_name = format_stamp_users(
                        th["stamp_users"], replace_mentions
                    )
                lines.append(f"### {user_name} ({ts_str})")
                lines.append("")
                lines.append(
                    build_original_thread_summary(
                        th["original_thread"], replace_mentions
                    )
                )
                lines.append("")
            else:
                lines.append(f"### {user_name} ({ts_str})")
                lines.append("")
                for line in parent_text.split("\n"):
                    lines.append(f"> {line}")
                lines.append("")

            if th["replies"]:
                for r in th["replies"]:
                    r_user = get_user_name(r.get("user", ""))
                    r_ts = format_timestamp(r["ts"]) if r.get("ts") else ""
                    r_text = replace_mentions(r.get("text", ""))
                    lines.append(f"- **{r_user}** ({r_ts})")
                    for line in r_text.split("\n"):
                        lines.append(f"  > {line}")
                    lines.append("")

    output_text = "\n".join(lines)

    channel_name_to_id = {ch["name"]: ch["id"] for ch in channels}

    if args.notion:
        print("\nNotionページに出力中...", file=sys.stderr)
        page_url = export_to_notion(
            tasks_by_channel, threads_by_channel, replace_mentions,
            channel_map_by_id_rev=channel_name_to_id,
        )
        print(f"Notionページに出力しました: {page_url}", file=sys.stderr)

    if args.slack_canvas:
        print("\nSlack Canvasに出力中...", file=sys.stderr)
        new_canvas_id = export_to_slack_canvas(
            client, args.slack_canvas,
            tasks_by_channel, threads_by_channel,
            replace_mentions, get_user_name,
            channel_map_by_name=channel_name_to_id,
        )
        print(f"Slack Canvasを作成しました: {new_canvas_id}", file=sys.stderr)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_text)
        print(f"\n結果を {args.output} に保存しました。", file=sys.stderr)

    if not args.output and not args.notion and not args.slack_canvas:
        print(output_text)


if __name__ == "__main__":
    main()
