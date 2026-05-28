# Cycle Laboratory (CL)

周期价值投资实验室。以 Claude Code Skills 为交互界面，Python 脚本为数据工具。

## 项目理念

- **Claude 是 AI 判断层**：定量数据由脚本产出，定性分析由 Claude 在对话中完成
- **脚本只做数据**：fetch 拉数据，analyze 出 JSON，scan 筛标的。不生成报告、不调 LLM API
- **一标的一 skill**：每个关注标的独有分析逻辑（行业周期、关键指标、风险特征）

## 快速上手

新会话只需记住三件事：
1. **数据在 `data/*.parquet`**，已缓存，无需重新拉（除非需要最新数据）
2. **分析用 `scripts/analyze.py`**，输出 JSON（无评分，输出周期阶段+交易策略）
3. **持仓在 `ledger.yaml`**：AI 给出买入/卖出建议后，按"用户已采纳"直接记录
4. **所有交互通过 skills**：`/check` 体检、`/scan` 扫市场、`/酒ETF-512690` 等标的专属分析

## 可用的 Skills

| Skill | 用途 |
|-------|------|
| `/check` | 全部关注标的快速体检。每日 15:02 自动执行。 |
| `/scan` | 全市场扫描（沪深300+中证500），发掘不在 watchlist 的新标的 |
| `/target-酒ETF-512690` | 酒ETF 专属深度分析（白酒周期、渠道库存、茅台批价） |
| `/target-牧原股份-002714` | 牧原股份 猪周期分析（能繁母猪、猪粮比） |
| `/target-紫金矿业-601899` | 紫金矿业 铜金资源周期分析 |
| `/target-沪深300ETF-510300` | 沪深300ETF 宏观周期分析 |
## 脚本用法

```bash
# 更新数据（默认全量，缓存有效则跳过）
python3 scripts/fetch.py --all --force

# 全量分析 → JSON 数组
python3 scripts/analyze.py --all 2>&1 | tail -1

# 单标的分析 → JSON
python3 scripts/analyze.py --symbol 512690 2>&1 | tail -1

# 全市场扫描 → JSON（扫描不在 watchlist 的候选）
python3 scripts/scan.py --top 30 2>&1 | tail -1
```

注意：`analyze.py` 和 `scan.py` 的 stdout 包含进度输出，只有最后一行是 JSON。用 `tail -1` 提取。

## 分析 JSON 字段说明

无评分体系，`analyze.py` 通过 `classify_cycle()` 做规则分类，输出周期阶段 + 交易策略。

```json
{
  "symbol": "512690", "name": "酒ETF", "type": "etf", "sector": "白酒",
  "price": 0.443, "date": "2026-05-29",
  "valuation": {
    "pb_pct": 0.0,              // PB 3年分位（ETF 为 null）
    "pe_pct": null,             // PE 3年分位（ETF 为 null）
    "price_pct_3y": 0.0,        // 价格 3年分位，越低越便宜
    "price_pct_5y": 0.0,        // 价格 5年分位
    "drawdown_3y": -48.0,       // 3年最大回撤
    "drawdown_days": 658        // 回撤持续天数
  },
  "technical": {
    "rsi": 28.0,                // RSI(14)
    "ma_status": "空头排列",     // 多头排列 / 空头排列 / 交叉
    "ma_values": {"ma20": 0.46, "ma60": 0.49, "ma120": 0.52, "ma250": 0.55},
    "price_vs_ma": {"ma20": -4.3, ...},  // 现价偏离均线的百分比
    "macd_daily": "中性",       // MACD 日线：金叉/死叉/中性
    "macd_weekly": "中性",      // MACD 周线：金叉/死叉/中性
    "volume_trend": "下跌中正常量",
    "divergence_bullish": false, // RSI 底背离
    "divergence_bearish": false  // RSI 顶背离
  },
  "fundamental": {
    "debt_ratio": 54.1,         // 资产负债率（ETF 为 null）
    "current_ratio": 0.82,      // 流动比率
    "cash_flow_positive_q": 3,  // 近 N 季度经营现金流为正
    "roe_current": -1.6,        // 最近季度 ROE
    "roe_trend": "稳定",        // ROE 趋势：改善/稳定/恶化/无数据
    "revenue_yoy_trend": "正增长",
    "revenue_declining_q": 3,   // 连续营收下滑季度数
    "survivability": "中等"     // 生存力评级：强/中等/弱
  },
  "macro": {
    "csi300_phase": "顶部",     // 沪深300 当前周期阶段
    "csi300_price_pct_3y": 99.0 // 沪深300 3年价格分位
  },
  "cycle_phase": "底部（早期）",
  "cycle_phase_reason": "价格3年0.0%低分位，等待技术面确认",
  "cycle_evidence_for": ["价格3年0.0%低分位"],
  "cycle_evidence_against": [],
  "recommendation": "观望",
  "trading_stage": "左侧观察期 — 估值便宜但趋势未转",
  "entry_points": {
    "observation": {"price": 0.44, "condition": "当前即可买入1手（100股）"},
    "first_entry": {"trigger": "底背离出现 或 成交量萎缩", "price_hint": "0.44 附近"},
    "second_entry": {"trigger": "收盘站稳 MA20 且 MA20 拐头向上", "price_hint": "0.46 以上"},
    "stop_loss": {"price": 0.42, "description": "跌破前低 5%"}
  },
  "risk_notes": ["趋势仍在下行，左侧建仓需严格止损"],
  "holding": {
    "held": false, "shares": 0, "total_cost": 0,
    "avg_cost": null, "pnl": 0, "pnl_pct": null, "trades": []
  }
}
```

