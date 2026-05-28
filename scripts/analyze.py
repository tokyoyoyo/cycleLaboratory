#!/usr/bin/env python3
"""周期分析脚本 - 估值/技术/基本面/周期阶段/推荐行动

核心理念:
  - 周期股用 PB 估值，PE 在底部失真（盈利微薄/亏损）
  - 基本面看"生存力"（负债/现金流），不看 ROE 正负
  - 不输出机械评分，输出周期阶段 + 判断依据

用法:
  python scripts/analyze.py --all       # 全量分析 → JSON
  python scripts/analyze.py --symbol 002714  # 单标的分析 → JSON
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.fetch import (
    load_kline, load_financials, load_csi300, load_weekly_kline,
    load_indicator, needs_refresh,
)


# ============================================================
# 配置
# ============================================================

def load_config() -> dict:
    with open(os.path.join(ROOT, "watchlist.yaml"), "r") as f:
        return yaml.safe_load(f)


def load_ledger() -> dict:
    path = os.path.join(ROOT, "ledger.yaml")
    if not os.path.exists(path):
        return {"trades": []}
    with open(path, "r") as f:
        return yaml.safe_load(f) or {"trades": []}


# ============================================================
# 假设持仓计算
# ============================================================

def compute_holding(symbol: str, current_price: float) -> dict:
    """从 ledger 计算某个标的的假设持仓"""
    ledger = load_ledger()
    trades = [t for t in ledger.get("trades", []) if t["symbol"] == symbol]

    if not trades:
        return {"held": False, "shares": 0, "total_cost": 0,
                "avg_cost": None, "pnl": 0, "pnl_pct": None,
                "trades": []}

    total_shares = 0
    total_cost = 0

    for t in trades:
        if t["action"] == "buy":
            total_shares += t["quantity"]
            total_cost += t["quantity"] * t["price"]
        elif t["action"] == "sell":
            # FIFO 简化：按均价卖出
            avg_cost_before = total_cost / total_shares if total_shares > 0 else 0
            total_shares -= t["quantity"]
            total_cost -= t["quantity"] * avg_cost_before

    if total_shares <= 0:
        return {"held": False, "shares": 0, "total_cost": 0,
                "avg_cost": None, "pnl": 0, "pnl_pct": None,
                "trades": trades}

    avg_cost = total_cost / total_shares
    current_value = total_shares * current_price
    pnl = current_value - total_cost
    pnl_pct = round((current_price - avg_cost) / avg_cost * 100, 1)

    return {
        "held": True,
        "shares": total_shares,
        "total_cost": round(total_cost, 2),
        "current_value": round(current_value, 2),
        "avg_cost": round(avg_cost, 4),
        "pnl": round(pnl, 2),
        "pnl_pct": pnl_pct,
        "trades_count": len(trades),
    }


# ============================================================
# 技术指标计算（工具函数）
# ============================================================

def _calc_ma(data: np.ndarray, period: int) -> np.ndarray:
    if len(data) < period:
        return np.full_like(data, np.nan)
    kernel = np.ones(period) / period
    ma = np.convolve(data, kernel, mode="valid")
    result = np.full_like(data, np.nan)
    result[period - 1:] = ma
    return result


def _calc_ema(data: np.ndarray, period: int) -> np.ndarray:
    alpha = 2 / (period + 1)
    result = np.zeros_like(data)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


# ============================================================
# 估值分析 — PB 为主（周期股），PE 仅作参考
# ============================================================

def analyze_valuation(kline: list, financials: Optional[list],
                      is_etf: bool = False,
                      indicator: Optional[list] = None) -> dict:
    """返回原始估值指标，不打分"""
    closes = np.array([b["close"] for b in kline])
    dts = [b["date"] for b in kline]
    current_price = closes[-1]

    # --- PE 分位（参考） ---
    pe_pct = None
    if not is_etf and indicator and len(indicator) >= 8:
        price_map = {b["date"]: b["close"] for b in kline}

        def _find_price(report_date):
            if report_date in price_map:
                return price_map[report_date]
            best_date, best_price = None, None
            for d, p in price_map.items():
                if d <= report_date and (best_date is None or d > best_date):
                    best_date, best_price = d, p
            return best_price

        ind_sorted = sorted(indicator, key=lambda x: x["date"])
        historical_pe = []
        for i in range(4, len(ind_sorted) + 1):
            window = ind_sorted[i - 4:i]
            ttm_eps = sum(w["eps"] for w in window)
            if ttm_eps <= 0:
                continue
            rpt_date = window[-1]["date"]
            px = _find_price(rpt_date)
            if px and px > 0:
                historical_pe.append(px / ttm_eps)

        if historical_pe and len(historical_pe) >= 4:
            current_window = ind_sorted[-4:]
            current_ttm_eps = sum(w["eps"] for w in current_window)
            if current_ttm_eps > 0:
                current_pe = current_price / current_ttm_eps
                pe_array = np.array(historical_pe)
                pe_pct = round((pe_array < current_pe).sum() / len(pe_array) * 100, 1)

    # --- PB 分位（周期股主力指标） ---
    # 用价格分位近似（PB 数据源不稳定），有 PB 真实数据时优先
    pb_pct = round((closes < current_price).sum() / len(closes) * 100, 1)

    # --- 价格分位 ---
    price_pct_3y = None
    cutoff_3y = date.today() - timedelta(days=3 * 365)
    mask_3y = np.array([d >= cutoff_3y for d in dts])
    if mask_3y.sum() >= 100:
        filtered_3y = closes[mask_3y]
        price_pct_3y = round((filtered_3y < current_price).sum() / len(filtered_3y) * 100, 1)

    price_pct_5y = None
    cutoff_5y = date.today() - timedelta(days=5 * 365)
    mask_5y = np.array([d >= cutoff_5y for d in dts])
    if mask_5y.sum() >= 100:
        filtered_5y = closes[mask_5y]
        price_pct_5y = round((filtered_5y < current_price).sum() / len(filtered_5y) * 100, 1)

    # --- 回撤 ---
    dd_3y = None
    dd_days = None
    if mask_3y.sum() >= 100:
        filtered = closes[mask_3y]
        peak = np.maximum.accumulate(filtered)
        dd = (filtered - peak) / peak * 100
        dd_3y = round(float(dd[-1]), 1)
        dd_start = None
        for i in range(len(dd) - 1, -1, -1):
            if dd[i] > -1:
                dd_start = i
                break
        dd_days = len(dd) - dd_start if dd_start is not None else len(dd)

    return {
        "pb_pct": pb_pct,
        "pe_pct": pe_pct,
        "price_pct_3y": price_pct_3y,
        "price_pct_5y": price_pct_5y,
        "drawdown_3y": dd_3y,
        "drawdown_days": dd_days,
    }


# ============================================================
# 技术分析 — 输出信号描述，不打分
# ============================================================

def analyze_technical(kline: list, weekly_kline: Optional[list] = None) -> dict:
    """返回原始技术信号，不打分"""
    rsi_period = 14
    macd_fast, macd_slow, macd_signal = 12, 26, 9

    closes = np.array([b["close"] for b in kline])
    volumes = np.array([b.get("volume", 0) for b in kline])
    current = closes[-1]

    # --- 均线状态 ---
    ma_values = {}
    for p in [20, 60, 120, 250]:
        ma = _calc_ma(closes, p)
        if len(closes) >= p and not np.isnan(ma[-1]):
            ma_values[f"ma{p}"] = round(float(ma[-1]), 4)

    # 均线排列判断
    ma_status = "交叉"
    short_term = []
    for p in [20, 60, 120]:
        if f"ma{p}" in ma_values:
            short_term.append(ma_values[f"ma{p}"])
    if len(short_term) >= 3:
        if short_term[0] > short_term[1] > short_term[2]:
            ma_status = "多头排列"
        elif short_term[0] < short_term[1] < short_term[2]:
            ma_status = "空头排列"
        else:
            ma_status = "交叉"

    # 价格 vs 各均线
    price_vs_ma = {}
    for key, val in ma_values.items():
        pct = round((current - val) / val * 100, 1)
        price_vs_ma[key] = pct

    # --- RSI ---
    rsi = 50.0
    if len(closes) >= rsi_period + 1:
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-rsi_period:])
        avg_loss = np.mean(losses[-rsi_period:])
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = float(100 - 100 / (1 + rs))
    rsi = round(rsi, 1)

    # --- MACD ---
    macd_daily_signal = "中性"
    macd_weekly_signal = "中性"
    if len(closes) >= macd_slow + macd_signal:
        ema_fast = _calc_ema(closes, macd_fast)
        ema_slow = _calc_ema(closes, macd_slow)
        dif = ema_fast - ema_slow
        dea = _calc_ema(dif, macd_signal)
        if len(dif) >= 3 and len(dea) >= 3:
            if dif[-2] <= dea[-2] and dif[-1] > dea[-1]:
                macd_daily_signal = "金叉"
            elif dif[-2] >= dea[-2] and dif[-1] < dea[-1]:
                macd_daily_signal = "死叉"

    if weekly_kline and len(weekly_kline) >= macd_slow + macd_signal:
        weekly_closes = np.array([b["close"] for b in weekly_kline])
        w_ema_fast = _calc_ema(weekly_closes, macd_fast)
        w_ema_slow = _calc_ema(weekly_closes, macd_slow)
        w_dif = w_ema_fast - w_ema_slow
        w_dea = _calc_ema(w_dif, macd_signal)
        if len(w_dif) >= 3 and len(w_dea) >= 3:
            if w_dif[-2] <= w_dea[-2] and w_dif[-1] > w_dea[-1]:
                macd_weekly_signal = "金叉"
            elif w_dif[-2] >= w_dea[-2] and w_dif[-1] < w_dea[-1]:
                macd_weekly_signal = "死叉"

    # --- 成交量 ---
    volume_trend = "正常"
    vol_sum = np.sum(volumes) if len(volumes) > 0 else 0
    if vol_sum > 0 and len(volumes) >= 60:
        vol_short_avg = np.mean(volumes[-20:])
        vol_long_avg = np.mean(volumes[-60:])
        vol_ratio = vol_short_avg / vol_long_avg if vol_long_avg > 0 else 1.0
        price_falling = closes[-1] < closes[-20] if len(closes) >= 20 else False
        if price_falling:
            if vol_ratio < 0.7:
                volume_trend = "缩量（抛压衰竭）"
            elif vol_ratio > 1.3:
                volume_trend = "放量下跌"
            else:
                volume_trend = "下跌中正常量"
        else:
            if vol_ratio < 0.7:
                volume_trend = "缩量上涨"
            elif vol_ratio > 1.5:
                volume_trend = "放量上涨"
            else:
                volume_trend = "正常"
    elif vol_sum == 0:
        volume_trend = "无成交量数据"

    # --- 背离检测 ---
    div_bull = False
    div_bear = False
    if len(closes) >= 60:
        # 底背离：价格新低但 RSI 未新低
        recent_40_low = closes[-40:].min()
        rsi_now = rsi
        if len(closes) >= 40 + rsi_period:
            older_seg = closes[:-(rsi_period + 20)]
            if len(older_seg) >= rsi_period + 1:
                od = np.diff(older_seg)
                og = np.where(od > 0, od, 0)
                ol = np.where(od < 0, -od, 0)
                oag = np.mean(og[-rsi_period:]) if len(og) >= rsi_period else 0
                oal = np.mean(ol[-rsi_period:]) if len(ol) >= rsi_period else 0
                rsi_old = float(100 - 100 / (1 + oag / oal)) if oal > 0 else 100.0
                price_40d_low = closes[-40:-20].min() if len(closes) >= 40 else recent_40_low
                if closes[-1] <= price_40d_low * 1.02 and rsi_now > rsi_old + 5:
                    div_bull = True

        # 顶背离：价格新高但 RSI 未新高
        recent_40_high = closes[-40:].max()
        if len(closes) >= 40 + rsi_period:
            older_seg2 = closes[:-(rsi_period + 20)]
            if len(older_seg2) >= rsi_period + 1:
                od2 = np.diff(older_seg2)
                og2 = np.where(od2 > 0, od2, 0)
                ol2 = np.where(od2 < 0, -od2, 0)
                oag2 = np.mean(og2[-rsi_period:]) if len(og2) >= rsi_period else 0
                oal2 = np.mean(ol2[-rsi_period:]) if len(ol2) >= rsi_period else 0
                rsi_old2 = float(100 - 100 / (1 + oag2 / oal2)) if oal2 > 0 else 100.0
                price_40d_high = closes[-40:-20].max() if len(closes) >= 40 else recent_40_high
                if closes[-1] >= price_40d_high * 0.98 and rsi_now < rsi_old2 - 5:
                    div_bear = True

    return {
        "rsi": rsi,
        "ma_status": ma_status,
        "ma_values": ma_values,
        "price_vs_ma": price_vs_ma,
        "macd_daily": macd_daily_signal,
        "macd_weekly": macd_weekly_signal,
        "volume_trend": volume_trend,
        "divergence_bullish": div_bull,
        "divergence_bearish": div_bear,
    }


# ============================================================
# 基本面分析 — 生存力评估（负债/现金流/成本），不评 ROE
# ============================================================

def analyze_fundamental(financials: Optional[list], is_etf: bool = False) -> dict:
    """返回生存力指标，不打分"""
    if not financials:
        return {
            "debt_ratio": None, "current_ratio": None,
            "cash_flow_positive_q": None, "roe_current": None,
            "roe_trend": "无数据", "revenue_yoy_trend": "无数据",
            "revenue_declining_q": 0, "survivability": "无数据",
        }

    sorted_fin = sorted(financials, key=lambda x: x["report_date"])
    recent = sorted_fin[-4:]

    # --- 负债率 ---
    debt_ratios = [f["debt_ratio"] for f in recent if f["debt_ratio"] > 0]
    avg_debt = round(float(np.mean(debt_ratios)), 1) if debt_ratios else None

    # --- 流动比率 ---
    current_ratios = [f["current_ratio"] for f in recent if f["current_ratio"] > 0]
    avg_current = round(float(np.mean(current_ratios)), 2) if current_ratios else None

    # --- 经营现金流 ---
    cash_flows = [f["op_cash_flow"] for f in recent if f["op_cash_flow"] != 0]
    cash_positive_q = sum(1 for cf in cash_flows if cf > 0) if cash_flows else 0

    # --- ROE（仅输出趋势，不打分） ---
    roes = [f["roe"] for f in sorted_fin[-8:] if f["roe"] != 0]
    roe_current = round(float(roes[-1]), 1) if roes else None
    roe_trend = "无数据"
    if len(roes) >= 8:
        recent_roe = np.mean(roes[-4:])
        older_roe = np.mean(roes[-8:-4])
        if recent_roe > older_roe * 1.1:
            roe_trend = "改善"
        elif recent_roe < older_roe * 0.8:
            roe_trend = "下滑"
        else:
            roe_trend = "稳定"

    # --- 营收趋势 ---
    revenues = [f["revenue_yoy"] for f in sorted_fin[-12:] if f["revenue_yoy"] != 0]
    rev_trend = "无数据"
    if len(revenues) >= 4:
        recent_4 = revenues[-4:]
        avg_growth = np.mean(recent_4)
        if avg_growth > 10:
            rev_trend = "高增长"
        elif avg_growth > 0:
            rev_trend = "正增长"
        elif avg_growth > -10:
            rev_trend = "小幅下滑"
        else:
            rev_trend = "大幅下滑"

    # 营收连续下滑季度数
    recent_4_rev = [f["revenue_yoy"] for f in sorted_fin[-4:]]
    max_decline, current_decline = 0, 0
    for i in range(1, len(recent_4_rev)):
        if recent_4_rev[i] < recent_4_rev[i - 1]:
            current_decline += 1
            max_decline = max(max_decline, current_decline)
        else:
            current_decline = 0
    revenue_declining_q = max_decline

    # --- 生存力判断 ---
    # 周期股底部看的是：能不能活到周期反转
    survivability = "中等"
    danger_signals = 0
    if avg_debt and avg_debt > 70:
        danger_signals += 1
    if avg_current and avg_current < 1.0:
        danger_signals += 1
    if cash_positive_q is not None and cash_positive_q < 2:
        danger_signals += 1

    if danger_signals >= 2:
        survivability = "弱"
    elif danger_signals == 1:
        survivability = "中等"
    else:
        survivability = "强"

    return {
        "debt_ratio": avg_debt,
        "current_ratio": avg_current,
        "cash_flow_positive_q": cash_positive_q,
        "roe_current": roe_current,
        "roe_trend": roe_trend,
        "revenue_yoy_trend": rev_trend,
        "revenue_declining_q": revenue_declining_q,
        "survivability": survivability,
    }


# ============================================================
# 宏观环境 — 沪深300 自身周期位置
# ============================================================

def analyze_macro() -> dict:
    """返回市场环境指标"""
    csi300 = load_csi300()
    if not csi300 or len(csi300) < 250:
        return {"csi300_phase": "无数据", "csi300_price_pct_3y": None}

    closes = np.array([b["close"] for b in csi300])
    dts = [b["date"] for b in csi300]
    current = closes[-1]

    # 3年价格分位
    cutoff_3y = date.today() - timedelta(days=3 * 365)
    mask_3y = np.array([d >= cutoff_3y for d in dts])
    price_pct_3y = None
    if mask_3y.sum() >= 100:
        filtered = closes[mask_3y]
        price_pct_3y = round((filtered < current).sum() / len(filtered) * 100, 1)

    # 简单周期判断
    if price_pct_3y is not None:
        if price_pct_3y > 80:
            csi300_phase = "顶部"
        elif price_pct_3y < 20:
            csi300_phase = "底部"
        else:
            csi300_phase = "中部"
    else:
        csi300_phase = "无数据"

    return {
        "csi300_phase": csi300_phase,
        "csi300_price_pct_3y": price_pct_3y,
    }


# ============================================================
# 周期阶段分类 — 核心输出
# ============================================================

def classify_cycle(valuation: dict, technical: dict,
                   fundamental: dict, is_etf: bool = False) -> dict:
    """返回周期阶段 + 判断依据 + 证据列表"""
    pb_pct = valuation.get("pb_pct")
    pe_pct = valuation.get("pe_pct")
    price_pct = valuation.get("price_pct_3y")
    dd = valuation.get("drawdown_3y")

    rsi = technical.get("rsi", 50)
    ma_status = technical.get("ma_status", "")
    volume_trend = technical.get("volume_trend", "")
    div_bull = technical.get("divergence_bullish", False)
    div_bear = technical.get("divergence_bearish", False)

    survivability = fundamental.get("survivability", "中等")
    debt_ratio = fundamental.get("debt_ratio")
    rev_trend = fundamental.get("revenue_yoy_trend", "")
    rev_declining_q = fundamental.get("revenue_declining_q", 0)
    cash_pos_q = fundamental.get("cash_flow_positive_q", 4)

    evidence_for = []
    evidence_against = []

    # ================================================================
    # 1. 接飞刀检测 — 关键修复：看生存力，不看营收/ROE
    # ================================================================
    danger_reasons = []

    # 真正的危险信号：可能活不过周期底部
    if survivability == "弱":
        if debt_ratio and debt_ratio > 70:
            danger_reasons.append(f"负债率{debt_ratio:.0f}%，财务压力大")
        if cash_pos_q is not None and cash_pos_q <= 1:
            danger_reasons.append(f"近4季度仅{cash_pos_q}季经营现金流为正")

    # 深度回撤 + 无任何企稳信号
    if dd is not None and dd < -50:
        if ma_status == "空头排列" and rsi < 25 and not div_bull:
            danger_reasons.append(f"回撤{dd:.1f}%且无技术企稳信号")

    # 放量暴跌（恐慌抛售）
    if "放量下跌" in volume_trend and rsi < 20:
        danger_reasons.append("放量暴跌中，恐慌抛售未结束")

    if danger_reasons:
        evidence_against.extend(danger_reasons)
        return {
            "phase": "接飞刀",
            "reason": "；".join(danger_reasons),
            "evidence_for": evidence_for,
            "evidence_against": evidence_against,
        }

    # ================================================================
    # 2. 个股周期判断（PB 为主）
    # ================================================================
    if not is_etf:
        # 用 PB 分位作为主要判断依据
        anchor_pct = pb_pct if pb_pct is not None else price_pct

        if anchor_pct is not None and anchor_pct <= 15:
            # 估值极低
            if pe_pct is not None and pe_pct <= 15:
                evidence_for.append(f"PB分位{pb_pct}%，PE分位{pe_pct}%，双低确认")

            if div_bull:
                evidence_for.append("底背离信号")
                evidence_for.append(f"RSI({rsi:.0f})")
                return {
                    "phase": "底部（确认）",
                    "reason": f"PB处于{pb_pct}%极低分位，底背离确认，RSI企稳",
                    "evidence_for": evidence_for,
                    "evidence_against": evidence_against,
                }

            if "缩量" in volume_trend:
                evidence_for.append("成交量萎缩，抛压衰竭")
                return {
                    "phase": "底部（确认）",
                    "reason": f"PB处于{pb_pct}%极低分位，成交量萎缩筑底",
                    "evidence_for": evidence_for,
                    "evidence_against": evidence_against,
                }

            # 极低估值但无确认信号
            evidence_for.append(f"PB处于{pb_pct}%极低分位")
            if not div_bull:
                evidence_against.append("无底背离信号")
            if ma_status == "空头排列":
                evidence_against.append("均线空头排列，趋势未转")

            return {
                "phase": "底部（早期）",
                "reason": f"PB处于{pb_pct}%极低分位，但技术面尚未确认",
                "evidence_for": evidence_for,
                "evidence_against": evidence_against,
            }

        if anchor_pct is not None and anchor_pct <= 25:
            if ma_status == "多头排列" or div_bull:
                return {
                    "phase": "复苏",
                    "reason": f"PB处于{pb_pct}%低位，趋势向好",
                    "evidence_for": evidence_for + [f"PB分位{pb_pct}%", "技术面确认"],
                    "evidence_against": evidence_against,
                }
            return {
                "phase": "底部（早期）",
                "reason": f"PB处于{pb_pct}%低位，等待技术面确认",
                "evidence_for": evidence_for + [f"PB分位{pb_pct}%"],
                "evidence_against": evidence_against + ["均线偏弱"],
            }

        if anchor_pct is not None and anchor_pct > 75:
            if div_bear:
                evidence_against.append("顶背离信号")
            if rsi > 70:
                evidence_against.append(f"RSI超买({rsi:.0f})")
            return {
                "phase": "顶部",
                "reason": f"PB处于{pb_pct}%高分位",
                "evidence_for": evidence_for,
                "evidence_against": evidence_against,
            }

        # 中位区域
        if ma_status == "多头排列":
            return {
                "phase": "复苏",
                "reason": f"PB分位{anchor_pct}%，趋势向好",
                "evidence_for": evidence_for,
                "evidence_against": evidence_against,
            }
        if ma_status == "空头排列":
            return {
                "phase": "下降（深度）",
                "reason": f"PB分位{anchor_pct}%，趋势偏弱",
                "evidence_for": evidence_for,
                "evidence_against": evidence_against,
            }
        return {
            "phase": "下降（早期）",
            "reason": f"PB分位{anchor_pct}%",
            "evidence_for": evidence_for,
            "evidence_against": evidence_against,
        }

    # ================================================================
    # 3. ETF 周期判断（价格分位）
    # ================================================================
    if price_pct is not None:
        # 筑底确认检测
        basing_signals = []
        if price_pct <= 20:
            basing_signals.append(f"价格3年{price_pct}%低分位")
        if "缩量" in volume_trend:
            basing_signals.append("成交量萎缩")
        if div_bull:
            basing_signals.append("底背离确认")
        if 25 <= rsi <= 40:
            basing_signals.append(f"RSI({rsi:.0f})脱离极端超卖区")
        if ma_status == "交叉":
            basing_signals.append("均线走平")

        if len(basing_signals) >= 3 and price_pct is not None and price_pct <= 25:
            evidence_for.extend(basing_signals)
            return {
                "phase": "底部（确认）",
                "reason": "筑底确认：" + "；".join(basing_signals),
                "evidence_for": evidence_for,
                "evidence_against": evidence_against,
            }

        if price_pct <= 15:
            evidence_for.append(f"价格3年{price_pct}%低分位")
            if div_bull:
                return {
                    "phase": "底部（确认）",
                    "reason": f"价格3年{price_pct}%低分位，底背离确认",
                    "evidence_for": evidence_for,
                    "evidence_against": evidence_against,
                }
            return {
                "phase": "底部（早期）",
                "reason": f"价格3年{price_pct}%低分位，等待技术面确认",
                "evidence_for": evidence_for,
                "evidence_against": evidence_against,
            }

        if price_pct > 80:
            evidence_against.append(f"价格3年{price_pct}%高分位")
            if div_bear:
                evidence_against.append("顶背离")
            return {
                "phase": "顶部",
                "reason": f"价格处于3年{price_pct}%高分位",
                "evidence_for": evidence_for,
                "evidence_against": evidence_against,
            }

    # 回撤判断
    if dd is not None and dd < -40:
        if "缩量" in volume_trend:
            return {
                "phase": "底部（早期）",
                "reason": f"回撤{dd}%，成交量萎缩，可能在筑底",
                "evidence_for": evidence_for,
                "evidence_against": evidence_against,
            }
        return {
            "phase": "下降（深度）",
            "reason": f"回撤{dd}%，需等待企稳",
            "evidence_for": evidence_for,
            "evidence_against": evidence_against,
        }

    if ma_status == "多头排列":
        return {
            "phase": "复苏",
            "reason": "趋势向好，均线多头排列",
            "evidence_for": evidence_for,
            "evidence_against": evidence_against,
        }
    if ma_status == "空头排列":
        return {
            "phase": "下降（早期）",
            "reason": "趋势偏弱，均线空头排列",
            "evidence_for": evidence_for,
            "evidence_against": evidence_against,
        }

    return {
        "phase": "未知",
        "reason": "数据不足以判断",
        "evidence_for": evidence_for,
        "evidence_against": evidence_against,
    }


# ============================================================
# 入场点计算 + 推荐行动
# ============================================================

RECOMMENDATION_MAP = {
    "底部（确认）": "买入",
    "底部（早期）": "观望",
    "复苏": "买入",
    "顶部": "卖出",
    "下降（早期）": "观望",
    "下降（深度）": "观望",
    "接飞刀": "观望",
    "未知": "观望",
}


def _calc_entry_points(kline: list, technical: dict, phase: str) -> dict:
    """从 K 线和技术指标计算具体的入场/止损价位"""
    closes = np.array([b["close"] for b in kline])
    current = closes[-1]
    ma_values = technical.get("ma_values", {})

    # 前低：过去 60 个交易日内最低收盘价（排除今天）
    if len(closes) >= 61:
        recent_low = float(closes[-61:-1].min())
    elif len(closes) >= 2:
        recent_low = float(closes[-2:].min())
    else:
        recent_low = float(closes[-1])

    # 近期低点（40日）：判断是否企稳
    if len(closes) >= 40:
        low_40d = float(closes[-40:].min())
    else:
        low_40d = recent_low

    # 均线水平
    ma20 = ma_values.get("ma20", current)
    ma60 = ma_values.get("ma60", current)

    entry = {}

    if phase == "底部（早期）":
        entry = {
            "observation": {
                "description": "左侧观察仓",
                "price": round(current, 2),
                "condition": "当前即可买入1手（100股）",
            },
            "first_entry": {
                "description": "第一批 1/3 — 底部确认",
                "trigger": "底背离出现 或 成交量萎缩至20日均量70%以下",
                "price_hint": f"当前价 {current:.2f} 附近",
            },
            "second_entry": {
                "description": "第二批 1/3 — 右侧趋势确认",
                "trigger": f"收盘站稳 MA20({ma20:.2f}) 且 MA20 拐头向上",
                "price_hint": f"约 {ma20:.2f} 以上",
            },
            "third_entry": {
                "description": "第三批 1/3 — 右侧回调确认",
                "trigger": "趋势确认后，回踩 MA20 或 MA60 不破前低",
                "price_hint": f"约 {ma20:.2f} ~ {ma60:.2f} 区间",
            },
            "stop_loss": {
                "price": round(recent_low * 0.95, 2),
                "description": f"跌破前低 {recent_low:.2f} 的5%以下",
            },
        }

    elif phase == "底部（确认）":
        entry = {
            "first_entry": {
                "description": "第一批 1/3 — 当前即可建仓",
                "price": round(current, 2),
            },
            "second_entry": {
                "description": "第二批 1/3 — 右侧突破确认",
                "trigger": f"收盘站稳 MA20({ma20:.2f}) 且成交量放大",
                "price_hint": f"约 {ma20:.2f} 以上",
            },
            "third_entry": {
                "description": "第三批 1/3 — 右侧首次回调",
                "trigger": "趋势确认后首次回踩 MA20 不破",
                "price_hint": f"约 {ma20:.2f} ~ {ma60:.2f} 区间",
            },
            "stop_loss": {
                "price": round(recent_low * 0.95, 2),
                "description": f"跌破前低 {recent_low:.2f}",
            },
        }

    elif phase == "复苏":
        entry = {
            "add_position": {
                "description": "可加仓至 2/3",
                "price": round(current, 2),
            },
            "pullback_add": {
                "description": "回调加仓至满仓",
                "trigger": f"回踩 MA60({ma60:.2f}) 不破",
                "price_hint": f"约 {ma60:.2f} 附近",
            },
            "trailing_stop": {
                "price": round(ma60, 2),
                "description": f"跌破 MA60({ma60:.2f}) 止盈",
            },
        }

    else:
        # 顶部/下降/接飞刀 — 不给出场点
        entry = None

    return entry


def _build_recommendation(phase: str, technical: dict,
                          fundamental: dict, kline: list = None) -> dict:
    """生成推荐行动 + 交易策略（含具体价位）"""
    action = RECOMMENDATION_MAP.get(phase, "观望")

    # 阶段描述
    stage_descriptions = {
        "底部（确认）": "可分批建仓 — 估值极低 + 技术企稳",
        "底部（早期）": "左侧观察期 — 估值便宜但趋势未转",
        "复苏": "可加仓 — 趋势向好",
        "顶部": "减仓/清仓 — 估值高位",
        "下降（早期）": "不参与 — 趋势转弱",
        "下降（深度）": "密切关注 — 估值快速回落",
        "接飞刀": "坚决不买 — 基本面恶化",
        "未知": "信息不足",
    }
    trading_stage = stage_descriptions.get(phase, "信息不足")

    # 计算入场点
    entry_points = None
    if kline:
        entry_points = _calc_entry_points(kline, technical, phase)

    # 风险提示
    risk_notes = []
    if fundamental.get("survivability") == "弱":
        risk_notes.append("生存力弱，需关注财务风险")
    if technical.get("divergence_bearish"):
        risk_notes.append("顶背离，注意回调")
    if technical.get("rsi", 50) < 20:
        risk_notes.append("RSI极端超卖，短期可能继续杀跌")
    if phase == "底部（早期）" and technical.get("ma_status") == "空头排列":
        risk_notes.append("趋势仍在下行，左侧建仓需严格止损")

    return {
        "action": action,
        "trading_stage": trading_stage,
        "entry_points": entry_points,
        "risk_notes": risk_notes if risk_notes else None,
    }


# ============================================================
# 主分析函数
# ============================================================

def analyze_one(target: dict) -> dict:
    """分析单个标的 → JSON（无机械评分）"""
    symbol = target["symbol"]
    name = target["name"]
    ttype = target.get("type", "stock")
    is_etf = ttype == "etf"

    kline = load_kline(symbol)
    if not kline:
        return {"error": f"{name}: 无K线数据", "symbol": symbol, "name": name}

    financials = load_financials(symbol) if not is_etf else None
    indicator = load_indicator(symbol) if not is_etf else None
    current_price = kline[-1]["close"]

    valuation = analyze_valuation(kline, financials, is_etf, indicator)
    weekly = load_weekly_kline(symbol)
    technical = analyze_technical(kline, weekly)
    fundamental = analyze_fundamental(financials, is_etf)
    macro = analyze_macro()
    cycle = classify_cycle(valuation, technical, fundamental, is_etf)
    recommendation = _build_recommendation(cycle["phase"], technical, fundamental, kline)
    holding = compute_holding(symbol, current_price)

    return {
        "symbol": symbol,
        "name": name,
        "type": ttype,
        "sector": target.get("sector", ""),
        "price": round(current_price, 4),
        "date": str(date.today()),
        "valuation": valuation,
        "technical": technical,
        "fundamental": fundamental,
        "macro": macro,
        "cycle_phase": cycle["phase"],
        "cycle_phase_reason": cycle["reason"],
        "cycle_evidence_for": cycle["evidence_for"],
        "cycle_evidence_against": cycle["evidence_against"],
        "recommendation": recommendation["action"],
        "trading_stage": recommendation["trading_stage"],
        "entry_points": recommendation["entry_points"],
        "risk_notes": recommendation["risk_notes"],
        "holding": holding,
    }


def analyze_all() -> list:
    """分析全部关注标的"""
    config = load_config()
    targets = config.get("targets", [])
    results = []
    for t in targets:
        result = analyze_one(t)
        results.append(result)
        if "error" not in result:
            print(f"  {result['name']}({result['symbol']}) "
                  f"| {result['cycle_phase']} "
                  f"| RSI{result['technical']['rsi']:.0f} "
                  f"| PB分位{result['valuation']['pb_pct']}% "
                  f"| {result['recommendation']}")
        else:
            print(f"  {result['name']}: {result['error']}")
    return results


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="周期分析")
    parser.add_argument("--all", action="store_true", help="全量分析")
    parser.add_argument("--symbol", type=str, help="单标的分析")
    args = parser.parse_args()

    config = load_config()
    targets = config.get("targets", [])

    if args.symbol:
        target = next((t for t in targets if t["symbol"] == args.symbol), None)
        if not target:
            print(json.dumps({"error": f"未找到标的: {args.symbol}"}, ensure_ascii=False))
            sys.exit(1)
        result = analyze_one(target)
        print(json.dumps(result, ensure_ascii=False, default=str))
    elif args.all:
        print("=" * 60)
        print("  Cycle Laboratory - 周期分析")
        print("=" * 60)
        results = analyze_all()
        print("\n" + json.dumps(results, ensure_ascii=False, default=str))
    else:
        results = analyze_all()
        print("\n" + json.dumps(results, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
