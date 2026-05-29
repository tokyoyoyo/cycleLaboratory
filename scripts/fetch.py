#!/usr/bin/env python3
"""数据获取脚本 - 国内数据源 + parquet 缓存

数据源:
  ETF 日线: Sina Finance (fund_etf_hist_sina)
  个股日线: 腾讯财经 (stock_zh_a_hist_tx)
  财务数据: 腾讯财经 (stock_financial_abstract)
  指数行情: 腾讯财经 (stock_zh_index_daily_tx)
  周线: 日线聚合生成

用法:
  python scripts/fetch.py --all [--force]     # 获取全部关注标的
  python scripts/fetch.py --symbol 512690      # 获取单个标的
"""
import argparse
import os
import sys
import time
import json
from datetime import datetime, date, timedelta
from typing import Optional

import pandas as pd
import requests
import yaml
import akshare as ak

# 项目根目录
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ============================================================
# 缓存层（parquet）
# ============================================================

def _cache_path(filename: str) -> str:
    return os.path.join(ROOT, "data", filename)


def _is_stale(filepath: str) -> bool:
    """检查缓存是否过期

    规则:
      - 交易时段（工作日 9:30-15:30）：缓存当日过期，跨日即失效
      - 收盘后到次日开盘前：缓存不过期（数据已是今日收盘价）
      - 周末/节假日：24小时过期，确保周一开盘前能拉到上周五数据
    """
    if not os.path.exists(filepath):
        return True
    mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
    now = datetime.now()

    if now.weekday() < 5:
        opening = now.replace(hour=9, minute=30, second=0)
        closing = now.replace(hour=15, minute=30, second=0)

        if opening <= now <= closing:
            # 交易时段：必须今天拉过
            if mtime.date() < now.date():
                return True

        # 收盘后：缓存必须是在收盘后写入的，否则缺今天数据
        if now > closing and mtime < closing:
            return True

        # 开盘前：如果缓存是昨天的，等到交易时段再说
        if now < opening and mtime.date() < now.date():
            return False

    # 兜底：超过 48 小时强制过期
    return (now - mtime) > timedelta(hours=48)


def load_kline(symbol: str) -> Optional[list]:
    filepath = _cache_path(f"{symbol}_daily_kline.parquet")
    if not os.path.exists(filepath):
        return None
    df = pd.read_parquet(filepath)
    return [_row_to_bar(row, symbol) for _, row in df.iterrows()]


def save_kline(symbol: str, data: list):
    filepath = _cache_path(f"{symbol}_daily_kline.parquet")
    df = pd.DataFrame([{
        "date": bar["date"], "open": bar["open"], "high": bar["high"],
        "low": bar["low"], "close": bar["close"],
        "volume": bar.get("volume", 0), "amount": bar.get("amount", 0),
        "turnover": bar.get("turnover", 0),
    } for bar in data])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates(subset="date", keep="last")
    df.to_parquet(filepath, index=False)


def load_weekly_kline(symbol: str) -> Optional[list]:
    filepath = _cache_path(f"{symbol}_weekly_kline.parquet")
    if not os.path.exists(filepath):
        return None
    df = pd.read_parquet(filepath)
    return [_row_to_bar(row, symbol) for _, row in df.iterrows()]


def save_weekly_kline(symbol: str, data: list):
    filepath = _cache_path(f"{symbol}_weekly_kline.parquet")
    df = pd.DataFrame([{
        "date": bar["date"], "open": bar["open"], "high": bar["high"],
        "low": bar["low"], "close": bar["close"],
        "volume": bar.get("volume", 0), "amount": bar.get("amount", 0),
        "turnover": bar.get("turnover", 0),
    } for bar in data])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates(subset="date", keep="last")
    df.to_parquet(filepath, index=False)


