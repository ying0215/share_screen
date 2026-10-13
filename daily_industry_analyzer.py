#!/usr/bin/env python3
"""
每日產業熱度分析 + MA5/MA20 平滑

讀取：
  - yahoo_rank_intersection_YYYYMMDD.csv（29 天）
  - stock_screener_YYYYMMDD.csv（3 天，資料累積中）
  - stock_industry_cache.csv（4323 筆 FinMind TaiwanStockInfo）

產出：
  - 每日產業分布時間序列（Yahoo + 法人）
  - MA5 / MA20 平滑（資料不足會明確標註）
  - 近期前 5 大強勢產業（依 MA5 排名）

注意：
  - 法人（Screener）只有 3 天歷史，MA 算不出來，會顯示「資料累積中」
  - Yahoo 有 29 天歷史，MA5/MA20 都可算
"""

import csv
from collections import defaultdict
from pathlib import Path

WORKDIR = Path("/home/ubuntu/shared_from_jack")
YAHOO_DIR = WORKDIR / "output" / "yahoo"
SCREENER_DIR = WORKDIR / "output" / "screener"
INDUSTRY_DIR = WORKDIR / "output" / "industry"
YAHOO_GLOB = "yahoo_rank_intersection_*.csv"
SCREENER_GLOB = "stock_screener_*.csv"
INDUSTRY_CACHE = "stock_industry_cache.csv"

# 查不到產業（或 FinMind 欄位空）時的 fallback bucket，避免污染真實產業排名
UNKNOWN_INDUSTRY = "其他"


