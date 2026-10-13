"""
股票重複性比對 — Stock Repeat Detector

讀取兩種來源的 CSV，產出三個區塊：
  A. Yahoo 排行榜「最近 N 天內出現 ≥ M 次」（預設 5 天內 ≥ 3 次）
  B. Yahoo ∩ Stock Screener 當天重疊股
  C. Stock Screener「最近 N 天內出現 ≥ M 次」（跟 A 同一邏輯，套到法人買超榜）

區塊全空 → 輸出 0 檔訊息（正常，不視為錯誤）。
注意：C 區塊需要 Stock Screener CSV 累計 ≥ M 天才會有結果，
歷史資料不足時會輸出「資料不足」提示而非錯誤。

為什麼用「N 天內 M 次」而不是「連續 M 天」：
  連續天數會被週末、颱風假、cron 失敗輕易打斷；N 天內 M 次更貼近
  「法人是否持續買超」的交易直覺，也容忍單日缺席。
"""

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

WORKDIR = Path("/home/ubuntu/shared_from_jack")
YAHOO_GLOB = "yahoo_rank_intersection_*.csv"
SCREENER_GLOB = "stock_screener_*.csv"
DEFAULT_WINDOW = 5            # 觀察區間天數
DEFAULT_THRESHOLD = 3         # 區間內上榜次數門檻（條件 1）
DEFAULT_CONSECUTIVE_DAYS = 2  # 連續上榜天數門檻（條件 2）


def load_yahoo_csvs() -> list[tuple[str, set[str], dict[str, str]]]:
    """
    從 Yahoo CSV 檔名解析日期，讀出 (date_str, symbols_set, name_map)。
    按日期由新到舊排序。
    """
    out = []
    for path in sorted(WORKDIR.glob(YAHOO_GLOB), reverse=True):
        date_str = path.stem.replace("yahoo_rank_intersection_", "")
        symbols: set[str] = set()
        name_map: dict[str, str] = {}
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row["股票代碼"].strip()
                name = row["股票名稱"].strip()
                if code:
                    symbols.add(code)
                    if name:  # 空名稱不要覆蓋已有的（避免覆蓋有效名稱為空字串）
                        name_map[code] = name
        if symbols:
            out.append((date_str, symbols, name_map))
    return out


def load_screener_csvs() -> list[tuple[str, set[str], dict[str, str]]]:
    """同上，但讀 stock_screener_*.csv（欄位: 代號, 名稱）"""
    out = []
    for path in sorted(WORKDIR.glob(SCREENER_GLOB), reverse=True):
        date_str = path.stem.replace("stock_screener_", "")
        symbols: set[str] = set()
        name_map: dict[str, str] = {}
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row["代號"].strip()
                name = row["名稱"].strip()
                if code:
                    symbols.add(code)
                    if name:  # 空名稱不要覆蓋已有的
                        name_map[code] = name
        if symbols:
            out.append((date_str, symbols, name_map))
    return out


def find_recent_occurrences(
    source: list[tuple[str, set, dict]],
    window: int = DEFAULT_WINDOW,
    threshold: int = DEFAULT_THRESHOLD,
    consecutive_days: int = DEFAULT_CONSECUTIVE_DAYS,
) -> tuple[list[tuple[str, str, list[str], str]], str]:
    """
    找出「最近 window 個交易日」內，符合下列任一條件的股票：
      條件 1（門檻型）：區間內出現 ≥ threshold 次
      條件 2（連續型）：連續上榜 ≥ consecutive_days 天
    兩個條件是 OR 邏輯——命中任一即納入。

    「連續」的定義：date_str 已按新→舊排序，所以「連續 N 天」=
    dates 列表（由新到舊）的前 N 個日期，兩兩相鄰且無 gap。
    比方說 window=5, 命中 [20261001, 20260930, 20260929, 20260928]
    → 前 4 個連續 → 連續 4 天達標。
    區間內出現次數不足 threshold 但前 N 天連續，仍會被納入。

    回傳 (results, source_latest_date_str)：
      results = [(代號, 名稱, [出現的日期列表，由新到舊], 命中原因)]
        命中原因 ∈ {"threshold", "consecutive", "both"}
      排序：最新出現日期為主（同日期再按次數遞減），越接近今天越優先
    """
    if not source:
        return [], ""

    # 只取最新 window 天（檔案已按新到舊排序）
    window_data = source[:window]
    latest_date = window_data[0][0]

    # 名稱 map 採 window 內所有天的聯集，最新一天優先（後寫入覆蓋舊值）
    latest_names: dict[str, str] = {}
    for date_str, symbols, name_map in window_data:  # 新→舊 順序
        for sym, n in name_map.items():
            latest_names[sym] = n

    # 統計每檔股票在 window 內的出現日期（由新到舊）
    occ_map: dict[str, list[str]] = {}
    for date_str, symbols, _ in window_data:
        for sym in symbols:
            occ_map.setdefault(sym, []).append(date_str)

    def consecutive_streak(dates: list[str]) -> int:
        """由新到舊的日期列表，計算前綴連續天數。"""
        if not dates:
            return 0
        streak = 1
        for i in range(1, len(dates)):
            d_prev = datetime.strptime(dates[i - 1], "%Y%m%d")
            d_curr = datetime.strptime(dates[i], "%Y%m%d")
            if (d_prev - d_curr).days == 1:
                streak += 1
            else:
                break
        return streak

    results = []
    for sym, dates in occ_map.items():
        times = len(dates)
        streak = consecutive_streak(dates)
        hit_threshold = times >= threshold
        hit_consecutive = streak >= consecutive_days
        if not (hit_threshold or hit_consecutive):
            continue
        if hit_threshold and hit_consecutive:
            reason = "both"
        elif hit_threshold:
            reason = "threshold"
        else:
            reason = "consecutive"
        results.append((sym, latest_names.get(sym, ""), dates, reason))

    # 排序：最新日期為主（同日期內按次數遞減；次數同按代號排序）
    #   key = (-最新日期, -次數, 代號) — 用負號讓大的排前面
    results.sort(key=lambda x: (-int(x[2][0]), -len(x[2]), x[0]))
    return results, latest_date


