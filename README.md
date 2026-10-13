# 台股每日分析 Pipeline (Stock Daily Pipeline)

每天盤後自動產出「今日命中股清單 + 熱門產業」,推到 Discord。預設 16:00 GMT+8 觸發,設計為 fail-closed(預檢失敗立即終止,不汙染 Discord 訊息)。

## 檔案分工

| 階段 | 腳本 | 角色 |
|---|---|---|
| **預檢 + 框架** | `~/.hermes/scripts/run_stock_pipeline.sh` | 抓 Yahoo 榜 → cmp 昨天榜判斷開/休市 → 4 step 依序跑。每個 step 由 `run_step` 統一印 `▶️ Step: {name}` 標頭 + 分隔線。 |
| **Step 1** | `scraper_yahoo_rank.py` → `reader_yahoo_csv.py` | 預檢抓的榜寫成 CSV,Step 1 讀 CSV(不重抓 Yahoo)。每天 Yahoo API 請求 1 次。 |
| **Step 2** | `stock_scraper.py` | TWSE T86 法人買超 × MI_INDEX 量變化(20 檔)。支援 `--date YYYYMMDD` 抓歷史日。 |
| **Step 3** | `stock_repeat_compare.py` | A. Yahoo 5天內 ≥3 次 / B. 雙榜交集 / C. Screener 連續上榜。 |
| **Step 4** | `daily_industry_analyzer.py` | 合併 Yahoo + Screener Union 去重 → 產業熱度 + MA5/MA20。 |

詳細陷阱、決策脈絡、訊號標籤邏輯見 `~/.hermes/skills/research/stock-pipeline/`。

## 必要環境

- Python 3.10+(已內建 `urllib`、`json`,不依賴外部套件跑 T86 / MI_INDEX)
- `uv`(讓 `scraper_yahoo_rank.py` 走 `requests` + `beautifulsoup4`)
- FinMind API token(放在 `/home/ubuntu/.hermes/secrets/finmind_token`,**絕不進 git**)

## Clone 後設定

```bash
# 1. FinMind token
mkdir -p /home/ubuntu/.hermes/secrets
echo "# your JWT token here" > /home/ubuntu/.hermes/secrets/finmind_token
# 第一行是註解、第二行是 token(scraper 會取最後一行)

# 2. 跑一次 stock_industry.py 產 cache(全市場產業別)
#    注意:這個腳本目前在 pipeline 之外,要手動跑
python3 stock_industry.py
# → 產出 stock_industry_cache.csv(已 .gitignore 排除,需本地重產)

# 3. 手動驗證一次(可選)
bash ~/.hermes/scripts/run_stock_pipeline.sh
# 檢查 Discord 訊息是否符合預期(4 個 step 區塊格式)
```

## Cron job 設定摘要

| 欄位 | 值 |
|---|---|
| `job_id` | `f29b8b92ad1e` |
| `name` | `[Research-Agent] Stock Daily Pipeline 每日 16:00 GMT+8` |
| `schedule` | `30 16 * * *` (16:30 GMT+8) |
| `no_agent` | `true`(只跑 shell,scheduler 直接送 stdout 到 Discord) |
| `script` | `run_stock_pipeline.sh` |
| `workdir` | `/home/ubuntu/shared_from_jack` |
| `deliver` | `discord:1511040268093231195` |

## .gitignore 排除清單速覽

- `*.token` / `*_token` / `secrets/` — 任何 secret
- `stock_industry_cache.csv` — 本地 cache,clone 後重產
- `stock_screener_*.csv` / `repeat_compare_*.csv` / `yahoo_rank_intersection_*.csv` — 每日產出
- `TWSE_FAILED_*.marker` / `*_TWSE_FAILED.csv` — TWSE 抓失敗的標記
- `__pycache__/` / `*.log`
- `stock_screener.py` / `stock_industry.py` / `run_yahoo_rank.sh` — 早期 / 工具腳本,不入版
- `test_*.py` / `_patch_token.py` — 測試 / 工具