def load_industry_lookup() -> dict[str, tuple[str, str]]:
    """{代號: (名稱, 產業)}"""
    if not (WORKDIR / INDUSTRY_CACHE).exists():
        raise SystemExit(f"❌ 找不到 {INDUSTRY_CACHE}，請先跑 stock_industry.py --refresh")
    lookup = {}
    with open(WORKDIR / INDUSTRY_CACHE, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row["stock_id"].strip()
            if not code:
                continue
            # 上市 (twse) 優先；若只有上櫃 (tpex) 也收
            if code not in lookup or row["type"] == "twse":
                lookup[code] = (row["stock_name"], row["industry_category"] or UNKNOWN_INDUSTRY)
    return lookup


def load_yahoo_daily() -> dict[str, list[str]]:
    """{日期(YYYYMMDD): [代號...]}"""
    daily = {}
    for path in sorted(YAHOO_DIR.glob(YAHOO_GLOB)):
        date = path.stem.replace("yahoo_rank_intersection_", "")
        codes = []
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                code = row.get("股票代碼", "").strip()
                if code:
                    codes.append(code)
        if codes:
            daily[date] = codes
    return daily


def load_screener_daily() -> dict[str, list[str]]:
    """{日期(YYYYMMDD): [代號...]}"""
    daily = {}
    for path in sorted(SCREENER_DIR.glob(SCREENER_GLOB)):
        date = path.stem.replace("stock_screener_", "")
        codes = []
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = row.get("代號", "").strip()
                if code:
                    codes.append(code)
        if codes:
            daily[date] = codes
    return daily


def daily_industry_counts(daily_codes: dict[str, list[str]], lookup: dict) -> dict[str, dict[str, int]]:
    """{日期: {產業: 次數}} — 查不到產業的代號歸「其他」"""
    out = {}
    for date, codes in daily_codes.items():
        counts: dict[str, int] = defaultdict(int)
        for code in codes:
            _, industry = lookup.get(code, ("", UNKNOWN_INDUSTRY))
            counts[industry] += 1
        out[date] = dict(counts)
    return out


def merged_daily_industry_counts(
    yahoo_daily_codes: dict[str, list[str]],
    screener_daily_codes: dict[str, list[str]],
    lookup: dict,
) -> dict[str, dict[str, int]]:
    """
    Union 去重：每天把 Yahoo 交集 ∪ Screener 名單合併成單一代號集合，
    每個代號只計 1 次（不論兩個來源都出現還是只有一個）。

    「同產業」計數 = 該日 Union 去重後的代號集合中，屬於該產業的代號數。
    所以若 Yahoo 跟 Screener 都有同一個半導體股，只算 1 次半導體。

    若某來源那天沒資料（Screener 只有 3 天），Union 就是另一個來源，
    不會因為缺資料而扭曲。
    """
    all_dates = sorted(set(yahoo_daily_codes.keys()) | set(screener_daily_codes.keys()))
    out = {}
    for d in all_dates:
        # Union 去重
        union_codes = set(yahoo_daily_codes.get(d, [])) | set(screener_daily_codes.get(d, []))
        counts: dict[str, int] = defaultdict(int)
        for code in union_codes:
            _, industry = lookup.get(code, ("", UNKNOWN_INDUSTRY))
            counts[industry] += 1
        out[d] = dict(counts)
    return out


def moving_average(series: list[float], window: int) -> list[float | None]:
    """
    MA：window 之前的回傳 None（資料不足）。

    邊界處理：
      - 前 window-1 個位置：資料不足，回傳 None
      - 中間如果值是 None（資料尚未開始）：該位置保留 None，**不**混入平均
      - 中間純 0（產業有資料但當天 0 檔）：納入平均（這是合理的「該產業那天沒熱」訊號）
    """
    result = []
    for i in range(len(series)):
        if i + 1 < window:
            result.append(None)
        else:
            window_slice = series[i - window + 1: i + 1]
            # 若 window 內有任何 None（資料尚未開始），整個 MA 標 None
            if any(v is None for v in window_slice):
                result.append(None)
            else:
                result.append(sum(window_slice) / window)
    return result


def industry_series_with_history(
    daily_counts: dict[str, dict[str, int]],
    industry: str,
    sorted_dates: list[str],
) -> list[float | None]:
    """
    對單一產業產生時間序列：
      - 該產業第一次出現的日期之前 → None（資料尚未開始）
      - 第一次出現之後（包含當天）→ 該日實際次數（可能是 0）
    """
    first_seen_idx = None
    for i, d in enumerate(sorted_dates):
        if industry in daily_counts.get(d, {}):
            first_seen_idx = i
            break
    series: list[float | None] = []
    for i, d in enumerate(sorted_dates):
        if first_seen_idx is None or i < first_seen_idx:
            series.append(None)
        else:
            series.append(float(daily_counts[d].get(industry, 0)))
    return series


def fmt_date(d: str) -> str:
    """YYYYMMDD -> YYYY-MM-DD"""
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def signal_label(latest_count: int, ma5: float | None, ma20: float | None) -> str:
    """
    判讀訊號：優先看 MA5/MA20 方向，再看今日是否 0。

    邏輯順序很重要：
      1. MA5 算不出來 → 資料不足
      2. MA20 算不出來（但 MA5 有）→ MA20 不足
      3. MA5 > MA20 → 短線轉強；今日 0 額外加註
      4. MA5 < MA20 → 短線轉弱；今日 0 額外加註
    """
    if ma5 is None:
        return "⚠️ 資料不足"
    if ma20 is None:
        return "⚠️ MA20 不足"
    if ma5 > ma20:
        return "✅ MA5 > MA20" if latest_count > 0 else "⚠️ 今日 0（但 MA5 > MA20）"
    if ma5 < ma20:
        return "⚠️ MA5 < MA20" if latest_count > 0 else "⚠️ 今日 0 + MA5 < MA20"
    return "—"


def print_top5_markdown(top5, ma20_map, daily_counts, sorted_dates) -> None:
    """印 Discord-friendly markdown 表格（含 MA5、MA20、訊號）"""
    print(f"\U0001f4ca 產業熱度分析")
    print()
    print(f"近期前 5 大強勢產業（依最新一天 MA5 排名，{fmt_date(sorted_dates[-1])}）")
    print()
    print("| 排名 | 產業 | 今日 (Union) | MA5 | MA20 | 訊號 |")
    print("|------|------|--------------|-----|------|------|")
    for rank, (ind, ma5_score) in enumerate(top5, 1):
        latest_count = daily_counts[sorted_dates[-1]].get(ind, 0)
        ma20_score = ma20_map[sorted_dates[-1]][ind]
        ma5_str = f"{ma5_score:.2f}" if ma5_score is not None else "—"
        ma20_str = f"{ma20_score:.2f}" if ma20_score is not None else "—"
        sig = signal_label(latest_count, ma5_score, ma20_score)
        print(f"| {rank} | {ind} | {latest_count} 檔 | {ma5_str} | {ma20_str} | {sig} |")


def top_n_strongest(daily_counts: dict[str, dict[str, int]], ma5_map: dict[str, dict[str, float | None]], top_n: int = 5) -> list[tuple[str, float]]:
    """
    取「最新一天 MA5」分數最高的前 N 個產業。
    回傳 [(產業, MA5 分數)]，MA5 是 None 的產業排除。
    """
    if not ma5_map:
        return []
    latest_date = max(ma5_map.keys())
    candidates = [
        (ind, ma) for ind, ma in ma5_map[latest_date].items()
        if ma is not None
    ]
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:top_n]