def find_yahoo_screener_overlap(yahoo, screener) -> tuple[list[tuple[str, str, str]], str, bool]:
    """
    B 區塊：最新一天 Yahoo ∩ Stock Screener 重疊股（只看當天，不看歷史）。

    因為 Stock Screener 的 CSV 日期是「資料日期」（抓當天會 fallback 到前一天），
    所以要比對的是「兩個檔案名裡的日期相同」的那天。本函式只看 yahoo 最新的那
    個日期，若 screener 沒有同一天資料就直接回傳「今天無重疊」，不再 fallback
    到歷史天的配對。

    回傳 (results, latest_date, has_match)：
      results = [(代號, 名稱, 日期)]
      latest_date = 最新 yahoo 的日期
      has_match = 最新一天是否有 yahoo 跟 screener 同時存在的配對
    """
    if not yahoo or not screener:
        return [], yahoo[0][0] if yahoo else "", False

    y_latest_date, y_latest_symbols, y_latest_names = yahoo[0]
    s_latest_date, s_latest_symbols, s_latest_names = screener[0]

    # 只看最新一天：yahoo 跟 screener 都要有「最新一天」的 CSV 才能配對
    if y_latest_date != s_latest_date:
        return [], y_latest_date, False

    overlap = y_latest_symbols & s_latest_symbols
    out = []
    for sym in sorted(overlap):
        name = y_latest_names.get(sym) or s_latest_names.get(sym, "")
        out.append((sym, name, y_latest_date))
    return out, y_latest_date, True


