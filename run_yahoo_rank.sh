#!/bin/bash
# Yahoo 股市排行榜爬蟲 — daily cron wrapper
# 產出：當天 CSV + Discord 訊息（stdout）
# 失敗：stdout 開頭加 :rotating_light: ERROR，cron 會原樣送到 Discord

set -e

WORKDIR="/home/ubuntu/shared_from_jack"
SCRIPT="scraper_yahoo_rank.py"
TODAY=$(date +%Y%m%d)
CSV="yahoo_rank_intersection_${TODAY}.csv"

cd "$WORKDIR"

# 1. 跑爬蟲（uv 自動處理 requests + bs4）
uv run --with requests --with beautifulsoup4 python3 "$SCRIPT" > /tmp/scraper_stdout.log 2>&1
SCRAPE_EXIT=$?

if [ $SCRAPE_EXIT -ne 0 ]; then
    echo ":rotating_light: **Yahoo 爬蟲失敗** (exit $SCRAPE_EXIT)"
    echo '```'
    cat /tmp/scraper_stdout.log
    echo '```'
    exit 1
fi

# 2. 確認 CSV 存在
if [ ! -f "$CSV" ]; then
    echo ":rotating_light: **Yahoo 爬蟲完成但找不到 CSV**: \`$CSV\`"
    exit 1
fi

# 3. 把 stdout 完整帶出（yahoo 排行榜的股票清單）
echo "📊 **Yahoo 股市交集榜**（成交量 × 漲幅）— $(date +%Y-%m-%d)"
echo ""
cat /tmp/scraper_stdout.log
echo ""
echo "📁 CSV: \`${WORKDIR}/${CSV}\` ($(wc -l < "$CSV") 行)"
