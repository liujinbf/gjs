# 宏观数据源配置规格示例 (Macro Data Specs)

本文件提供了可以直接复制到系统中“宏观数据源规格”配置项的 JSON 示例。

## 1. 实时市场指标组 (Yahoo Finance)
这些数据源更新非常频繁（日频/盘中延迟），适合作为日内交易的背景趋势。

```json
[
  {
    "name": "美元指数 (DXY)",
    "provider": "yfinance",
    "symbol": "DX-Y.NYB",
    "importance": "high",
    "bias_mode": "higher_bearish",
    "symbols": ["XAUUSD", "XAGUSD", "EURUSD"],
    "notes": "美元走强通常压制贵金属和非美货币。"
  },
  {
    "name": "10年期美债收益率",
    "provider": "yfinance",
    "symbol": "^TNX",
    "importance": "medium",
    "bias_mode": "higher_bearish",
    "symbols": ["XAUUSD"],
    "notes": "实际利率上升（收益率上行）对黄金构成利空。"
  },
  {
    "name": "VIX 恐慌指数",
    "provider": "yfinance",
    "symbol": "^VIX",
    "importance": "medium",
    "bias_mode": "higher_bullish",
    "symbols": ["XAUUSD"],
    "notes": "市场恐慌情绪升温通常提振避险资产黄金。"
  }
]
```

## 2. 核心经济指标组 (Alpha Vantage)
相比 WorldBank，Alpha Vantage 提供的 CPI 和非农数据更及时（月频）。注：需要申请免费 API Key。

```json
[
  {
    "name": "美国核心 CPI",
    "provider": "alphavantage",
    "function": "CPI",
    "interval": "monthly",
    "importance": "high",
    "bias_mode": "higher_bearish",
    "symbols": ["XAUUSD", "EURUSD"],
    "api_key_env": "ALPHAVANTAGE_API_KEY"
  },
  {
    "name": "非农就业人数 (NFP)",
    "provider": "alphavantage",
    "function": "NONFARM_PAYROLL",
    "importance": "high",
    "bias_mode": "higher_bearish",
    "symbols": ["XAUUSD", "USDJPY"]
  }
]
```

## 3. 专业数据源组 (FRED)
美联储官方数据库，数据权威且支持数千个指标。

```json
[
  {
    "name": "联邦基金利率 (Upper)",
    "provider": "fred",
    "series_id": "DFEDTARU",
    "importance": "high",
    "bias_mode": "higher_bearish",
    "symbols": ["XAUUSD", "EURUSD"]
  },
  {
    "name": "10年期美债实际收益率",
    "provider": "fred",
    "series_id": "REAINTRATREARAT10Y",
    "importance": "high",
    "bias_mode": "higher_bearish",
    "symbols": ["XAUUSD"]
  }
]
```

## 配置说明
1. **api_key_env**: 如果配置了此项，系统会从环境变量中读取 Key（推荐）。
2. **bias_mode**: 
   - `higher_bearish`: 数值上行偏多（利空贵金属）。
   - `higher_bullish`: 数值上行偏多（利好资产）。
3. **symbols**: 定义该指标影响的交易品种，匹配后会优先在 UI 展示。
