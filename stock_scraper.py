import urllib.request
import json
import time
import logging
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import os

# ── Logging 設定 ──────────────────────────────────────────────────────────────
WORKDIR = Path(__file__).resolve().parent
LOG_PATH = WORKDIR / "stock_scraper_debug.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
log = logging.getLogger("stock_scraper")

# ── 重試參數（可被外界環境變數覆寫）─────────────────────────────────────────
_RETRY_ATTEMPTS = int(os.environ.get("STOCK_RETRY_ATTEMPTS", 5))
_RETRY_INTERVAL = int(os.environ.get("STOCK_RETRY_INTERVAL", 60))  # 秒

def pad_display_width(s: str, target_width: int) -> str:
    """
    把 s 補空白到 target_width 顯示寬（1 ASCII = 1, 1 中文 = 2）。
    Discord / 等寬終端會用 2 倍寬顯示中文，Python 的 str.ljust() 算半形寬，
    會導致中文欄位沒對齊。

    用 unicodedata.east_asian_width()：
      - 'W' (全形) / 'F' (全形) / 'A' (曖昧，看字型) → 算 2
      - 'Na' (窄) / 'H' (半形) → 算 1
    """
    import unicodedata
    width = 0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ('W', 'F', 'A'):
            width += 2
        else:
            width += 1
    if width >= target_width:
        return s
    return s + ' ' * (target_width - width)


def fetch_json(url, timeout=30):
    log.debug("→ GET %s (timeout=%ss)", url, timeout)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode('utf-8')
            log.debug("  ← HTTP %s, body len=%d", response.status, len(body))
            return json.loads(body)
    except Exception as e:
        log.warning("  ✗ %s: %s", type(e).__name__, e)
        raise

def latest_workday(after: datetime) -> str:
    """從 after 往前找最近一個工作日 (Mon-Fri)，最多回退 7 天。"""
    d = after
    for _ in range(7):
        d -= timedelta(days=1)
        if d.weekday() < 5:  # 0=Mon ... 4=Fri
            return d.strftime("%Y%m%d")
    raise RuntimeError("找不到工作日")


def mi_has_daily_quote(date_str: str) -> bool:
    """MI_INDEX 對 date_str 是否有「每日收盤行情」tables。
    用於處理補班日 / 颱風假 / 國定假日 — 這些日子 weekday() < 5
    但 MI_INDEX 不一定有收盤行情。
    """
    try:
        mi = fetch_json(
            f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={date_str}&type=ALL"
        )
        return any("每日收盤行情" in t.get("title", "") for t in mi.get("tables", []))
    except Exception:
        return False


def resolve_yesterday_with_mi(today_obj: datetime) -> str:
    """找最近一個「有 MI_INDEX 每日收盤行情資料」的工作日，最多回退 10 天。

    解決「昨天是補班日但沒收盤行情」這個邊界條件——
    例如 2026-09-28 (週一) 補班但 MI_INDEX 沒資料。
    """
    d = today_obj
    for _ in range(10):
        d -= timedelta(days=1)
        if d.weekday() >= 5:  # 週末直接跳
            continue
        d_str = d.strftime("%Y%m%d")
        if mi_has_daily_quote(d_str):
            return d_str
    raise RuntimeError("找不到有 MI_INDEX 資料的工作日")