def load_financials(symbol: str) -> Optional[list]:
    filepath = _cache_path(f"{symbol}_financials.parquet")
    if not os.path.exists(filepath):
        return None
    df = pd.read_parquet(filepath)
    result = []
    for _, row in df.iterrows():
        d = row["report_date"]
        if hasattr(d, "date"):
            d = d.date()
        elif isinstance(d, str):
            d = datetime.strptime(d[:10], "%Y-%m-%d").date()
        result.append({
            "symbol": symbol, "report_date": d,
            "roe": float(row["roe"]), "net_margin": float(row["net_margin"]),
            "revenue_yoy": float(row["revenue_yoy"]), "profit_yoy": float(row["profit_yoy"]),
            "debt_ratio": float(row["debt_ratio"]), "current_ratio": float(row["current_ratio"]),
            "op_cash_flow": float(row["op_cash_flow"]),
        })
    return result


def save_financials(symbol: str, data: list):
    filepath = _cache_path(f"{symbol}_financials.parquet")
    df = pd.DataFrame([{
        "report_date": d["report_date"], "roe": d["roe"], "net_margin": d["net_margin"],
        "revenue_yoy": d["revenue_yoy"], "profit_yoy": d["profit_yoy"],
        "debt_ratio": d["debt_ratio"], "current_ratio": d["current_ratio"],
        "op_cash_flow": d["op_cash_flow"],
    } for d in data])
    df["report_date"] = pd.to_datetime(df["report_date"])
    df = df.sort_values("report_date").drop_duplicates(subset="report_date", keep="last")
    df.to_parquet(filepath, index=False)


def load_indicator(symbol: str) -> Optional[list]:
    filepath = _cache_path(f"{symbol}_indicator.parquet")
    if not os.path.exists(filepath):
        return None
    df = pd.read_parquet(filepath)
    result = []
    for _, row in df.iterrows():
        d = row["date"]
        if hasattr(d, "date"):
            d = d.date()
        elif isinstance(d, str):
            d = datetime.strptime(d[:10], "%Y-%m-%d").date()
        result.append({
            "symbol": symbol, "date": d,
            "eps": float(row["eps"]),
            "bvps": float(row["bvps"]),
            "roe": float(row["roe"]),
        })
    return result


def save_indicator(symbol: str, data: list):
    filepath = _cache_path(f"{symbol}_indicator.parquet")
    df = pd.DataFrame([{
        "date": d["date"], "eps": d["eps"],
        "bvps": d["bvps"], "roe": d["roe"],
    } for d in data])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates(subset="date", keep="last")
    df.to_parquet(filepath, index=False)


def load_csi300() -> Optional[list]:
    filepath = _cache_path("csi300.parquet")
    if not os.path.exists(filepath):
        return None
    df = pd.read_parquet(filepath)
    return [_row_to_bar(row, "CSI300") for _, row in df.iterrows()]


def save_csi300(data: list):
    filepath = _cache_path("csi300.parquet")
    df = pd.DataFrame([{
        "date": bar["date"], "open": bar["open"], "high": bar["high"],
        "low": bar["low"], "close": bar["close"],
        "volume": bar.get("volume", 0), "amount": bar.get("amount", 0),
        "turnover": bar.get("turnover", 0),
    } for bar in data])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates(subset="date", keep="last")
    df.to_parquet(filepath, index=False)


def needs_refresh(symbol: str) -> bool:
    return _is_stale(_cache_path(f"{symbol}_daily_kline.parquet"))


