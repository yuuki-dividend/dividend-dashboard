#!/bin/bash
# 毎日深夜0時に自動実行されるスクリプト
# データ更新 → バリデーション → GitHub Pagesデプロイ

LOG="/Users/yuukiyasushishigeru/dividend-dashboard/nightly_update.log"
DIR="/Users/yuukiyasushishigeru/dividend-dashboard"

echo "========================================" >> "$LOG"
echo "自動更新開始: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG"

cd "$DIR"

# Phase 1-3: データ更新＋バリデーション
/usr/bin/python3 update_all.py >> "$LOG" 2>&1
UPDATE_RESULT=$?

if [ $UPDATE_RESULT -ne 0 ]; then
    echo "❌ データ更新に失敗しました" >> "$LOG"
    echo "========================================" >> "$LOG"
    exit 1
fi

echo "✅ データ更新＋バリデーション完了" >> "$LOG"

# Phase 4: GitHub Pagesへデプロイ
export PATH="$HOME/bin:$PATH"
bash deploy.sh >> "$LOG" 2>&1
DEPLOY_RESULT=$?

if [ $DEPLOY_RESULT -ne 0 ]; then
    echo "❌ デプロイに失敗しました" >> "$LOG"
else
    echo "✅ デプロイ完了" >> "$LOG"
fi

echo "自動更新終了: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG"
echo "========================================" >> "$LOG"