字段要点：
- `valuation` 以 PB/PE/价格 分位 + 回撤为核心，不做评分
- `technical` 无评分，直接输出 RSI、均线排列状态、MACD 方向、背离信号
- `fundamental` 侧重"生存力"（负债率、现金流、ROE 趋势），不单独给分
- `entry_points` 仅在底部/复苏阶段输出，包含三批建仓策略 + 止损位
- `holding` 来自 `ledger.yaml` 的假设交易记录（FIFO 简化模型）

## 周期阶段说明

| 阶段 | 含义 | 操作方向 |
|------|------|---------|
| 底部（确认） | 估值极低 + 技术企稳 + 成交量萎缩 | 可分批建仓 |
| 底部（早期） | 估值极低但技术面未确认 | 观察，等待信号 |
| 复苏 | 估值低位 + 趋势向好 | 可加仓 |
| 顶部 | 估值高位 | 减仓/不参与 |
| 下降（早期） | 趋势转弱 | 观望 |
| 下降（深度） | 大幅下跌 | 关注但不出手 |
| 接飞刀 | 基本面恶化 + 继续下跌 | 坚决不买 |

## 周期分类逻辑

`classify_cycle()` 采用层级规则分类，无加权评分。

**个股分类规则（PB 分位优先）：**

| 条件 | 阶段 |
|------|------|
| 生存力"弱" + PB > 50%分位 + 非多头排列 | 接飞刀 |
| PB < 5%分位 + (多头排列 或 底背离) | 底部（确认） |
| PB < 20%分位 或 价格 < 20%分位 | 底部（早期） |
| PB 20-40%分位 + 多头排列 | 复苏 |
| PB > 80%分位 或 价格 > 80%分位 | 顶部 |
| 3年回撤 > 30% + 非底部阶段 | 下降（深度） |
| 以上皆不满足 | 下降（早期） |

**ETF 分类规则（价格分位 + 技术确认）：**
- 逻辑类似，但用价格分位替代 PB 分位，增加成交量/RSI/MACD 辅助判定

**recommendation 映射：**

| 阶段 | recommendation |
|------|---------------|
| 底部（确认）、复苏 | 买入 |
| 底部（早期）、下降（早期）、下降（深度）、接飞刀 | 观望 |
| 顶部 | 卖出 |

入场点（`entry_points`）仅在底部/复苏阶段输出，包含三批建仓 + 止损位。

## 用户投资风格

- 周期价值投资者，擅长趋势判断和机会发掘
- 已知弱点：看好后过度乐观、喜欢猜底、容易一次性满仓
- **分析时必须**：给出反方观点、提醒仓位纪律、区分"便宜"和"价值陷阱"

## 数据源

- ETF 日线：Sina Finance（akshare `fund_etf_hist_sina`）
- 个股日线：Sina Finance（akshare `stock_zh_a_daily`），前复权，有成交量
- 财务数据：腾讯财经（akshare `stock_financial_abstract`），季度数据
- 财务指标：同花顺（akshare `stock_financial_abstract_ths`），EPS/BVPS/ROE
- 指数行情：腾讯财经（akshare `stock_zh_index_daily_tx`）
- 周线：日线 `resample("W")` 聚合生成
- 数据缓存：`data/*.parquet`，交易时间当天过期，非交易时间 24h 过期

## 扫描范围（scan.py）

- 沪深300 + 中证500 成分股（~800 只行业龙头+大盘股）
- 筛选条件：市值 > 100 亿，0 < PE < 25，排除已在 watchlist 的标的
- 按 PE 从低到高排序，市值从大到小
