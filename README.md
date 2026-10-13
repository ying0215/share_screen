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

## 對應的本地資料夾

```
/home/ubuntu/shared_from_jack
```

就是 cron job `f29b8b92ad1e` 設定的 `workdir`,pipeline 一直跑在這裡。本地已經 `git init` + commit `c4238a1`,但**目前沒設 remote**(避免 token 寫入任何檔案)。所有修改在這個資料夾做完 → commit → 推上去。

## 本地修改後的同步 SOP

```bash
cd /home/ubuntu/shared_from_jack

# 1. 看改了什麼
git status

# 2. 確認沒意外檔案混入(secrets / CSV 產出 / 舊腳本都該被 .gitignore 擋掉)
git check-ignore -v 新檔名    # 確認有被擋

# 3. Stage + commit
git add .
git commit -m "描述這次改了什麼"

# 4. 推上去(用一次性 URL,token 不留任何地方)
#    傑克去 https://github.com/settings/personal-access-tokens 拿新 token 貼上:
git push https://x-access-token:你的github_pat@github.com/ying0215/share_screen.git main
```

**為什麼不 `git remote add origin` + `git push -u origin main`**:
- 設 origin 就要把 token 存進 `.git/config`(`git remote set-url ...` 帶 token),違反「token 不進任何檔案」原則
- 之後要 push 還是會問密碼(token 不存在 config 會跳輸入框)
- 一次性 URL 推完就丟,本地零痕跡

**之後傑克的工作流**:
- **只改本地** → 推上去用一次性 URL
- **clone 回別台電腦跑** → 看「Clone 後設定」那段(產 token + 產 cache)
- **跨裝置同步** → 兩邊都用一次性 URL push/pull

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