def main():
    # CLI 用法：stock_repeat_compare.py [window] [threshold] [consecutive_days]
    # 例：python3 stock_repeat_compare.py 5 3 2
    #   → 最近 5 天內出現 ≥3 次 OR 連續 2 天上榜
    window = DEFAULT_WINDOW
    threshold = DEFAULT_THRESHOLD
    consecutive_days = DEFAULT_CONSECUTIVE_DAYS
    if len(sys.argv) > 1:
        try:
            window = int(sys.argv[1])
        except ValueError:
            pass
    if len(sys.argv) > 2:
        try:
            threshold = int(sys.argv[2])
        except ValueError:
            pass
    if len(sys.argv) > 3:
        try:
            consecutive_days = int(sys.argv[3])
        except ValueError:
            pass

    yahoo = load_yahoo_csvs()
    screener = load_screener_csvs()

    if not yahoo:
        print(":rotating_light: **找不到任何 Yahoo 排行榜 CSV**")
        sys.exit(1)

    today = yahoo[0][0]  # 最新 Yahoo 的日期
    today_display = f"{today[:4]}-{today[4:6]}-{today[6:8]}"

    # 命中原因的中文短標（給 Discord 顯示）
    reason_label = {
        "threshold": f"區間內≥{threshold}次",
        "consecutive": f"連續{consecutive_days}天",
        "both": f"區間內≥{threshold}次+連續{consecutive_days}天",
    }

    print(f"\U0001f4ca Repeat Compare — {today_display}")
    print(f"（觀察區間：最近 {window} 個交易日、門檻 ≥ {threshold} 次 或 連續 {consecutive_days} 天）")
    print(f"（資料範圍：Yahoo 最近 {len(yahoo)} 天、Screener 最近 {len(screener)} 天）")
    print()

    # A 區塊：Yahoo 排行榜最近 N 天內上榜 ≥ M 次 OR 連續 N 天
    hits_yahoo, _ = find_recent_occurrences(yahoo, window, threshold, consecutive_days)
    print(f"A. Yahoo 排行榜最近 {window} 天內上榜 ≥ {threshold} 次 或 連續 {consecutive_days} 天")
    if len(yahoo) < threshold and len(yahoo) < consecutive_days:
        print(f"  Yahoo 資料只有 {len(yahoo)} 天，少於門檻 {threshold}（條件 1）也少於連續 {consecutive_days} 天（條件 2）。")
    elif not hits_yahoo:
        print(f"  最近 {window} 天沒有任何股票上榜 ≥ {threshold} 次，也沒有連續 {consecutive_days} 天上榜。")
    else:
        print(f"  共 {len(hits_yahoo)} 檔命中（{window} 天內 ≥ {threshold} 次 或 連續 {consecutive_days} 天）：")
        for sym, name, dates, reason in hits_yahoo:
            times = len(dates)
            date_range = f"{dates[-1][:4]}-{dates[-1][4:6]}-{dates[-1][6:8]} ~ {dates[0][:4]}-{dates[0][4:6]}-{dates[0][6:8]}"
            print(f"- {sym} {name} — {times} 次（{date_range}） ({reason_label[reason]})")
    print()

    # B 區塊：當天 Yahoo ∩ Stock Screener 重疊（只看最新一天，不看歷史 fallback）
    overlap, b_latest_date, b_has_match = find_yahoo_screener_overlap(yahoo, screener)
    b_latest_display = f"{b_latest_date[:4]}-{b_latest_date[4:6]}-{b_latest_date[6:8]}" if b_latest_date else "—"
    print("B. Yahoo 熱門 ∩ Stock Screener 法人同步吃貨（僅看最新一天）")
    if not screener:
        print("  Stock Screener CSV 還沒產出（cron 15:30 還沒跑過）。")
    elif not b_has_match:
        # screener 最新一天日期跟 yahoo 對不上（兩邊還沒同步到同一天）
        print(f"  今天（{b_latest_display}）Yahoo 與 Screener 沒有同一天的 CSV 可比對，等下一次 cron 同步。")
    elif not overlap:
        print(f"  今天（{b_latest_display}）Yahoo 熱門股沒有跟 Stock Screener 法人買超重疊。")
    else:
        print(f"  共 {len(overlap)} 檔同時出現在今天的兩個榜（{b_latest_display}）：")
        for sym, name, date in overlap:
            print(f"- {sym} {name}")
    print()

    # C 區塊：Stock Screener 最近 N 天內上榜 ≥ M 次 OR 連續 N 天（跟 A 同演算法）
    hits_screener, screener_latest = find_recent_occurrences(screener, window, threshold, consecutive_days)
    print(f"C. Stock Screener 最近 {window} 天內上榜 ≥ {threshold} 次 或 連續 {consecutive_days} 天")
    if not screener:
        print("  Stock Screener CSV 還沒產出，無資料可比。")
    elif len(screener) < threshold and len(screener) < consecutive_days:
        # 資料天數不足門檻（例：Screener 9/16 才有第一份，9/17 只累積 2 天）
        print(f"  目前只有 {len(screener)} 天 Screener 資料，需累計到 ≥{threshold} 天或連續 {consecutive_days} 天才會出現結果。")
    elif not hits_screener:
        print(f"  最近 {window} 天沒有任何股票在 Stock Screener 上榜 ≥ {threshold} 次，也沒有連續 {consecutive_days} 天上榜。")
    else:
        print(f"  共 {len(hits_screener)} 檔命中（資料範圍至 {screener_latest}）：")
        for sym, name, dates, reason in hits_screener:
            times = len(dates)
            date_range = f"{dates[-1][:4]}-{dates[-1][4:6]}-{dates[-1][6:8]} ~ {dates[0][:4]}-{dates[0][4:6]}-{dates[0][6:8]}"
            print(f"- {sym} {name} — {times} 次（{date_range}） ({reason_label[reason]})")
    print()

    # 寫一份 CSV 保留歷史（向後相容：欄位不變；命中原因附加在備註尾端）
    out_csv = WORKDIR / f"repeat_compare_{today}.csv"
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["區塊", "代號", "名稱", "備註"])
        for sym, name, dates, reason in hits_yahoo:
            w.writerow([f"A_{window}天內≥{threshold}次_OR_連續{consecutive_days}天_Yahoo", sym, name, f"{len(dates)}次 ({reason_label[reason]})"])
        for sym, name, date in overlap:
            w.writerow(["B_雙榜重疊", sym, name, f"{date[:4]}-{date[4:6]}-{date[6:8]}"])
        for sym, name, dates, reason in hits_screener:
            w.writerow([f"C_{window}天內≥{threshold}次_OR_連續{consecutive_days}天_Screener", sym, name, f"{len(dates)}次 ({reason_label[reason]})"])

    print(f"📁 CSV: {out_csv}")


if __name__ == "__main__":
    main()