def _log_fetch(symbol: str, name: str, target_type: str,
               data_start: str, data_end: str, rows: int,
               has_today: bool, status: str, error: str = ""):
    """记录每次数据拉取到 data/fetch_log.jsonl"""
    log_path = _cache_path("fetch_log.jsonl")
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "symbol": symbol,
        "name": name,
        "type": target_type,
        "data_start": data_start,
        "data_end": data_end,
        "rows": rows,
        "has_today": has_today,
        "status": status,
    }
    if error:
        entry["error"] = error
    try:
        with open(log_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 日志写入失败不应中断主流程


def _log_fetch_csi300(data_start: str, data_end: str, rows: int, status: str, error: str = ""):
    """CSI300 拉取日志"""
    _log_fetch("CSI300", "沪深300指数", "index",
               data_start, data_end, rows, False, status, error)


def _row_to_bar(row, symbol: str) -> dict:
    d = row["date"]
    if hasattr(d, "date"):
        d = d.date()
    elif isinstance(d, str):
        d = datetime.strptime(d[:10], "%Y-%m-%d").date()
    return {
        "symbol": symbol, "date": d,
        "open": float(row["open"]), "high": float(row["high"]),
        "low": float(row["low"]), "close": float(row["close"]),
        "volume": float(row.get("volume", 0)), "amount": float(row.get("amount", 0)),
        "turnover": float(row.get("turnover", 0)),
    }


# ============================================================
# Symbol 格式转换
# ============================================================

def _to_exchange_symbol(symbol: str) -> str:
    """纯数字 → 带交易所前缀"""
    code = str(symbol)
    if code.startswith(("6", "51", "56", "58")):
        return f"sh{code}"
    if code.startswith(("0", "1", "3")):
        return f"sz{code}"
    return f"sh{code}"


# ============================================================
# 数据获取
# ============================================================

def _retry(func, *args, retries=3, backoff=2.0, **kwargs):
    last_err = None
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise last_err


def fetch_daily_kline(symbol: str, target_type: str,
                      start_date: str = "20190101",
                      end_date: Optional[str] = None) -> list:
    """获取日K线数据"""
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    if target_type == "etf":
        return _fetch_daily_kline_sina(symbol, start_date, end_date)
    else:
        return _fetch_daily_kline_sina_stock(symbol, start_date, end_date)


def _is_trading_day(d: date = None) -> bool:
    """简单判断是否为交易日（仅排除周末，节假日由数据源自然过滤）"""
    if d is None:
        d = date.today()
    return d.weekday() < 5


def _after_market_close() -> bool:
    """当前时间是否在收盘后（>15:30）"""
    now = datetime.now()
    closing = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return now > closing


def _fetch_realtime_sina(ex_symbol: str) -> Optional[dict]:
    """从新浪实时行情获取当日数据，用于补充历史接口缺少的当日数据"""
    try:
        url = f"https://hq.sinajs.cn/list={ex_symbol}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None
        # 格式: var hq_str_shXXXXXX="名称,今开,昨收,现价,最高,最低,买价,卖价,成交量,成交额,...日期,时间";
        text = resp.text
        if '="' not in text:
            return None
        data = text.split('="')[1].rstrip('";\n')
        parts = data.split(",")
        if len(parts) < 32:
            return None
        trade_date = datetime.strptime(parts[30], "%Y-%m-%d").date()
        price = float(parts[3])
        if price <= 0:
            return None
        return {
            "date": trade_date,
            "open": float(parts[1]),
            "high": float(parts[4]),
            "low": float(parts[5]),
            "close": price,
            "volume": float(parts[8]),
            "amount": float(parts[9]),
        }
    except Exception:
        return None


def _supplement_today(symbol: str, result: list, ex_symbol: str) -> tuple:
    """历史接口缺当日数据时，用实时行情补充。

    仅在交易日收盘后执行——盘中实时价不是收盘价，不能写入日线缓存。
    返回 (supplemented: bool, reason: str)。
    """
    if not result:
        return False, "无历史数据"
    last_date = result[-1]["date"]
    today = date.today()
    if last_date >= today:
        return False, "数据已含今日"
    if not _is_trading_day(today):
        return False, "非交易日"
    if not _after_market_close():
        return False, "尚未收盘"
    rt = _fetch_realtime_sina(ex_symbol)
    if not rt:
        return False, "实时行情获取失败"
    if rt["date"] != today:
        return False, f"实时行情日期不匹配: {rt['date']} != {today}"
    result.append({
        "symbol": symbol, "date": rt["date"],
        "open": rt["open"], "high": rt["high"],
        "low": rt["low"], "close": rt["close"],
        "volume": rt["volume"], "amount": rt["amount"],
    })
    return True, "已补充当日实时数据"


def _fetch_daily_kline_sina(symbol: str, start_date: str, end_date: str) -> list:
    """ETF 日线 - Sina Finance"""
    ex_symbol = _to_exchange_symbol(symbol)
    df = _retry(ak.fund_etf_hist_sina, symbol=ex_symbol)
    if df.empty:
        return []

    df["date"] = pd.to_datetime(df["date"])
    start_dt = datetime.strptime(start_date, "%Y%m%d")
    end_dt = datetime.strptime(end_date, "%Y%m%d")
    df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]

    result = []
    for _, row in df.iterrows():
        try:
            d = row["date"]
            if hasattr(d, "date"):
                d = d.date()
            result.append({
                "symbol": symbol, "date": d,
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row["volume"]), "amount": float(row["amount"]),
            })
        except (ValueError, KeyError, TypeError):
            continue

    supplemented, reason = _supplement_today(symbol, result, ex_symbol)
    if supplemented:
        print(f"  ℹ {symbol}: {reason}")

    return result


