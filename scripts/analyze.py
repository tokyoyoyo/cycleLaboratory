#!/usr/bin/env python3
"""量化分析脚本 - 估值/技术/基本面/周期检测/综合打分

用法:
  python scripts/analyze.py --all       # 全量分析 → JSON
  python scripts/analyze.py --symbol 512690  # 单标的分析 → JSON
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
    """加载 watchlist"""
    with open(os.path.join(ROOT, "watchlist.yaml"), "r") as f:
        return yaml.safe_load(f)


# ============================================================
# 技术分析
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


def analyze_technical(kline: list, weekly_kline: Optional[list] = None) -> dict:
    """技术分析 → dict"""
    ma_periods = [20, 60, 120, 250]
    rsi_period = 14
    macd_fast, macd_slow, macd_signal = 12, 26, 9
    vol_short, vol_long = 20, 60

    closes = np.array([b["close"] for b in kline])
    volumes = np.array([b.get("volume", 0) for b in kline])
    current = closes[-1]

    # --- 均线 ---
    ma_scores = []
    for period in ma_periods:
        if len(closes) < period:
            continue
        ma = _calc_ma(closes, period)
        ma_val = ma[-1]
        if np.isnan(ma_val):
            continue
        diff_pct = (current - ma_val) / ma_val * 100
        if diff_pct > 5:
            ma_scores.append(80)
        elif diff_pct > 0:
            ma_scores.append(60)
        elif diff_pct > -5:
            ma_scores.append(40)
        elif diff_pct > -10:
            ma_scores.append(20)
        else:
            ma_scores.append(10)

    # 均线排列
    if len(ma_periods) >= 3:
        mas = []
        for p in ma_periods[:3]:
            ma = _calc_ma(closes, p)
            if not np.isnan(ma[-1]):
                mas.append(ma[-1])
        if len(mas) >= 3:
            if mas[0] > mas[1] > mas[2]:
                ma_scores.append(90)
            elif mas[0] < mas[1] < mas[2]:
                ma_scores.append(10)
            else:
                ma_scores.append(45)

    ma_score = float(np.mean(ma_scores)) if ma_scores else 50.0

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

    # RSI 评分 — 分段线性插值
    rsi_score = 50.0
    breakpoints = [
        (0, 95), (10, 90), (20, 80), (30, 60),
        (45, 50), (55, 50), (70, 35), (80, 15), (100, 5),
    ]
    for i in range(len(breakpoints) - 1):
        x0, y0 = breakpoints[i]
        x1, y1 = breakpoints[i + 1]
        if x0 <= rsi <= x1:
            t = (rsi - x0) / (x1 - x0)
            rsi_score = y0 + (y1 - y0) * t
            break

    # --- MACD ---
    macd_score = 50.0
    if len(closes) >= macd_slow + macd_signal:
        ema_fast = _calc_ema(closes, macd_fast)
        ema_slow = _calc_ema(closes, macd_slow)
        dif = ema_fast - ema_slow
        dea = _calc_ema(dif, macd_signal)
        macd_hist = 2 * (dif - dea)

        if len(dif) >= 3 and len(dea) >= 3:
            if dif[-2] <= dea[-2] and dif[-1] > dea[-1]:
                macd_score += 25
            elif dif[-2] >= dea[-2] and dif[-1] < dea[-1]:
                macd_score -= 25

        if len(macd_hist) >= 5:
            if macd_hist[-1] > macd_hist[-5]:
                macd_score += 10
            else:
                macd_score -= 10

        if dif[-1] < 0:
            macd_score += 10
        if dif[-1] > 0 and dif[-1] > dif[-5]:
            macd_score += 5

    macd_score = max(0, min(100, macd_score))

    # --- 周线 MACD ---
    macd_weekly_signal = "neutral"
    if weekly_kline and len(weekly_kline) >= macd_slow + macd_signal:
        weekly_closes = np.array([b["close"] for b in weekly_kline])
        w_ema_fast = _calc_ema(weekly_closes, macd_fast)
        w_ema_slow = _calc_ema(weekly_closes, macd_slow)
        w_dif = w_ema_fast - w_ema_slow
        w_dea = _calc_ema(w_dif, macd_signal)
        if len(w_dif) >= 3 and len(w_dea) >= 3:
            if w_dif[-2] <= w_dea[-2] and w_dif[-1] > w_dea[-1]:
                macd_weekly_signal = "golden_cross"
                macd_score += 15
            elif w_dif[-2] >= w_dea[-2] and w_dif[-1] < w_dea[-1]:
                macd_weekly_signal = "death_cross"
                macd_score -= 15
        if len(w_dif) >= 2 and w_dif[-1] > 0 and w_dif[-1] > w_dif[-2]:
            macd_score += 5

    macd_score = max(0, min(100, macd_score))

    # --- 成交量 ---
    volume_score = 50.0
    vol_sum = np.sum(volumes) if len(volumes) > 0 else 0
    if vol_sum > 0 and len(volumes) >= vol_long:
        vol_short_avg = np.mean(volumes[-vol_short:])
        vol_long_avg = np.mean(volumes[-vol_long:])
        vol_ratio = vol_short_avg / vol_long_avg if vol_long_avg > 0 else 1.0
        price_falling = closes[-1] < closes[-20] if len(closes) >= 20 else False

        if price_falling:
            if vol_ratio < 0.7:
                volume_score += 30
            elif vol_ratio < 0.85:
                volume_score += 15
            elif vol_ratio > 1.3:
                volume_score -= 25
        else:
            if 0.8 < vol_ratio < 1.5:
                volume_score += 15
            elif vol_ratio > 2.0:
                volume_score -= 10

        if len(volumes) >= 60:
            vol_trend = volumes[-20:].mean() / (volumes[-60:-20].mean() + 1e-10)
            if price_falling and vol_trend < 0.8:
                volume_score += 10

    volume_score = max(0, min(100, volume_score))

    # --- 背离检测 ---
    div_bull = False
    div_bear = False
    if len(closes) >= 60:
        # 底背离
        rsi_now = rsi
        if len(closes) > 20 + rsi_period:
            rsi_20d_ago_seg = closes[:-(20 + rsi_period)]
            if len(rsi_20d_ago_seg) >= rsi_period + 1:
                deltas2 = np.diff(closes[:-20])
                g2 = np.where(deltas2 > 0, deltas2, 0)
                l2 = np.where(deltas2 < 0, -deltas2, 0)
                ag2 = np.mean(g2[-rsi_period:]) if len(g2) >= rsi_period else 0
                al2 = np.mean(l2[-rsi_period:]) if len(l2) >= rsi_period else 0
                if al2 > 0:
                    rsi_20d_ago = float(100 - 100 / (1 + ag2 / al2))
                else:
                    rsi_20d_ago = 100.0 if ag2 > 0 else 50.0

                price_40d_low = closes[-40:-20].min() if len(closes) >= 40 else closes[-20:].min()
                if closes[-1] <= price_40d_low * 1.02 and rsi_now > rsi_20d_ago + 5:
                    div_bull = True

        # 顶背离
        if len(closes) > 20 + rsi_period:
            rsi_20d_ago2_seg = closes[:-(20 + rsi_period)]
            if len(rsi_20d_ago2_seg) >= rsi_period + 1:
                deltas3 = np.diff(closes[:-20])
                g3 = np.where(deltas3 > 0, deltas3, 0)
                l3 = np.where(deltas3 < 0, -deltas3, 0)
                ag3 = np.mean(g3[-rsi_period:]) if len(g3) >= rsi_period else 0
                al3 = np.mean(l3[-rsi_period:]) if len(l3) >= rsi_period else 0
                rsi_20d_ago2 = float(100 - 100 / (1 + ag3 / al3)) if al3 > 0 else 100.0

                price_40d_high = closes[-40:-20].max() if len(closes) >= 40 else closes[-20:].max()
                if closes[-1] >= price_40d_high * 0.98 and rsi_now < rsi_20d_ago2 - 5:
                    div_bear = True

    tech_score = ma_score * 0.35 + rsi_score * 0.25 + macd_score * 0.20 + volume_score * 0.20

    return {
        "rsi_value": round(rsi, 1),
        "ma_score": round(ma_score, 1),
        "rsi_score": round(rsi_score, 1),
        "macd_score": round(macd_score, 1),
        "volume_score": round(volume_score, 1),
        "divergence_bullish": div_bull,
        "divergence_bearish": div_bear,
        "macd_weekly_signal": macd_weekly_signal,
        "score": round(tech_score, 1),
    }


# ============================================================
# 估值分析
# ============================================================

def analyze_valuation(kline: list, financials: Optional[list],
                      is_etf: bool = False,
                      indicator: Optional[list] = None) -> dict:
    """估值分析 → dict"""
    closes = np.array([b["close"] for b in kline])
    dts = [b["date"] for b in kline]
    current_price = closes[-1]

    pe_pct = None
    pb_pct = None

    if not is_etf and indicator and len(indicator) >= 8:
        # --- 真实 PE 分位：从季度 EPS + 价格计算 ---
        # 构建价格查找表 {date: close}
        price_map = {}
        for b in kline:
            price_map[b["date"]] = b["close"]

        # 为每个报告期匹配最近的价格
        def _find_price(report_date):
            if report_date in price_map:
                return price_map[report_date]
            best_date, best_price = None, None
            for d, p in price_map.items():
                if d <= report_date and (best_date is None or d > best_date):
                    best_date, best_price = d, p
            return best_price

        # 按日期排序 indicator 数据
        ind_sorted = sorted(indicator, key=lambda x: x["date"])

        # 计算每个报告期的 TTM EPS 和 TTM PE
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
            # 当前 TTM PE
            current_window = ind_sorted[-4:]
            current_ttm_eps = sum(w["eps"] for w in current_window)
            if current_ttm_eps > 0:
                current_pe = current_price / current_ttm_eps
                pe_array = np.array(historical_pe)
                pe_pct = (pe_array < current_pe).sum() / len(pe_array) * 100

        # PB 分位（价格分位近似）
        pb_pct = (closes < current_price).sum() / len(closes) * 100

    # 价格分位
    price_pct_3y = None
    cutoff_3y = date.today() - timedelta(days=3 * 365)
    mask_3y = np.array([d >= cutoff_3y for d in dts])
    if mask_3y.sum() >= 100:
        filtered_3y = closes[mask_3y]
        price_pct_3y = (filtered_3y < current_price).sum() / len(filtered_3y) * 100

    price_pct_5y = None
    cutoff_5y = date.today() - timedelta(days=5 * 365)
    mask_5y = np.array([d >= cutoff_5y for d in dts])
    if mask_5y.sum() >= 100:
        filtered_5y = closes[mask_5y]
        price_pct_5y = (filtered_5y < current_price).sum() / len(filtered_5y) * 100

    # 回撤
    dd_3y = None
    dd_days = None
    if mask_3y.sum() >= 100:
        filtered = closes[mask_3y]
        peak = np.maximum.accumulate(filtered)
        dd = (filtered - peak) / peak * 100
        dd_3y = float(dd[-1])
        dd_start = None
        for i in range(len(dd) - 1, -1, -1):
            if dd[i] > -1:
                dd_start = i
                break
        dd_days = len(dd) - dd_start if dd_start is not None else len(dd)

    # 估值评分
    val_score = 50.0
    if pe_pct is not None:
        if pe_pct <= 15:
            val_score += 30
        elif pe_pct <= 25:
            val_score += 20
        elif pe_pct >= 75:
            val_score -= 25
    if pb_pct is not None:
        if pb_pct <= 20:
            val_score += 10
        elif pb_pct >= 75:
            val_score -= 15
    if dd_3y is not None:
        if dd_3y <= -50:
            val_score += 10
        elif dd_3y <= -30:
            val_score += 5
        elif dd_3y >= -10:
            val_score -= 5
    if price_pct_3y is not None:
        if price_pct_3y <= 15:
            val_score += 10
        elif price_pct_3y >= 85:
            val_score -= 20

    val_score = max(0, min(100, val_score))

    return {
        "pe_percentile": round(pe_pct, 1) if pe_pct is not None else None,
        "pb_percentile": round(pb_pct, 1) if pb_pct is not None else None,
        "price_pct_3y": round(price_pct_3y, 1) if price_pct_3y is not None else None,
        "price_pct_5y": round(price_pct_5y, 1) if price_pct_5y is not None else None,
        "drawdown_3y": round(dd_3y, 1) if dd_3y is not None else None,
        "drawdown_days": dd_days,
        "score": round(val_score, 1),
    }


# ============================================================
# 基本面分析
# ============================================================

def analyze_fundamental(financials: Optional[list], is_etf: bool = False) -> dict:
    """基本面分析 → dict"""
    if not financials:
        if is_etf:
            return {
                "roe_current": None, "roe_stability": 60.0,
                "revenue_trend": 55.0, "balance_sheet": 60.0,
                "roe_positive_q": 4, "revenue_declining_q": 0,
                "debt_ratio": None, "score": 58.0,
            }
        return {
            "roe_current": None, "roe_stability": 50.0,
            "revenue_trend": 50.0, "balance_sheet": 50.0,
            "roe_positive_q": 0, "revenue_declining_q": 0,
            "debt_ratio": None, "score": 50.0,
        }

    sorted_fin = sorted(financials, key=lambda x: x["report_date"])

    # ROE 分析
    roes = np.array([f["roe"] for f in sorted_fin[-12:]])
    roes = roes[roes != 0]
    roe_score = 50.0
    if len(roes) >= 4:
        avg_roe = np.mean(roes)
        if avg_roe > 20:
            roe_score += 20
        elif avg_roe > 15:
            roe_score += 15
        elif avg_roe > 10:
            roe_score += 10
        elif avg_roe > 5:
            roe_score += 5
        elif avg_roe <= 0:
            roe_score -= 20

        std_roe = np.std(roes)
        if std_roe < 2:
            roe_score += 20
        elif std_roe < 5:
            roe_score += 10
        elif std_roe > 15:
            roe_score -= 15

        if len(roes) >= 8:
            recent_roe = np.mean(roes[-4:])
            older_roe = np.mean(roes[-8:-4])
            if recent_roe > older_roe * 1.1:
                roe_score += 10
            elif recent_roe < older_roe * 0.8:
                roe_score -= 10

        positive_count = (roes[-4:] > 0).sum()
        if positive_count >= 4:
            roe_score += 10
        elif positive_count >= 2:
            roe_score += 5
        else:
            roe_score -= 15

    roe_score = max(0, min(100, roe_score))
    roe_positive_q = int((roes[-4:] > 0).sum()) if len(roes) >= 4 else 0
    roe_current = float(roes[-1]) if len(roes) > 0 else None

    # 营收趋势
    revenues = np.array([f["revenue_yoy"] for f in sorted_fin[-12:]])
    revenues = revenues[~np.isnan(revenues)]
    rev_score = 50.0
    if len(revenues) >= 4:
        avg_growth = np.mean(revenues[-4:])
        if avg_growth > 20:
            rev_score += 15
        elif avg_growth > 10:
            rev_score += 10
        elif avg_growth > 0:
            rev_score += 5
        elif avg_growth > -10:
            rev_score -= 10
        else:
            rev_score -= 20

        recent_4 = revenues[-4:]
        improving = sum(1 for i in range(1, len(recent_4)) if recent_4[i] > recent_4[i - 1])
        if improving >= 3:
            rev_score += 10
        elif improving >= 2:
            rev_score += 5
        elif improving == 0:
            rev_score -= 10

        if len(revenues) >= 8:
            std_rev = np.std(revenues[-8:])
            if std_rev < 5:
                rev_score += 10
            elif std_rev > 20:
                rev_score -= 5

    rev_score = max(0, min(100, rev_score))

    # 营收连续下滑
    recent_4_rev = [f["revenue_yoy"] for f in sorted_fin[-4:]]
    max_decline, current_decline = 0, 0
    for i in range(1, len(recent_4_rev)):
        if recent_4_rev[i] < recent_4_rev[i - 1]:
            current_decline += 1
            max_decline = max(max_decline, current_decline)
        else:
            current_decline = 0
    revenue_declining_q = max_decline

    # 资产负债
    recent_fin = sorted_fin[-4:]
    bs_score = 50.0

    debt_ratios = [f["debt_ratio"] for f in recent_fin if f["debt_ratio"] > 0]
    avg_debt = float(np.mean(debt_ratios)) if debt_ratios else None
    if debt_ratios:
        if avg_debt < 30:
            bs_score += 15
        elif avg_debt < 50:
            bs_score += 8
        elif avg_debt >= 70:
            bs_score -= 15

    current_ratios = [f["current_ratio"] for f in recent_fin if f["current_ratio"] > 0]
    if current_ratios:
        avg_cr = np.mean(current_ratios)
        if avg_cr > 2:
            bs_score += 15
        elif avg_cr > 1.5:
            bs_score += 10
        elif avg_cr > 1:
            bs_score += 5
        else:
            bs_score -= 10

    cash_flows = [f["op_cash_flow"] for f in recent_fin if f["op_cash_flow"] != 0]
    if cash_flows:
        positive_cf = sum(1 for cf in cash_flows if cf > 0)
        if positive_cf >= len(cash_flows) * 0.75:
            bs_score += 15
        elif positive_cf >= len(cash_flows) * 0.5:
            bs_score += 8
        else:
            bs_score -= 10

    bs_score = max(0, min(100, bs_score))

    # 综合
    fund_score = roe_score * 0.45 + rev_score * 0.30 + bs_score * 0.25

    return {
        "roe_current": round(roe_current, 1) if roe_current is not None else None,
        "roe_stability": round(roe_score, 1),
        "revenue_trend": round(rev_score, 1),
        "balance_sheet": round(bs_score, 1),
        "roe_positive_q": roe_positive_q,
        "revenue_declining_q": revenue_declining_q,
        "debt_ratio": round(avg_debt, 1) if avg_debt is not None else None,
        "score": round(fund_score, 1),
    }


# ============================================================
# 周期阶段分类
# ============================================================

def classify_cycle(valuation: dict, technical: dict,
                   fundamental: dict) -> tuple:
    """返回 (阶段名, 理由)"""
    pe_pct = valuation.get("pe_percentile")
    price_pct = valuation.get("price_pct_3y")
    dd = valuation.get("drawdown_3y")

    # 1. 接飞刀检测
    reasons = []
    if fundamental.get("roe_positive_q", 4) < 2:
        reasons.append(f"最近4季度仅{fundamental['roe_positive_q']}个季度ROE为正")
    if fundamental.get("revenue_declining_q", 0) >= 3:
        reasons.append(f"营收连续{fundamental['revenue_declining_q']}个季度下滑")
    if technical.get("volume_score", 50) < 30:
        reasons.append("下跌中放量，恐慌抛售未结束")
    if dd is not None and dd < -60:
        if technical.get("ma_score", 50) < 20:
            reasons.append("深度回撤且无技术企稳信号")
    if reasons:
        return ("接飞刀", "；".join(reasons))

    # 2. 有 PE 数据时
    if pe_pct is not None:
        if pe_pct <= 15:
            if technical.get("divergence_bullish") and technical.get("rsi_value", 50) > 35:
                return ("底部（确认）", f"PE处于{pe_pct:.1f}%极低分位，底背离确认，RSI企稳")
            if technical.get("volume_score", 50) > 60:
                return ("底部（确认）", f"PE处于{pe_pct:.1f}%极低分位，成交量萎缩筑底")
            return ("底部（早期）", f"PE处于{pe_pct:.1f}%极低分位，但技术面尚未确认底部")

        if pe_pct <= 25:
            if technical.get("ma_score", 50) > 50:
                return ("复苏", f"PE处于{pe_pct:.1f}%低位，价格站上均线，进入复苏阶段")
            if technical.get("divergence_bullish"):
                return ("底部（确认）", f"PE处于{pe_pct:.1f}%低位，出现底背离信号")
            return ("底部（早期）", f"PE处于{pe_pct:.1f}%低位，技术面偏弱")

        if pe_pct <= 75:
            if technical.get("ma_score", 50) > 60 and fundamental.get("roe_stability", 50) > 50:
                return ("复苏", f"PE处于{pe_pct:.1f}%中位，趋势向好")
            return ("下降（深度）", f"PE处于{pe_pct:.1f}%中位区域")

        rsi_val = technical.get("rsi_value", 50)
        if rsi_val > 70:
            return ("顶部", f"PE处于{pe_pct:.1f}%高位，RSI超买")
        return ("顶部", f"PE处于{pe_pct:.1f}%高位")

    # 3. 无 PE 数据时（ETF）用价格分位
    # 筑底确认
    basing_reasons = []
    if price_pct is not None and price_pct <= 20:
        basing_reasons.append(f"价格处于3年{price_pct:.1f}%低分位")
    if technical.get("volume_score", 50) > 60:
        basing_reasons.append("成交量显著萎缩，抛压衰竭")
    if technical.get("divergence_bullish"):
        basing_reasons.append("底背离信号确认")
    rsi_val = technical.get("rsi_value", 50)
    if 25 <= rsi_val <= 40:
        basing_reasons.append(f"RSI({rsi_val:.0f})脱离极端超卖区")
    ma_val = technical.get("ma_score", 50)
    if 35 <= ma_val <= 55:
        basing_reasons.append("均线开始走平，下跌动能减弱")
    if len(basing_reasons) >= 3 and price_pct is not None and price_pct <= 25:
        return ("底部（确认）", "筑底确认：" + "；".join(basing_reasons))

    if price_pct is not None:
        if price_pct <= 15:
            if technical.get("divergence_bullish"):
                return ("底部（确认）", f"价格处于3年{price_pct:.1f}%低分位，底背离确认")
            if technical.get("volume_score", 50) > 60:
                return ("底部（早期）", f"价格处于3年{price_pct:.1f}%低分位，成交量萎缩")
            return ("底部（早期）", f"价格处于3年{price_pct:.1f}%低分位，等待技术面确认")

        if price_pct > 80:
            return ("顶部", f"价格处于3年{price_pct:.1f}%高分位")

    if dd is not None and dd < -40:
        if technical.get("volume_score", 50) > 60:
            return ("底部（早期）", f"回撤{dd:.1f}%，成交量萎缩，可能在筑底")
        if technical.get("volume_score", 50) > 40:
            return ("下降（深度）", f"回撤{dd:.1f}%，成交量萎缩中")
        return ("下降（深度）", f"回撤{dd:.1f}%，需等待企稳")

    if ma_val > 60:
        return ("复苏", "趋势向好，均线多头排列")
    if ma_val < 40:
        return ("下降（早期）", "趋势偏弱，均线空头排列")

    return ("未知", "数据不足以判断周期阶段")


# ============================================================
# 综合打分
# ============================================================

def composite_score(val: dict, tech: dict, fund: dict,
                    kline: list = None) -> dict:
    """综合打分 → dict"""
    # 宏观背景
    macro_score = 50.0
    csi300 = load_csi300()
    if csi300 and kline and len(csi300) >= 20 and len(kline) >= 20:
        csi300_ret = (csi300[-1]["close"] - csi300[-20]["close"]) / csi300[-20]["close"] * 100
        target_ret = (kline[-1]["close"] - kline[-20]["close"]) / kline[-20]["close"] * 100
        relative = target_ret - csi300_ret
        if relative > 5:
            macro_score += 15
        elif relative > 0:
            macro_score += 5
        elif relative < -5:
            macro_score -= 15

    weights = {"valuation": 0.35, "technical": 0.30, "fundamental": 0.25, "macro": 0.10}
    score = (
        val["score"] * weights["valuation"] +
        tech["score"] * weights["technical"] +
        fund["score"] * weights["fundamental"] +
        macro_score * weights["macro"]
    )

    # 背离调整
    if tech.get("divergence_bullish"):
        score = min(100, score + 10)
    if tech.get("divergence_bearish"):
        score = max(0, score - 10)

    score = round(score, 1)

    # 建议文本
    if score >= 80:
        rec = "强烈买入 - 多因子共振"
    elif score >= 65:
        rec = "买入 - 处于良好入场区域，建议分批建仓"
    elif score >= 50:
        rec = "观察 - 估值有吸引力但技术面尚未确认"
    elif score >= 35:
        rec = "持有/减仓 - 不适宜新入场"
    else:
        rec = "回避 - 估值偏贵或基本面恶化"

    return {
        "composite_score": score,
        "macro_score": round(macro_score, 1),
        "recommendation": rec,
    }


# ============================================================
# 主分析函数
# ============================================================

def analyze_one(target: dict) -> dict:
    """分析单个标的 → JSON  dict"""
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
    phase, phase_reason = classify_cycle(valuation, technical, fundamental)
    scoring = composite_score(valuation, technical, fundamental, kline)

    # 持仓信息
    holding = {}
    if target.get("position_held") and target.get("cost_basis"):
        cost = target["cost_basis"]
        loss_pct = round((cost - current_price) / cost * 100, 1)
        holding = {
            "held": True,
            "cost_basis": cost,
            "loss_pct": loss_pct,
        }
    else:
        holding = {"held": False, "cost_basis": None, "loss_pct": None}

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
        "composite_score": scoring["composite_score"],
        "macro_score": scoring["macro_score"],
        "recommendation": scoring["recommendation"],
        "cycle_phase": phase,
        "cycle_phase_reason": phase_reason,
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
        # 简要终端输出
        if "error" not in result:
            print(f"  {result['name']}({result['symbol']}) "
                  f"评分{result['composite_score']:.0f} "
                  f"| {result['cycle_phase']} "
                  f"| RSI{result['technical']['rsi_value']:.0f}")
        else:
            print(f"  {result['name']}: {result['error']}")
    return results


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="量化分析")
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
        print("  Cycle Laboratory - 量化分析")
        print("=" * 60)
        results = analyze_all()
        print("\n" + json.dumps(results, ensure_ascii=False, default=str))
    else:
        # 默认 --all
        results = analyze_all()
        print("\n" + json.dumps(results, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
