import requests
from bs4 import BeautifulSoup
import re

def get_yahoo_rank_stocks(url):
    """
    爬取 Yahoo 股市排行榜的股票代號與名稱
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    # 30 秒 timeout 防止 Yahoo 慢回應時永久 hang（跟 stock_scraper.py 的策略對齊）
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # 找到每一列股票的 <li> 標籤
    list_items = soup.find_all('li', class_=re.compile(r'List\(n\)'))
    
    stocks = {}
    for li in list_items:
        # 尋找股票名稱，特徵通常是包含了 Lh(20px) 與 Fw(600) 這些 class
        name_elem = li.find('div', class_=re.compile(r'Lh\(20px\).*Fw\(600\)'))
        # 尋找股票代號，特徵通常是包含了 C(#979ba7) 這個 class
        symbol_elem = li.find('span', class_=re.compile(r'C\(#979ba7\)'))
        
        if name_elem and symbol_elem:
            name = name_elem.text.strip()
            # 移除後方的 .TW 或 .TWO，只保留數字代碼
            symbol = symbol_elem.text.strip().split('.')[0].strip()
            # 防禦：Yahoo 改格式時空代碼不要進字典
            if symbol:
                stocks[symbol] = name
            
    return stocks

import csv
from datetime import datetime

def main():
    url_volume = "https://tw.stock.yahoo.com/rank/volume/"
    url_change_up = "https://tw.stock.yahoo.com/rank/change-up/"
    
    print("正在爬取「成交量排行」...")
    try:
        volume_stocks = get_yahoo_rank_stocks(url_volume)
        print(f"成功取得 {len(volume_stocks)} 檔股票。")
    except Exception as e:
        print(f"爬取成交量排行失敗: {e}")
        return
    
    print("\n正在爬取「漲幅排行」...")
    try:
        change_up_stocks = get_yahoo_rank_stocks(url_change_up)
        print(f"成功取得 {len(change_up_stocks)} 檔股票。")
    except Exception as e:
        print(f"爬取漲幅排行失敗: {e}")
        return
    
    # 找出同時存在於兩個排行榜的股票代號 (取交集)
    # 保留 Yahoo 成交量榜的原始順序 (榜首 = 最大量，依序遞減)
    intersection_symbols = [
        symbol for symbol in volume_stocks.keys()
        if symbol in change_up_stocks
    ]
    
    print("\n" + "="*50)
    print("同時出現在「成交量排行」與「漲幅排行」的股票")
    print("(依 Yahoo 成交量榜順序排序 — 榜首為當日最大量)")
    print("="*50)
    
    if not intersection_symbols:
        print("目前沒有同時上榜的股票。")
    else:
        # 準備儲存成 CSV 的檔案名稱 (加上今天的日期)
        today_str = datetime.now().strftime("%Y%m%d")
        csv_filename = f"yahoo_rank_intersection_{today_str}.csv"
        
        # 寫入 CSV 檔案
        with open(csv_filename, mode="w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(["股票代碼", "股票名稱"])
            
            for symbol in intersection_symbols:
                name = volume_stocks[symbol]
                print(f"- {symbol.ljust(10)} {name}")
                writer.writerow([symbol, name])
                
        print(f"\n資料已成功儲存至: {csv_filename}")

if __name__ == "__main__":
    main()