def _fetch_daily_kline_sina_stock(symbol: str, start_date: str, end_date: str) -> list:
    """个股日线 - Sina Finance（有真实成交量）"""
    ex_symbol = _to_exchange_symbol(symbol)
    df = _retry(ak.stock_zh_a_daily, symbol=ex_symbol, adjust="qfq")
    if df.empty:
        return []

    df["date"] = pd.to_datetime(df["date"])
    start_dt = datetime.strptime(start_date, "%Y%m%d")
    end_dt = datetime.strptime(end_date, "%Y%m%d")
    df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]

    result = []
    for _, row in df.iterrows():
        try:
            d = row["date"]
            if hasattr(d, "date"):
                d = d.date()
            result.append({
                "symbol": symbol, "date": d,
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row.get("volume", 0)),
                "amount": float(row.get("amount", 0)),
                "turnover": float(row.get("turnover", 0)),
            })
        except (ValueError, KeyError, TypeError):
            continue

    supplemented, reason = _supplement_today(symbol, result, ex_symbol)
    if supplemented:
        print(f"  ℹ {symbol}: {reason}")

    return result


def resample_daily_to_weekly(bars: list) -> list:
    """从日线数据聚合为周线"""
    if not bars:
        return []
    df = pd.DataFrame([{
        "date": b["date"], "open": b["open"], "high": b["high"],
        "low": b["low"], "close": b["close"],
        "volume": b.get("volume", 0), "amount": b.get("amount", 0),
    } for b in bars])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")

    weekly = df.resample("W").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum", "amount": "sum",
    })
    weekly = weekly.dropna()

    result = []
    for idx, row in weekly.iterrows():
        result.append({
            "symbol": bars[0]["symbol"], "date": idx.date(),
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": float(row["volume"]), "amount": float(row["amount"]),
        })
    return result