def analyze_source(source_name: str, daily_counts: dict[str, dict[str, int]], n_days: int, discord_only: bool = False) -> None:
    if not discord_only:
        print(f"\n{'=' * 70}")
        print(f"📊 {source_name} 產業熱度分析")
        print(f"{'=' * 70}")
        print(f"資料天數：{n_days} 天（{fmt_date(min(daily_counts.keys()))} ~ {fmt_date(max(daily_counts.keys()))}）")

    if n_days < 5 and not discord_only:
        print(f"⚠️  資料不足，MA5 算不出來（需 ≥ 5 天）")
    if n_days < 20 and not discord_only:
        print(f"⚠️  資料不足，MA20 算不出來（需 ≥ 20 天）")

    # 收集所有出現過的產業
    all_industries = sorted({ind for counts in daily_counts.values() for ind in counts.keys()})

    # 對每個產業算每日次數序列（沒出現 = 0；第一次出現前 = None）
    industry_series: dict[str, list[float | None]] = {}
    sorted_dates = sorted(daily_counts.keys())
    for ind in all_industries:
        industry_series[ind] = industry_series_with_history(daily_counts, ind, sorted_dates)

    # 算 MA5 / MA20
    ma5_map: dict[str, dict[str, float | None]] = {d: {} for d in sorted_dates}
    ma20_map: dict[str, dict[str, float | None]] = {d: {} for d in sorted_dates}
    for ind in all_industries:
        # 移除 series 前段的 None（不然 MA 算出來也會是 None）
        series = industry_series[ind]
        ma5 = moving_average(series, 5)
        ma20 = moving_average(series, 20)
        for i, d in enumerate(sorted_dates):
            ma5_map[d][ind] = ma5[i]
            ma20_map[d][ind] = ma20[i]

    # 列印熱度 Top 5（依最新一天 MA5）
    top5 = top_n_strongest(daily_counts, ma5_map, 5)
    if discord_only:
        # Discord 模式：印 markdown 表格
        if not top5:
            print(f"\n⚠️ _資料不足，無法排名（MA5 需 ≥ 5 天）_")
        else:
            print_top5_markdown(top5, ma20_map, daily_counts, sorted_dates)
    else:
        # 一般模式：維持原本的純文字輸出
        print(f"\n🔥 近期前 5 大強勢產業（依最新一天 MA5 排名）：")
        if not top5:
            print(f"  _資料不足，無法排名（MA5 需 ≥ 5 天）_")
        else:
            for rank, (ind, ma5_score) in enumerate(top5, 1):
                latest_count = daily_counts[sorted_dates[-1]].get(ind, 0)
                print(f"  {rank}. {ind:30s}  MA5={ma5_score:.2f}  (今日 {latest_count} 檔)")

        # 列印時間序列表格（Top 5 產業）
        if top5:
            print(f"\n📈 Top 5 產業每日次數（含 MA5 / MA20）：")
            print(f"  {'日期':<12s}", end="")
            for ind, _ in top5:
                short = ind[:8] + ("…" if len(ind) > 8 else "")
                print(f"  {short:>10s}", end="")
            print()
            for i, d in enumerate(sorted_dates[-15:]):  # 最後 15 天
                print(f"  {fmt_date(d):<12s}", end="")
                for ind, _ in top5:
                    c = daily_counts[d].get(ind, 0)
                    m5 = ma5_map[d][ind]
                    m20 = ma20_map[d][ind]
                    cell = f"{c}"
                    if m5 is not None:
                        cell += f"·{m5:.1f}"
                    if m20 is not None:
                        cell += f"·{m20:.1f}"
                    print(f"  {cell:>10s}", end="")
                print()
            print(f"\n  (格式：當日次數·MA5·MA20；MA5/MA20 為 None 表示資料不足)")

    # 寫一份 CSV 保留歷史（之後 cron 累積用）
    if discord_only:
        return  # pipeline 模式不需要寫 CSV

    # 2026-10-13 重構：CSV 統一寫入 output/industry/
    out_csv = INDUSTRY_DIR / f"daily_industry_{source_name}.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["日期", "產業", "當日次數", "MA5", "MA20"])
        for d in sorted_dates:
            for ind in all_industries:
                w.writerow([
                    fmt_date(d),
                    ind,
                    daily_counts[d].get(ind, 0),
                    "" if ma5_map[d][ind] is None else f"{ma5_map[d][ind]:.2f}",
                    "" if ma20_map[d][ind] is None else f"{ma20_map[d][ind]:.2f}",
                ])
    print(f"\n📁 序列資料已存：{out_csv}")


