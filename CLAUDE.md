# Cycle Laboratory (CL)

周期价值投资实验室。以 Claude Code Skills 为交互界面，Python 脚本为数据工具。

## 项目理念

- **Claude 是 AI 判断层**：定量数据由脚本产出，定性分析由 Claude 在对话中完成
- **脚本只做数据**：fetch 拉数据，analyze 出 JSON，scan 筛标的。不生成报告、不调 LLM API
- **一标的一 skill**：每个关注标的独有分析逻辑（行业周期、关键指标、风险特征）

## 快速上手

新会话只需记住三件事：
1. **数据在 `data/*.parquet`**，已缓存，无需重新拉（除非需要最新数据）
2. **分析用 `scripts/analyze.py`**，输出 JSON
3. **所有交互通过 skills**：`/check` 体检、`/scan` 扫市场、`/酒ETF-512690` 等标的专属分析

## 可用的 Skills

| Skill | 用途 |
|-------|------|
| `/check` | 全部关注标的快速体检。每日 15:27 自动执行。 |
| `/scan` | 全市场扫描（沪深300+中证500），发掘不在 watchlist 的新标的 |
| `/target-酒ETF-512690` | 酒ETF 专属深度分析（白酒周期、渠道库存、茅台批价） |
| `/target-牧原股份-002714` | 牧原股份 猪周期分析（能繁母猪、猪粮比） |
| `/target-紫金矿业-601899` | 紫金矿业 铜金资源周期分析 |
| `/target-赣锋锂业-002460` | 赣锋锂业 锂矿周期分析 |
| `/target-新希望-000876` | 新希望 猪周期+饲料分析 |
| `/target-沪深300ETF-510300` | 沪深300ETF 宏观周期分析 |
| `/target-中国海油-600938` | 中国海油 原油周期分析 |

## 脚本用法

```bash
# 更新数据（默认全量，缓存有效则跳过）
python scripts/fetch.py --all --force

# 全量分析 → JSON 数组
python scripts/analyze.py --all 2>&1 | tail -1

# 单标的分析 → JSON
python scripts/analyze.py --symbol 512690 2>&1 | tail -1

# 全市场扫描 → JSON（扫描不在 watchlist 的候选）
python scripts/scan.py --top 30 2>&1 | tail -1
```

注意：`analyze.py` 和 `scan.py` 的 stdout 包含进度输出，只有最后一行是 JSON。用 `tail -1` 提取。

## 分析 JSON 字段说明

```json
{
  "symbol": "512690", "name": "酒ETF", "type": "etf", "sector": "白酒",
  "price": 0.453, "date": "2026-05-27",
  "valuation": {
    "pe_percentile": null,       // ETF 无 PE 数据，用价格分位替代
    "price_pct_3y": 0.7,         // 3年价格分位，越低越便宜
    "drawdown_3y": -46.8,        // 3年最大回撤
    "score": 65.0                // 估值评分 0-100
  },
  "technical": {
    "rsi_value": 31.3,           // RSI(14)
    "ma_score": 18.0,            // 均线评分（多头排列=高分）
    "macd_score": 70.0,          // MACD 评分
    "volume_score": 50.0,        // 成交量评分（缩量=高分，放量下跌=低分）
    "divergence_bullish": false,  // 底背离
    "divergence_bearish": false,  // 顶背离
    "score": 45.1
  },
  "fundamental": {
    "roe_current": null,         // 最近季度 ROE（ETF 为 null）
    "roe_stability": 60.0,       // ROE 稳定性 0-100
    "debt_ratio": null,          // 资产负债率
    "score": 58.0
  },
  "composite_score": 54.3,       // 综合评分 0-100（≥65 买入，50-64 观察，<50 回避）
  "cycle_phase": "底部（早期）",  // 周期阶段
  "recommendation": "观察 - 估值有吸引力但技术面尚未确认",
  "holding": {
    "held": true, "cost_basis": 0.482, "loss_pct": 6.0
  }
}
```

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

## 综合评分逻辑

```
composite_score = 估值×0.35 + 技术×0.30 + 基本面×0.25 + 宏观×0.10
                  + 底背离+10 / 顶背离-10

≥80: 强烈买入    ≥65: 买入（分批）    50-64: 观察
35-49: 持有/减仓  <35: 回避
```

## 用户投资风格

- 周期价值投资者，擅长趋势判断和机会发掘
- 已知弱点：看好后过度乐观、喜欢猜底、容易一次性满仓
- **分析时必须**：给出反方观点、提醒仓位纪律、区分"便宜"和"价值陷阱"

## 数据源

- ETF 日线：Sina Finance（akshare `fund_etf_hist_sina`）
- 个股日线：腾讯财经（akshare `stock_zh_a_hist_tx`），无成交量数据
- 财务数据：腾讯财经（akshare `stock_financial_abstract`），季度数据
- 指数行情：腾讯财经（akshare `stock_zh_index_daily_tx`）
- 周线：日线 `resample("W")` 聚合生成
- 数据缓存：`data/*.parquet`，交易时间当天过期，非交易时间 24h 过期

## 扫描范围（scan.py）

- 沪深300 + 中证500 成分股（~800 只行业龙头+大盘股）
- 筛选条件：市值 > 100 亿，0 < PE < 25，排除已在 watchlist 的标的
- 按 PE 从低到高排序，市值从大到小
