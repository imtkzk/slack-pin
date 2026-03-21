---
name: notion-export-filtered
description: Slackのピン留めタスクをフィルタ付きでNotionに出力する（プライベートチャンネル除外、特定担当者除外）
user_invocable: true
---

# Notion Export (Filtered)

Slackからピン留めタスク一覧と直近スレッドを取得し、NotionページにインラインDBとして出力します。
以下のフィルタが適用されます:
- プライベートチャンネルは除外
- 担当者が「my1806」「Quynh Anh （アイン）」「Kyoichi Watanabe」「Noriyuki Mizuno」「hisatake」「Kunihiro Okamura」のタスクは除外

## 手順

1. `.env` ファイルに以下の環境変数が設定されていることを確認:
   - `SLACK_USER_TOKEN` (xoxp-...)
   - `NOTION_TOKEN`
   - `NOTION_PAGE_ID`

2. 以下のコマンドを実行:

```bash
cd /Users/imoto/Desktop/slack-pin && source .venv/bin/activate && python get_pins.py --notion --public-only --exclude-assignees "my1806" "Quynh Anh （アイン）" "Kyoichi Watanabe" "Noriyuki Mizuno" "hisatake" "Kunihiro Okamura"
```

3. 実行結果を確認し、NotionページのURLをユーザーに伝える。

4. エラーが発生した場合:
   - `SLACK_USER_TOKEN` 関連: Slackアプリの設定とトークンを確認
   - `NOTION_TOKEN` / `NOTION_PAGE_ID` 関連: Notion integrationの設定を確認
   - API rate limit: 時間をおいて再実行
