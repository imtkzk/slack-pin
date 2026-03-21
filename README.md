# Slack Pin to Notion Export

Slackの各チャンネルにピン留めされたHPHero-taskタスクと、`#nicehero` / `#knowledge` の直近1週間のスレッドを収集し、Notionページ、Slack Canvas、または Markdown ファイルに出力するツール。

## 機能

- **ピン留めタスク収集** — 参加中の全チャンネルからHPHero-taskボットが投稿したピン留めメッセージを取得し、タスク名・ステータス・担当者・期日を抽出（「完了」「クローズ」は除外）
- **直近スレッド収集** — `#nicehero` / `#knowledge` の直近7日間のスレッドと返信を取得（転送メッセージの元スレッドも自動追跡）
- **Notion出力** — 収集結果をNotionページに構造化して出力
- **Slack Canvas出力** — 「日付_HPH定例」名の新規Canvasを作成し、親Canvas（定例アジェンダ/議事録）にリンクを追記
- **Markdownファイル出力** — ローカルにMarkdownファイルとして保存
- **フィルタ機能** — プライベートチャンネル除外、特定担当者除外に対応

## セットアップ

### 1. Slack App の準備

[Slack API](https://api.slack.com/apps) でAppを作成し、以下のUser Token Scopesを付与:

- `pins:read` — ピン留めメッセージの読み取り
- `channels:read` — パブリックチャンネル一覧の取得
- `channels:history` — チャンネルメッセージの読み取り
- `groups:read` — プライベートチャンネル一覧の取得
- `users:read` — ユーザー名の解決
- `canvases:write` — Canvas作成・編集（Canvas出力時のみ）

### 2. 環境変数

`.env` ファイルまたは環境変数で以下を設定:

```
SLACK_USER_TOKEN=xoxp-...
NOTION_TOKEN=ntn_...        # Notion出力時のみ
NOTION_PAGE_ID=...           # Notion出力時のみ
```

### 3. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

## 使い方

```bash
# コンソールに出力
python get_pins.py

# Markdownファイルに出力
python get_pins.py --output pinned_messages.md

# Notionページに出力
python get_pins.py --notion

# プライベートチャンネルを除外してNotionに出力
python get_pins.py --notion --public-only

# 特定担当者を除外してNotionに出力
python get_pins.py --notion --exclude-assignees "担当者A" "担当者B"

# Slack Canvasに出力（親CanvasにリンクされたCanvas「日付_HPH定例」を新規作成）
python get_pins.py --slack-canvas F09BML7LGGL

# Notion + Slack Canvas 両方に出力
python get_pins.py --notion --slack-canvas F09BML7LGGL --public-only
```

## GitHub Actions による自動実行

`.github/workflows/notion-export.yml` により、毎週金曜 9:00 (JST) に自動でNotionへエクスポートされます。手動実行（workflow_dispatch）も可能です。

必要なRepository Secrets:

- `SLACK_USER_TOKEN`
- `NOTION_TOKEN`
- `NOTION_PAGE_ID`
- `SLACK_CANVAS_ID` — 親Canvas ID（定例アジェンダ/議事録）
