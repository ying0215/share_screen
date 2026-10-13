#!/usr/bin/env python3
"""
reader_yahoo_csv.py — 讀取 yahoo_rank_intersection_*.csv 並印出 Discord 區塊

設計目的：
- Stock Daily Pipeline 預檢已抓過 Yahoo 榜並寫入 yahoo_rank_intersection_${TODAY}.csv
- Step 1 不再重抓，直接讀 CSV 印 stdout（避免一天打兩次 Yahoo API）
- stdout 格式刻意模仿 scraper_yahoo_rank.py（保留「同時出現在...的股票」標題與 ljust(10) 對齊），
  但「正在爬取」改成「正在讀取」、「成功取得 N 檔」合併、「資料已儲存」改成「資料來源」
  → Discord 讀者看得出這次是讀檔不是爬網頁

用法：
    # 自動找今日榜（看 WORKDIR 環境變數，再 fallback 到 cwd）
    python3 reader_yahoo_csv.py

    # 自訂 CSV 路徑（debug / 重跑昨天 / 撈歷史）
    python3 reader_yahoo_csv.py --csv /path/to/yahoo_rank_intersection_20260929.csv

Exit code：
    0 = 成功（含 0 筆交集的正常情況）
    2 = 找不到 CSV
    3 = CSV 是空檔（0 bytes）
    4 = CSV header 格式錯誤
"""
import argparse
import csv
import os
import sys
from pathlib import Path

EXPECTED_HEADER = ["股票代碼", "股票名稱"]


def find_default_csv() -> Path:
    """從 WORKDIR 環境變數找今日榜，找不到就直接報錯（不 fallback cwd）。

    為什麼不 fallback：
    - WORKDIR 是使用者明確指定的專案目錄，cwd 可能跑其他任務
    - 靜默 fallback 會誤導：debug 時以為 WORKDIR 生效了，實際讀到 cwd 別人家的檔
    - 找不到就直接 exit 2，讓問題浮上來
    """
    from datetime import datetime
    today_str = datetime.now().strftime("%Y%m%d")
    filename = f"yahoo_rank_intersection_{today_str}.csv"
    workdir = os.environ.get("WORKDIR")
    if not workdir:
        err_and_exit(2, "找不到今日 CSV: WORKDIR 環境變數未設定，且沒指定 --csv")
    # 2026-10-13 重構：Yahoo CSV 統一進 output/yahoo/
    return Path(workdir) / "output" / "yahoo" / filename


def err_and_exit(code: int, msg: str) -> None:
    """印錯誤訊息到 stderr 並 exit。stderr 內容會被 run_step 的 cat 包進 Discord。"""
    print(f":rotating_light: {msg}", file=sys.stderr)
    sys.exit(code)


def main() -> None:
    parser = argparse.ArgumentParser(description="讀取 Yahoo 排行榜 CSV 並印 Discord 區塊")
    parser.add_argument("--csv", type=str, default=None,
                        help="自訂 CSV 路徑（預設：WORKDIR 或 cwd 下的 yahoo_rank_intersection_YYYYMMDD.csv）")
    args = parser.parse_args()

    # 1. 決定 CSV 路徑
    csv_path = Path(args.csv) if args.csv else find_default_csv()

    # 2. 檢查 CSV 存在
    if not csv_path.exists():
        err_and_exit(2, f"找不到 CSV: {csv_path}")

    # 3. 檢查 CSV 非空
    if csv_path.stat().st_size == 0:
        err_and_exit(3, f"CSV 是空的: {csv_path}")

    # 4. 讀 CSV
    # Yahoo CSV 用 utf-8-sig（有 BOM），跟 scraper_yahoo_rank.py 寫入時一致
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            err_and_exit(3, f"CSV 沒有 header: {csv_path}")

        # 5. 驗證 header
        if header != EXPECTED_HEADER:
            err_and_exit(4, f"CSV header 錯誤: {header}（預期 {EXPECTED_HEADER}）")

        rows = list(reader)

    # 6. 印 stdout
    # 標題層級統一：📊 {區塊名稱} {（括號資訊）}
    # 注意：run_step 已經印「▶️ Step: yahoo」標頭跟分隔線，
    #       reader 不再重複印標頭，只負責「📊 Yahoo 榜 ...」內容區塊開頭
    count = len(rows)
    print(f"\U0001f4ca Yahoo 榜 \uff08讀取 {count} \u6a94\u80a1\u7968\uff09")
    if count == 0:
        print()
        print("目前沒有同時上榜的股票。")
    else:
        print()
        print("=" * 50)
        print("同時出現在「成交量排行」與「漲幅排行」的股票")
        print("(依 Yahoo 成交量榜順序排序 — 榜首為當日最大量)")
        print("=" * 50)
        for symbol, name in rows:
            print(f"- {symbol.ljust(10)} {name}")

    print()
    print(f"資料來源: {csv_path.name}")

    sys.exit(0)


if __name__ == "__main__":
    main()