def resolve_dates(today_override: str | None = None) -> tuple[str, str]:
    """抓最近兩個有資料的工作日。TWSE 颱風假會回空，所以用實測 fallback。

    時間 ≥ 15:30：retry 5 次（60s 間隔），全部失敗則寫入標記檔後 return (None, None)。
    時間 < 15:30：原本的 7 天 fallback 行為（早上 / 手動觸發用）。
    為什麼不再 raise：pipeline 在 TWSE 完全掛掉時仍要能輸出 Yahoo CSV。

    today_override：CLI --date 傳入時使用，today=指定日、yesterday 由 MI_INDEX 自動找。
                    不走 datetime.now()、不走 retry、不走 7 天 fallback。
    """
    # CLI 路徑：指定日期 → 直接抓，失敗就報錯（不 retry / 不 fallback）
    if today_override:
        if not (len(today_override) == 8 and today_override.isdigit()):
            raise ValueError(f"--date 格式錯誤：{today_override}（需 YYYYMMDD）")
        # 模擬 00:00 的 datetime，強制走「< 15:30」分支 → 1 次抓取、不 retry
        today_obj = datetime.strptime(today_override, "%Y%m%d")
        def _try_t86_cli(date_str: str):
            try:
                data = fetch_json(
                    f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALL"
                )
                if data.get("data") and data.get("stat") == "OK":
                    return data
            except Exception:
                pass
            return None

        data = _try_t86_cli(today_override)
        if data is not None:
            yesterday = resolve_yesterday_with_mi(today_obj)
            log.info("[CLI] 指定 today=%s, 自動 yesterday=%s", today_override, yesterday)
            return today_override, yesterday
        log.error("[CLI] 指定日期 %s 抓不到 T86 資料（不 retry、不 fallback）", today_override)
        return None, None

    today_obj = datetime.now()
    today = today_obj.strftime("%Y%m%d")
    past_cutoff = today_obj.replace(hour=15, minute=30, second=0, microsecond=0)
    post_market = today_obj >= past_cutoff

    def _try_t86(date_str: str):
        """抓單一日期的 T86，成功回傳 data，失敗（含空資料）回傳 None。"""
        try:
            data = fetch_json(
                f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALL"
            )
            if data.get("data") and data.get("stat") == "OK":
                return data
        except Exception:  # logging 已在 fetch_json 處理，這裡只負責控制流
            pass
        return None

    # 先試當天
    data = _try_t86(today)
    if data is not None:
        yesterday = resolve_yesterday_with_mi(today_obj)
        log.info("✓ today=%s, yesterday=%s", today, yesterday)
        return today, yesterday

    # 當天沒資料：根據時間決定策略
    if post_market:
        # 15:30 後：retry _RETRY_ATTEMPTS 次（_RETRY_INTERVAL s 間隔）
        log.warning("%s ≥ 15:30，T86 尚未 flush，重試 %s 次（間隔 %ss）...",
                    today, _RETRY_ATTEMPTS, _RETRY_INTERVAL)
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            time.sleep(_RETRY_INTERVAL)
            log.info("  重試 %s/%s...", attempt, _RETRY_ATTEMPTS)
            data = _try_t86(today)
            if data is not None:
                yesterday = resolve_yesterday_with_mi(today_obj)
                log.info("  ✓ 重試成功 today=%s, yesterday=%s", today, yesterday)
                return today, yesterday

        # 全部失敗：寫標記檔，溫和回報，不 raise
        # 2026-10-13 重構：TWSE_FAILED marker 也進 output/screener/
        MARKER = WORKDIR / "output" / "screener" / f"TWSE_FAILED_{today}.marker"
        MARKER.parent.mkdir(parents=True, exist_ok=True)
        MARKER.write_text(f"last_try={datetime.now().isoformat()}\nattempts={_RETRY_ATTEMPTS}\ninterval={_RETRY_INTERVAL}\n")
        log.error("✗ %s 已過 15:30，T86 連續 %s 次抓取仍失敗。已寫入標記檔：%s",
                  today, _RETRY_ATTEMPTS, MARKER)
        return None, None

    # 15:30 前：保留原本 fallback 行為（早上 / 手動觸發用）
    log.info("%s 仍在 15:30 前，啟用 fallback 往前找...", today)
    candidate = today_obj
    for _ in range(7):
        candidate -= timedelta(days=1)
        if candidate.weekday() >= 5:
            continue
        cand_str = candidate.strftime("%Y%m%d")
        data = _try_t86(cand_str)
        if data is not None:
            today = cand_str
            yesterday = resolve_yesterday_with_mi(candidate)
            log.info("  ✓ fallback today=%s, yesterday=%s", today, yesterday)
            return today, yesterday

    log.error("連續 7 天都抓不到 T86 資料")
    return None, None

