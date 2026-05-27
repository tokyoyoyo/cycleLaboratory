#!/usr/bin/env python3
"""全市场初筛脚本 - 发掘不在关注列表的周期价值标的

扫描范围: 沪深300 + 中证500 成分股（~800只行业龙头+大盘股）
筛选逻辑: PE低分位 + 深度回撤 + 大盘股 → 返回候选列表

用法:
  python scripts/scan.py              # 全量扫描
  python scripts/scan.py --top 20     # 只返回 top 20
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, date

import numpy as np
import pandas as pd
import yaml
import akshare as ak

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_watchlist_symbols() -> set:
    """获取已关注的 symbol 集合"""
    with open(os.path.join(ROOT, "watchlist.yaml"), "r") as f:
        config = yaml.safe_load(f)
    return {t["symbol"] for t in config.get("targets", [])}


def get_index_constituents(index_symbol: str) -> list:
    """获取指数成分股列表"""
    try:
        df = ak.index_stock_cons(symbol=index_symbol)
        if df.empty:
            return []
        # 列名可能是 "品种代码" 或 "stock_code" 等
        for col in ["品种代码", "stock_code", "code", "symbol"]:
            if col in df.columns:
                return df[col].astype(str).tolist()
        # fallback: 第一列
        return df.iloc[:, 0].astype(str).tolist()
    except Exception:
        return []


def get_all_a_spot() -> pd.DataFrame:
    """获取全A实时行情"""
    try:
        df = ak.stock_zh_a_spot()
        if df.empty:
            return pd.DataFrame()
        # 标准化列名
        df["symbol"] = df["代码"].astype(str)
        df["name"] = df["名称"].astype(str)
        df["price"] = pd.to_numeric(df["最新价"], errors="coerce")
        df["pe"] = pd.to_numeric(df["市盈率-动态"], errors="coerce")
        df["pb"] = pd.to_numeric(df["市净率"], errors="coerce")
        df["market_cap"] = pd.to_numeric(df["总市值"], errors="coerce")
        df["change_pct"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
        df["volume"] = pd.to_numeric(df["成交量"], errors="coerce")
        return df.dropna(subset=["price", "market_cap"])
    except Exception as e:
        print(f"获取全A行情失败: {e}", file=sys.stderr)
        return pd.DataFrame()


def quick_scan() -> list:
    """快速扫描，返回候选标的列表"""
    watched = load_watchlist_symbols()
    print("  获取指数成分股...", file=sys.stderr)

    # 沪深300 + 中证500 成分股
    hs300 = get_index_constituents("000300")
    zz500 = get_index_constituents("000905")
    universe = set(hs300 + zz500)
    print(f"  沪深300: {len(hs300)} 只, 中证500: {len(zz500)} 只, "
          f"并集: {len(universe)} 只", file=sys.stderr)

    # 获取全A实时行情
    print("  获取实时行情...", file=sys.stderr)
    spot = get_all_a_spot()
    if spot.empty:
        return []

    # 只在指数成分股中筛选
    spot_in_universe = spot[spot["symbol"].isin(universe)]
    print(f"  成分股中有效: {len(spot_in_universe)} 只", file=sys.stderr)

    # === 筛选条件 ===
    df = spot_in_universe.copy()

    # 条件1: 市值 > 100亿（大盘）
    df = df[df["market_cap"] > 1e10]
    print(f"  市值>100亿: {len(df)} 只", file=sys.stderr)

    # 条件2: PE > 0（有盈利）且 < 25（便宜）
    df_cheap = df[(df["pe"] > 0) & (df["pe"] < 25)].copy()
    print(f"  0<PE<25: {len(df_cheap)} 只", file=sys.stderr)

    # 条件3: 排除已关注的
    df_cheap = df_cheap[~df_cheap["symbol"].isin(watched)]
    print(f"  排除已关注后: {len(df_cheap)} 只", file=sys.stderr)

    # 先按 PE 排序取前200，再按市值排序保证可投性
    df_cheap = df_cheap.sort_values("pe").head(200)
    df_cheap = df_cheap.sort_values("market_cap", ascending=False)

    # 构建候选列表
    candidates = []
    for _, row in df_cheap.head(50).iterrows():
        candidates.append({
            "symbol": row["symbol"],
            "name": row["name"],
            "price": round(float(row["price"]), 4),
            "pe": round(float(row["pe"]), 2),
            "pb": round(float(row["pb"]), 2) if pd.notna(row["pb"]) else None,
            "market_cap": int(row["market_cap"]),
            "market_cap_yi": round(float(row["market_cap"]) / 1e8, 1),  # 亿
            "change_pct": round(float(row["change_pct"]), 2) if pd.notna(row["change_pct"]) else None,
            "in_hs300": row["symbol"] in hs300,
            "in_zz500": row["symbol"] in zz500,
        })

    return candidates


def main():
    parser = argparse.ArgumentParser(description="全市场初筛")
    parser.add_argument("--top", type=int, default=50, help="返回 top N 候选")
    args = parser.parse_args()

    print("=" * 60, file=sys.stderr)
    print("  Cycle Laboratory - 全市场扫描", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  扫描时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}", file=sys.stderr)

    candidates = quick_scan()
    top = candidates[:args.top]

    print(f"\n  最终候选: {len(top)} 只\n", file=sys.stderr)

    # 输出 JSON
    print(json.dumps({
        "scan_date": str(date.today()),
        "universe": "沪深300 + 中证500",
        "criteria": "市值>100亿, 0<PE<25, 未在关注列表",
        "count": len(top),
        "candidates": top,
    }, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