def fetch_financials(symbol: str) -> list:
    """获取季度财务数据 - 腾讯财经"""
    try:
        df = _retry(ak.stock_financial_abstract, symbol=symbol)
    except Exception:
        return []
    if df.empty:
        return []

    indicator_map = {}
    for i, row in df.iterrows():
        indicator_map[str(row["指标"])] = i

    quarter_cols = [c for c in df.columns if c not in ["选项", "指标"]]
    if not quarter_cols:
        return []

    result = []
    for qcol in quarter_cols:
        try:
            qdate = datetime.strptime(str(qcol)[:8], "%Y%m%d").date()
        except ValueError:
            continue

        def _val(name: str):
            idx = indicator_map.get(name)
            if idx is None:
                return 0.0
            v = df.iloc[idx][qcol]
            try:
                return float(v) if pd.notna(v) else 0.0
            except (ValueError, TypeError):
                return 0.0

        roe = _val("净资产收益率(ROE)")
        net_margin = _val("销售净利率")
        revenue_yoy = _val("营业总收入增长率")
        profit_yoy = _val("归属母公司净利润增长率")
        debt_ratio = _val("资产负债率")
        current_ratio = _val("流动比率")
        op_cash_flow = _val("每股经营现金流")

        if all(v == 0.0 for v in [roe, net_margin, revenue_yoy, profit_yoy,
                                    debt_ratio, current_ratio, op_cash_flow]):
            continue

        result.append({
            "symbol": symbol, "report_date": qdate,
            "roe": roe, "net_margin": net_margin,
            "revenue_yoy": revenue_yoy, "profit_yoy": profit_yoy,
            "debt_ratio": debt_ratio, "current_ratio": current_ratio,
            "op_cash_flow": op_cash_flow,
        })

    result.sort(key=lambda x: x["report_date"], reverse=True)
    return result[:16]


def fetch_financial_indicator(symbol: str) -> list:
    """获取季度财务指标（EPS/BVPS/ROE）- 用于 PE 分位计算"""
    try:
        df = _retry(ak.stock_financial_abstract_ths,
                    symbol=symbol, indicator="按报告期")
    except Exception:
        return []
    if df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        try:
            d = row["报告期"]
            if hasattr(d, "date"):
                d = d.date()
            elif isinstance(d, str):
                d = datetime.strptime(d[:10], "%Y-%m-%d").date()

            eps = row.get("基本每股收益", None)
            bvps = row.get("每股净资产", None)
            roe = row.get("净资产收益率", None)

            # Parse numeric values from percentage strings
            def _parse(v):
                if v is None or (isinstance(v, float) and pd.isna(v)) or v == "False" or v is False:
                    return 0.0
                if isinstance(v, (int, float)):
                    return float(v)
                s = str(v)
                if s.endswith("%"):
                    return float(s[:-1])
                try:
                    return float(s)
                except ValueError:
                    return 0.0

            eps_val = _parse(eps)
            bvps_val = _parse(bvps)
            roe_val = _parse(roe)

            if eps_val == 0 and bvps_val == 0 and roe_val == 0:
                continue

            result.append({
                "symbol": symbol, "date": d,
                "eps": eps_val, "bvps": bvps_val, "roe": roe_val,
            })
        except (ValueError, KeyError, TypeError):
            continue

    result.sort(key=lambda x: x["date"])
    return result