def validate_output(csv_path: Path, expected_date: str, rows: list) -> tuple[bool, list[str]]:
    """C 方案：產出後自我驗證。回傳 (passed, warnings)。

    檢查項目：
      1. 檔名日期 == expected_date（防 9/22 撞到「寫成昨天日期」這類 bug）
      2. 資料筆數 >= 5（< 5 通常是只抓到 ETF 或部分市場）
      3. 至少有一筆成交量相對變化 > 1.0（避免拿到全 0 的死資料）
    """
    warnings = []

    # 檢查 1：檔名日期
    actual_date = csv_path.stem.replace("stock_screener_", "")
    if actual_date != expected_date:
        warnings.append(f"檔名日期不符：CSV={actual_date}, 預期={expected_date}")

    # 檢查 2：資料筆數
    if len(rows) < 5:
        warnings.append(f"資料筆數過少：{len(rows)} 筆（預期 ≥ 5）")

    # 檢查 3：至少有一筆 > 1.0
    max_change = max((float(r[2]) for r in rows), default=0.0)
    if max_change <= 1.0:
        warnings.append(f"無顯著成交量變化：最大值={max_change:.2f}（預期 > 1.0）")

    return (len(warnings) == 0, warnings)


def run_once(today_override: str | None = None) -> tuple[Path, list]:
    """執行一次完整抓取流程，回傳 (csv_path, rows)。

    若 resolve_dates() 回傳 (None, None)（TWSE 完全失敗），
    會寫一個空的 stock_screener_{date}_TWSE_FAILED.csv 作為識別，不拋例外。

    today_override：CLI --date 傳入時使用，直接抓指定日期、不 retry、不 fallback。
    """
    today, yesterday = resolve_dates(today_override)
    log.info("[日期] today=%s, yesterday=%s", today, yesterday)

    # TWSE 完全失敗：寫識別檔，溫和結束
    if today is None or yesterday is None:
        today_str = datetime.now().strftime("%Y%m%d")
        # 2026-10-13 重構：失敗識別檔統一進 output/screener/
        csv_path = WORKDIR / "output" / "screener" / f"stock_screener_{today_str}_TWSE_FAILED.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.write_text("代號,名稱,成交量相對變化,法人比\n", encoding="utf-8")
        log.warning("TWSE 失敗，寫入識別檔：%s", csv_path)
        return csv_path, []

    log.info("1. 取得今日三大法人買賣超 (T86)...")
    t86_data = fetch_json(f"https://www.twse.com.tw/fund/T86?response=json&date={today}&selectType=ALL")

    idx_code = t86_data['fields'].index("證券代號")
    idx_name = t86_data['fields'].index("證券名稱")
    idx_net_buy = t86_data['fields'].index("三大法人買賣超股數")

    stocks_t86 = []
    for row in t86_data['data']:
        code = row[idx_code]
        # 過濾：只抓 4 碼的「一般股票」，排除權證與 ETF
        if len(code) == 4 and code.isdigit():
            name = row[idx_name].strip()
            net_buy = int(row[idx_net_buy].replace(',', ''))
            stocks_t86.append({"code": code, "name": name, "net_buy": net_buy})

    # 依買超量遞減排序，取出前 100 名
    stocks_t86.sort(key=lambda x: x['net_buy'], reverse=True)
    top100_dict = {s['code']: s for s in stocks_t86[:100]}

    log.info("2. 取得今日全市場成交量 (MI_INDEX)...")
    mi_today = fetch_json(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={today}&type=ALL")

    today_price_table = next(t for t in mi_today['tables'] if "每日收盤行情" in t['title'])
    idx_code_mi = today_price_table['fields'].index("證券代號")
    idx_vol_mi = today_price_table['fields'].index("成交股數")

    # 填入今日成交量，並計算法人比
    for row in today_price_table['data']:
        code = row[idx_code_mi]
        if code in top100_dict:
            vol = int(row[idx_vol_mi].replace(',', ''))
            top100_dict[code]['vol_today'] = vol
            top100_dict[code]['ratio'] = top100_dict[code]['net_buy'] / vol if vol > 0 else 0

    # 依法人比遞減排序，篩選出前 20 名
    top100_list = [s for s in top100_dict.values() if 'ratio' in s]
    top100_list.sort(key=lambda x: x['ratio'], reverse=True)
    top20_dict = {s['code']: s for s in top100_list[:20]}

    log.info("3. 取得昨日全市場成交量 (MI_INDEX)... (等待 3 秒避免被擋)")
    time.sleep(3)
    mi_yesterday = fetch_json(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={yesterday}&type=ALL")

    yesterday_price_table = next(t for t in mi_yesterday['tables'] if "每日收盤行情" in t['title'])
    idx_code_y = yesterday_price_table['fields'].index("證券代號")
    idx_vol_y = yesterday_price_table['fields'].index("成交股數")

    # 填入昨日成交量，計算成交量相對變化
    for row in yesterday_price_table['data']:
        code = row[idx_code_y]
        if code in top20_dict:
            vol_y = int(row[idx_vol_y].replace(',', ''))
            top20_dict[code]['vol_yesterday'] = vol_y
            top20_dict[code]['vol_change'] = top20_dict[code]['vol_today'] / vol_y if vol_y > 0 else 0

    # 最終依成交量相對變化進行排序
    final_list = [s for s in top20_dict.values() if 'vol_change' in s]
    final_list.sort(key=lambda x: x['vol_change'], reverse=True)

    # 1. 寫 CSV（保留歷史，Shell 也會讀檔確認）
    import csv as csv_mod
    # 2026-10-13 重構：CSV 統一寫入 output/screener/
    csv_path = WORKDIR / "output" / "screener" / f"stock_screener_{today}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv_mod.writer(f)
        w.writerow(["代號", "名稱", "成交量相對變化", "法人比"])
        for s in final_list:
            w.writerow([s["code"], s["name"], f"{s['vol_change']:.2f}", f"{s['ratio']*100:.2f}%"])

    rows = [[s["code"], s["name"], f"{s['vol_change']:.2f}", f"{s['ratio']*100:.2f}%"]
            for s in final_list]
    return csv_path, rows


def find_fallback_csv() -> Path | None:
    """當今天 TWSE 抓不到時，找最近的「非失敗」CSV 作為 fallback。

    條件（全部要符合）：
      1. 檔名是 stock_screener_YYYYMMDD.csv（不是 _TWSE_FAILED 也不是 _TWSE_FAILED.csv）
      2. 檔名日期 ≤ 昨天（不能 fallback 到未來）
      3. 檔案內容是有效的（≥ 5 行，header 正確）
      4. 取「最新的」一個
    """
    candidates: list[tuple[str, Path]] = []
    # 2026-10-13 重構：fallback 也改讀 output/screener/
    screener_dir = WORKDIR / "output" / "screener"
    for path in screener_dir.glob("stock_screener_*.csv"):
        # 排除 _TWSE_FAILED 標記檔
        if "_TWSE_FAILED" in path.stem:
            continue
        # 從檔名抽出日期
        date_str = path.stem.replace("stock_screener_", "")
        if not (len(date_str) == 8 and date_str.isdigit()):
            continue
        # 排除未來日期
        today = datetime.now().strftime("%Y%m%d")
        if date_str >= today:
            continue
        # 內容檢查：至少 5 行（header + 4 筆）
        try:
            line_count = sum(1 for _ in open(path, encoding="utf-8"))
            if line_count < 5:
                continue
        except Exception:
            continue
        candidates.append((date_str, path))

    if not candidates:
        return None
    # 取最新日期
    candidates.sort(reverse=True)
    return candidates[0][1]


def main():
    parser = argparse.ArgumentParser(
        description="Stock Screener — TWSE 法人買賣超 + 成交量相對變化 Top 20"
    )
    parser.add_argument(
        "--date",
        metavar="YYYYMMDD",
        help="指定抓取日期（例：20261005）。不指定則用今天，行為等同原 pipeline 模式。",
    )
    args = parser.parse_args()
    is_cli = args.date is not None

    csv_path, rows = run_once(args.date)

    # CLI 模式：抓不到就直接報錯，不跑歷史 CSV fallback（語意：我要的就只是指定日）
    if is_cli and (not rows or "_TWSE_FAILED" in str(csv_path)):
        print(f"[驗證] 失敗 ❌ CLI 指定日期 {args.date} 抓不到 T86 資料")
        if "_TWSE_FAILED" in str(csv_path):
            print(f"  （{csv_path} 為識別檔）")
        sys.exit(1)

    # TWSE 失敗：嘗試 fallback 到昨天的 CSV，否則視為驗證失敗
    if "_TWSE_FAILED" in str(csv_path):
        fallback = find_fallback_csv()
        if fallback:
            fallback_date = fallback.stem.replace("stock_screener_", "")
            fallback_display = f"{fallback_date[:4]}-{fallback_date[4:6]}-{fallback_date[6:8]}"
            print(f"[驗證] 通過 ✅ (fallback: TWSE 今日失敗，使用 {fallback_display} 歷史資料)")
            print(f"[CSV] {fallback}")
            print("")
            print("⚠️ 今日 stock_screener 為 fallback 資料（昨日或更早），Yahoo CSV 仍請參考 Step 1。")
            print("  （stock_scraper.py 已寫入識別檔：TWSE_FAILED_*.csv）")
            return
        print("[驗證] 失敗 ❌ TWSE 抓取失敗且無 fallback CSV 可用")
        print(f"  （{csv_path} 為識別檔，無歷史資料）")
        return

    passed, warnings = validate_output(csv_path, csv_path.stem.replace("stock_screener_", ""), rows)

    if not passed:
        print(f"[驗證] 初次產出有 {len(warnings)} 個警告：{warnings}")
        print("[驗證] 觸發 1 次重跑...")
        time.sleep(60)  # 等 60 秒讓 TWSE / 網路狀況緩解
        csv_path, rows = run_once()
        passed, warnings = validate_output(csv_path, csv_path.stem.replace("stock_screener_", ""), rows)

    if passed:
        print(f"[驗證] 通過 ✅")
    else:
        # 重跑仍失敗：明確警告但不擋流程（pipeline 可繼續往下跑 Step 3）
        print(f"[驗證] 重跑後仍有 {len(warnings)} 個警告：")
        for w in warnings:
            print(f"  :warning: {w}")

    # 2. 精簡 stdout（給 cron shell 抓的格式：一行一檔，沿用原本格式）
    if rows:
        print(f"[CSV] {csv_path}")
        print()
        print(f"\U0001f4ca Stock Screener （{len(rows)} 檔）")
        print()
        for s in rows:
            print(f"- {s[0]} {s[1]}, {s[2]}, {s[3]}")
    else:
        # TWSE 完全失敗：告知 CSV 位置（pipeline 仍會送到 Discord）
        print(f"[CSV] {csv_path}")
        print("⚠️ TWSE 今日完全無法抓取，Yahoo CSV 仍有資料，請參考 Step 1 結果。")
        print("  （stock_scraper.py 已寫入識別檔：TWSE_FAILED_{date}.marker）")
        # 這不是錯誤，讓 shell 收到 exit 0
        sys.exit(0)

if __name__ == "__main__":
    main()