def main():
    import sys
    discord_only = "--discord-top5" in sys.argv

    if not discord_only:
        print(f"🔍 讀取產業對照表（stock_industry_cache.csv）...")
    lookup = load_industry_lookup()
    if not discord_only:
        print(f"   共 {len(lookup)} 筆")

        print(f"\n🔍 讀取 Yahoo 每日交集...")
    yahoo_daily = load_yahoo_daily()
    if not discord_only:
        print(f"   共 {len(yahoo_daily)} 天")

    if not discord_only:
        print(f"🔍 讀取 Screener（法人）每日...")
    screener_daily = load_screener_daily()
    if not discord_only:
        print(f"   共 {len(screener_daily)} 天")

    if discord_only:
        # Discord 模式：只算合併 + 只印 Top 5 markdown，不寫 CSV
        merged_counts = merged_daily_industry_counts(yahoo_daily, screener_daily, lookup)
        analyze_source("merged_合併", merged_counts, len(merged_counts), discord_only=True)
        return

    # Yahoo 分析
    yahoo_counts = daily_industry_counts(yahoo_daily, lookup)
    analyze_source("yahoo_交集", yahoo_counts, len(yahoo_daily))

    # Screener 分析
    screener_counts = daily_industry_counts(screener_daily, lookup)
    analyze_source("screener_法人", screener_counts, len(screener_daily))

    # 合併分析（Yahoo + 法人，Union 去重，每代號只算 1 次）
    merged_counts = merged_daily_industry_counts(yahoo_daily, screener_daily, lookup)
    analyze_source("merged_合併", merged_counts, len(merged_counts))

    # 今日對照
    if yahoo_daily and screener_daily:
        latest_yahoo = max(yahoo_daily.keys())
        latest_screener = max(screener_daily.keys())
        print(f"\n{'=' * 70}")
        print(f"📅 今日對照（Yahoo {fmt_date(latest_yahoo)} / 法人 {fmt_date(latest_screener)}）")
        print(f"{'=' * 70}")
        yahoo_today = yahoo_counts[latest_yahoo]
        screener_today = screener_counts[latest_screener]
        all_ind = sorted(set(yahoo_today) | set(screener_today))
        print(f"  {'產業':<30s}  {'Yahoo':>6s}  {'法人':>6s}")
        for ind in all_ind:
            print(f"  {ind:<30s}  {yahoo_today.get(ind, 0):>6d}  {screener_today.get(ind, 0):>6d}")


if __name__ == "__main__":
    main()