def fetch_csi300(start_date: str = "20190101",
                 end_date: Optional[str] = None) -> list:
    """获取沪深300指数数据"""
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
    try:
        df = _retry(ak.stock_zh_index_daily_tx, symbol="sh000300")
    except Exception:
        return []
    if df.empty:
        return []

    df["date"] = pd.to_datetime(df["date"])
    start_dt = datetime.strptime(start_date, "%Y%m%d")
    end_dt = datetime.strptime(end_date, "%Y%m%d")
    df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]

    result = []
    for _, row in df.iterrows():
        try:
            d = row["date"]
            if hasattr(d, "date"):
                d = d.date()
            result.append({
                "symbol": "CSI300", "date": d,
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": 0, "amount": float(row.get("amount", 0)) * 10000,
            })
        except (ValueError, KeyError, TypeError):
            continue
    return result


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="数据获取")
    parser.add_argument("--all", action="store_true", help="获取全部关注标的")
    parser.add_argument("--symbol", type=str, help="获取单个标的")
    parser.add_argument("--force", action="store_true", help="强制刷新")
    args = parser.parse_args()

    # 加载 watchlist
    with open(os.path.join(ROOT, "watchlist.yaml"), "r") as f:
        watchlist = yaml.safe_load(f)
    targets = watchlist.get("targets", [])

    today = date.today()
    lookback = 5  # years

    def fetch_target(target, force: bool):
        symbol = target["symbol"]
        name = target["name"]
        ttype = target.get("type", "stock")

        if not force and not needs_refresh(symbol):
            print(f"  {name}({symbol}): 缓存有效，跳过")
            return

        print(f"  获取 {name}({symbol}) 日线...")
        try:
            kline = fetch_daily_kline(
                symbol, ttype,
                start_date=f"{today.year - lookback}0101"
            )
            if kline:
                save_kline(symbol, kline)
                print(f"  ✓ {name}: {len(kline)} 条日线")
                # 周线
                weekly = resample_daily_to_weekly(kline)
                if weekly:
                    save_weekly_kline(symbol, weekly)
                # 日志
                has_today = kline[-1]["date"] == today
                _log_fetch(symbol, name, ttype,
                           str(kline[0]["date"]), str(kline[-1]["date"]),
                           len(kline), has_today, "ok")
            else:
                print(f"  ⚠ {name}: 无数据")
                _log_fetch(symbol, name, ttype, "", "", 0, False, "empty")
        except Exception as e:
            print(f"  ⚠ {name}: 获取失败 - {e}")
            _log_fetch(symbol, name, ttype, "", "", 0, False, "error", str(e))

        # 个股获取财务数据和指标
        if ttype == "stock":
            print(f"  获取 {name} 财务...")
            try:
                fin = fetch_financials(symbol)
                if fin:
                    save_financials(symbol, fin)
                    print(f"  ✓ {name} 财务: {len(fin)} 条")
            except Exception as e:
                print(f"  ⚠ {name} 财务: {e}")

            print(f"  获取 {name} 财务指标(EPS/BVPS/ROE)...")
            try:
                ind = fetch_financial_indicator(symbol)
                if ind:
                    save_indicator(symbol, ind)
                    print(f"  ✓ {name} 指标: {len(ind)} 条")
            except Exception as e:
                print(f"  ⚠ {name} 指标: {e}")

    if args.symbol:
        target = next((t for t in targets if t["symbol"] == args.symbol), None)
        if not target:
            print(f"未找到标的: {args.symbol}")
            sys.exit(1)
        fetch_target(target, force=args.force)
    elif args.all:
        print("=" * 60)
        print("  Cycle Laboratory - 数据获取")
        print("=" * 60)
        for t in targets:
            fetch_target(t, force=args.force)

        # CSI300
        if args.force or not os.path.exists(_cache_path("csi300.parquet")):
            print("  获取沪深300指数...")
            try:
                csi300 = fetch_csi300(start_date=f"{today.year - lookback}0101")
                if csi300:
                    save_csi300(csi300)
                    print(f"  ✓ 沪深300: {len(csi300)} 条")
                    _log_fetch_csi300(str(csi300[0]["date"]), str(csi300[-1]["date"]),
                                      len(csi300), "ok")
            except Exception as e:
                print(f"  ⚠ 沪深300: {e}")
                _log_fetch_csi300("", "", 0, "error", str(e))
    else:
        # 默认 --all
        for t in targets:
            fetch_target(t, force=args.force)
        if args.force or not os.path.exists(_cache_path("csi300.parquet")):
            print("  获取沪深300指数...")
            try:
                csi300 = fetch_csi300(start_date=f"{today.year - lookback}0101")
                if csi300:
                    save_csi300(csi300)
                    print(f"  ✓ 沪深300: {len(csi300)} 条")
                    _log_fetch_csi300(str(csi300[0]["date"]), str(csi300[-1]["date"]),
                                      len(csi300), "ok")
            except Exception as e:
                print(f"  ⚠ 沪深300: {e}")
                _log_fetch_csi300("", "", 0, "error", str(e))

    print("\n数据获取完成。")


if __name__ == "__main__":
    main()
