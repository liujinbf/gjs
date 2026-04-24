# 贵金属监控终端

这是从原有综合项目中抽离出的独立 MVP，目标很明确：

- 只做贵金属 / 宏观品种监控
- 只用 MT5 数据源
- 只做报价、点差、宏观窗口与风险提醒
- 默认以监控与模拟盘为主；实盘自动交易能力已接入，但默认关闭，必须显式开启并承担真实资金风险
- AI 配置、模型预设和提示词资产已经在本项目内独立维护

## 当前覆盖品种

- `XAUUSD`
- `XAGUSD`
- `EURUSD`
- `USDJPY`

## 已具备的能力

- MT5 终端连接检测
- 实时报价获取
- `Bid / Ask / 点差` 展示
- 点差放大提醒
- 贵金属 / 外汇宏观窗口提醒
- 钉钉 / PushPlus 关键提醒推送
- 手动触发的轻量 AI 快速研判
- 贵金属专用模型预设下拉
- 贵金属专用 AI 提示词模板与留痕
- AI 自动研判与结构化信号留痕
- 模拟盘自动试仓、止损止盈、分批止盈与爆仓保护
- 可选 MT5 实盘发单链路（默认关闭，需在设置中二次确认）
- 本地知识库、规则评分、规则沙盒与自动学习摘要
- 独立桌面终端界面
- 本地 `.env` 配置保存

补充说明：

- 当前“事件窗口面板”提供的是结构性提醒，不是实时经济日历
- `EVENT_RISK_MODE` 目前仍以手动切换为主，尚未接入自动事件识别
- 现在已支持“事件计划驱动的自动纪律切换”，但事件时间仍需你手动维护
- 现在也支持接入外部 JSON 事件源，并与手填计划合并；只是默认还没有内置第三方日历供应商

## 启动方式

先确认本机已安装并运行 MetaTrader 5 客户端，再执行：

```powershell
cd C:\Users\Administrator\Desktop\贵金属机器人
python main.py
```

## 配置说明

项目优先读取当前目录下的 `.env` 文件。可参考 `.env.example` 创建：

- `TARGET_SYMBOLS`
- `BROKER_SYMBOL_MAP_JSON`
- `REFRESH_INTERVAL_SEC`
- `EVENT_RISK_MODE`
- `MT5_PATH`
- `MT5_LOGIN`
- `MT5_PASSWORD`
- `MT5_SERVER`
- `EVENT_AUTO_MODE_ENABLED`
- `EVENT_SCHEDULES`
- `EVENT_PRE_WINDOW_MIN`
- `EVENT_POST_WINDOW_MIN`
- `EVENT_FEED_ENABLED`
- `EVENT_FEED_URL`
- `EVENT_FEED_REFRESH_MIN`
- `MACRO_NEWS_FEED_ENABLED`
- `MACRO_NEWS_FEED_URLS`
- `MACRO_NEWS_FEED_REFRESH_MIN`
- `MACRO_DATA_FEED_ENABLED`
- `MACRO_DATA_FEED_SPECS`
- `MACRO_DATA_FEED_REFRESH_MIN`
- `LEARNING_PUSH_ENABLED`
- `LEARNING_PUSH_MIN_INTERVAL_HOUR`
- `NOTIFY_DND_ENABLED`
- `NOTIFY_DND_START_HOUR`
- `NOTIFY_DND_END_HOUR`
- `OVERNIGHT_SPREAD_GUARD_ENABLED`
- `OVERNIGHT_SPREAD_GUARD_START_HOUR`
- `OVERNIGHT_SPREAD_GUARD_END_HOUR`
- `DINGTALK_WEBHOOK`
- `PUSHPLUS_TOKEN`
- `NOTIFY_COOLDOWN_MIN`
- `AI_API_KEY`
- `AI_API_BASE`
- `AI_MODEL`
- `AI_PUSH_ENABLED`
- `AI_PUSH_SUMMARY_ONLY`
- `AI_AUTO_INTERVAL_MIN`
- `TRADE_MODE`
- `LIVE_MAX_DRAWDOWN_PCT`
- `LIVE_ORDER_PRECHECK_ONLY`
- `LIVE_MAX_OPEN_POSITIONS`
- `LIVE_MAX_ORDERS_PER_DAY`
- `SIM_INITIAL_BALANCE`
- `SIM_NO_TP2_LOCK_R`
- `SIM_NO_TP2_PARTIAL_CLOSE_RATIO`
- `SIM_MIN_RR`
- `SIM_RELAXED_RR`
- `SIM_MODEL_MIN_PROBABILITY`
- `SIM_STRATEGY_MIN_RR_JSON`
- `SIM_STRATEGY_DAILY_LIMIT_JSON`
- `SIM_EXPLORATORY_DAILY_LIMIT`
- `SIM_STRATEGY_COOLDOWN_JSON`
- `SIM_EXPLORATORY_COOLDOWN_MIN`

其中：

- 如果 MT5 终端已经在本机登录完成，`MT5_LOGIN / MT5_PASSWORD / MT5_SERVER` 可以暂时留空
- 如果你的券商品种名不是标准名，可配置 `BROKER_SYMBOL_MAP_JSON`，例如 `{"XAUUSD":"GOLD","EURUSD":"EURUSDm"}`；系统内部仍显示 `XAUUSD/EURUSD`，发给 MT5 时自动转换成券商品种名
- `EVENT_RISK_MODE` 支持：
  - `normal`：正常观察
  - `pre_event`：事件前高敏
  - `post_event`：事件落地观察
  - `illiquid`：流动性偏弱
- `EVENT_AUTO_MODE_ENABLED=1` 后，系统会根据已登记事件计划，在事件前后自动切换 `pre_event / post_event`
- `EVENT_SCHEDULES` 采用分号分隔，格式例如：
  - `2026-04-15 20:30|美国 CPI;2026-04-16 02:00|联储利率决议`
- `EVENT_PRE_WINDOW_MIN` 控制事件前多久自动进入高敏阶段
- `EVENT_POST_WINDOW_MIN` 控制事件后多久维持观察阶段
- `EVENT_FEED_ENABLED=1` 后，可从外部 JSON 事件源自动拉取事件，并和手填 `EVENT_SCHEDULES` 合并
- `EVENT_FEED_URL` 支持本地 JSON 文件路径或 `https://...` 地址，兼容以下结构：
  - `[{"time":"2026-04-15 20:30","name":"美国 CPI","importance":"high","symbols":["XAUUSD","EURUSD"]}]`
  - `{"events":[{"time":"2026-04-16T02:00:00+08:00","title":"联储利率决议","importance":"high","symbols":"XAUUSD,USDJPY"}]}`
- 如果事件源同时提供结果值，也支持直接带：
  - `actual`
  - `forecast`
  - `previous`
  - `unit`
  - `better_when`
- `better_when` 可选：
  - `higher_bullish`
  - `higher_bearish`
  - `lower_bullish`
  - `lower_bearish`
- 这样系统会把“数据结果偏多 / 偏空 / 中性”的解释直接并入快照摘要、AI 研判和后续推送链
- `EVENT_FEED_REFRESH_MIN` 控制外部事件源缓存时长，避免每轮刷新都重复拉取
- `MACRO_NEWS_FEED_ENABLED=1` 后，系统会读取外部 RSS / Atom 资讯流，并把高相关资讯摘要并入快照、AI 研判和学习推送链
- `MACRO_NEWS_FEED_URLS` 支持用分号分隔多个 RSS / Atom 地址或本地 XML 文件，例如 ECB 的 `https://www.ecb.europa.eu/rss/press.html;https://www.ecb.europa.eu/rss/statpress.html`
- `MACRO_NEWS_FEED_REFRESH_MIN` 控制资讯流缓存时长，避免每轮刷新都重复抓取
- `MACRO_DATA_FEED_ENABLED=1` 后，系统会读取结构化宏观数据源，并把最新数值、前值变化和方向提示并入快照
- `MACRO_DATA_FEED_SPECS` 建议填写本地 JSON 规格文件路径，里面可配置 `fred / bls / treasury / generic_json` 四类数据源
- `MACRO_DATA_FEED_REFRESH_MIN` 控制结构化宏观数据缓存时长
- 仓库内已经附带第一版官方规格样例：[macro_data_sources.official.json](/C:/Users/Administrator/Desktop/贵金属机器人/macro_data_sources.official.json)
- 如果你准备直接试跑，可以把 `.env` 中的 `MACRO_DATA_FEED_SPECS` 指向 `macro_data_sources.official.json`
- 其中 `FRED` 需要 `FRED_API_KEY`，`BLS` 可匿名调用但建议配置 `BLS_API_KEY` 以获得更稳定的频率和参数支持
- `LEARNING_PUSH_ENABLED=1` 后，知识库在形成新的规则学习摘要时，会自动向已配置渠道推送一份精简学习日报
- `NOTIFY_DND_ENABLED=1` 后，普通市场提醒会在免打扰窗口内静默；默认仍允许 `MT5` 断连和外部数据源失效这类系统级提醒继续送达
- `NOTIFY_DND_START_HOUR / NOTIFY_DND_END_HOUR` 控制免打扰时段，默认是 `00:00-07:00`
- `OVERNIGHT_SPREAD_GUARD_ENABLED=1` 后，系统会在默认 `05:00-07:00` 的隔夜交割敏感窗口里压制普通点差异常推送，避免隔夜结算把人从睡眠中吵醒
- `OVERNIGHT_SPREAD_GUARD_START_HOUR / OVERNIGHT_SPREAD_GUARD_END_HOUR` 控制隔夜点差过滤窗口；如果同时处于高影响事件窗口，关键宏观提醒仍然会继续发出

结构化宏观数据规格示例：

```json
[
  {
    "provider": "fred",
    "name": "美国10年期实际利率",
    "series_id": "DFII10",
    "api_key_env": "FRED_API_KEY",
    "symbols": ["XAUUSD", "XAGUSD"],
    "importance": "high",
    "bias_mode": "higher_bearish"
  },
  {
    "provider": "bls",
    "name": "美国失业率",
    "series_id": "LNS14000000",
    "registration_key_env": "BLS_API_KEY",
    "start_year": 2025,
    "end_year": 2026,
    "symbols": ["XAUUSD", "EURUSD", "USDJPY"],
    "importance": "high",
    "bias_mode": "lower_bullish"
  }
]
```
- `LEARNING_PUSH_MIN_INTERVAL_HOUR` 控制学习日报的最小推送间隔，避免规则刚有一点波动就反复刷屏
- 如果你希望显式指定终端位置，可以填写 `MT5_PATH`
- 如果需要主动提醒，可以填写 `DINGTALK_WEBHOOK` 或 `PUSHPLUS_TOKEN`
- 推送默认只针对关键提醒（例如点差异常、MT5 断连、休市/流动性异常），并按 `NOTIFY_COOLDOWN_MIN` 节流
- 系统默认会在 `00:00-07:00` 开启免打扰，并在 `05:00-07:00` 额外压制隔夜结算带来的普通点差告警；如果你是夜间盯盘用户，可在 `.env` 里关闭或调整
- 如果需要手动 AI 研判，可以填写 `AI_API_KEY`；当前独立项目默认使用与老项目一致的硅基流动配置：
  - `AI_API_BASE=https://api.siliconflow.cn/v1`
  - `AI_MODEL=deepseek-ai/DeepSeek-R1`
- `AI_PUSH_ENABLED=1` 后，手动触发的 AI 研判可同步推送到钉钉 / PushPlus
- `AI_PUSH_SUMMARY_ONLY=1` 时，只推送 AI 摘要首句，避免长文本刷屏
- `AI_AUTO_INTERVAL_MIN` 大于 0 时，系统会按分钟间隔自动触发 AI 研判；配置异常会安全回落为关闭状态
- `TRADE_MODE=simulation` 是默认模式，只写入模拟盘；`TRADE_MODE=live` 会允许系统向 MT5 发送真实订单，必须确认 MT5 账户、品种、保证金和熔断设置无误后再启用
- `LIVE_MAX_DRAWDOWN_PCT` 控制实盘日内亏损熔断比例，例如 `0.05` 表示当日亏损达到 5% 后禁止继续开仓
- `LIVE_ORDER_PRECHECK_ONLY=1` 时，实盘链路只执行 MT5 `order_check` 预检，不会执行 `order_send`；确认稳定后才建议改为 `0`
- `LIVE_MAX_OPEN_POSITIONS` 控制实盘最大同时持仓数，达到上限后禁止继续开仓
- `LIVE_MAX_ORDERS_PER_DAY` 控制实盘每日 AI 订单上限，防止异常信号在同一天反复开仓
- `SIM_INITIAL_BALANCE` 控制模拟盘起始本金
- `SIM_NO_TP2_LOCK_R` 与 `SIM_NO_TP2_PARTIAL_CLOSE_RATIO` 控制无第二目标位时的保本保护和首次减仓比例
- `SIM_MIN_RR / SIM_RELAXED_RR / SIM_MODEL_MIN_PROBABILITY` 控制自动试仓的盈亏比门槛与本地模型胜率放宽条件
- `SIM_STRATEGY_MIN_RR_JSON` 控制早期动能、直线动能、回调狙击、方向试仓等策略族的独立 RR 门槛；HITL 批准策略学习建议后只调整对应策略族
- `SIM_STRATEGY_DAILY_LIMIT_JSON` 控制各策略族自己的探索试仓日上限；这样“回调狙击今天试错太多”不会误伤其他策略
- `SIM_EXPLORATORY_DAILY_LIMIT` 控制模拟盘探索试仓每日最多开仓次数，避免低置信学习样本在震荡行情里过度试错
- `SIM_STRATEGY_COOLDOWN_JSON` 控制各策略族自己的探索冷却分钟数；这样短线动能可以快一些，回调策略可以稳一些
- `SIM_EXPLORATORY_COOLDOWN_MIN` 控制同一品种同一方向探索试仓的冷却分钟数，避免同一波行情被重复拆成多笔低质量样本

## 外部历史行情回放样本

系统支持把公开 M1/OHLC 历史行情 CSV 导入知识库，生成带来源标记的回放样本，用于缓解冷启动样本不足。外部回放样本不是他人真实账户成交单，不应直接当作实盘盈利证明；它的作用是用真实历史报价回放我们的候选逻辑，并把结果写入 `market_snapshots` / `snapshot_outcomes` 供本地概率模型训练。

```powershell
python external_market_samples.py .runtime\external_data\xauusd_m1.csv --symbol XAUUSD --horizon-min 30 --lookback-bars 60 --stride-bars 5 --min-move-pct 0.12
```

CSV 至少需要包含时间、开盘、最高、最低、收盘字段，支持常见表头如 `timestamp,open,high,low,close,volume`，也支持部分无表头 `YYYYMMDD HHMMSS;open;high;low;close;volume` 格式。导入后可以运行现有训练流程刷新本地胜率模型。

也可以使用 Dukascopy 历史行情下载工具先拉取 M1 数据，再导入：

```powershell
npx --yes dukascopy-node -i xauusd -from 2026-04-20 -to 2026-04-21 -t m1 -f csv -dir .runtime\external_data -fn xauusd_2026-04-20_m1.csv -s
python external_market_samples.py .runtime\external_data\xauusd_2026-04-20_m1.csv.csv --symbol XAUUSD
```

批量导入可以使用：

```powershell
python external_market_batch.py --date-from 2026-04-18 --date-to 2026-04-21 --symbol XAUUSD --download-dir .runtime\external_data --output .runtime\external_batch_xauusd.json
```

`--date-to` 是不包含的结束日期，例如 `2026-04-18` 到 `2026-04-21` 会导入 18、19、20 三天。批量器会按天下载、导入、训练，并输出每一天的候选数量、写入数量和最终模型训练结果。

## 错过行情复盘

当系统长时间只提示观察时，可以运行错过行情复盘器，检查过去一段时间内是否出现了足够大的后续波动，以及当时被哪一层规则拦截：

```powershell
python missed_opportunity_auditor.py --symbol XAUUSD --horizon-min 30 --start-time "2026-04-20 00:00:00" --end-time "2026-04-22 23:59:59" --output .runtime\missed_opportunities_xauusd.json
```

报告会输出 `missed_count`、`captured_count`、主要阻断原因和代表样本。默认会排除外部回放样本，只分析系统真实运行快照；如需一起分析外部回放样本，可追加 `--include-external`。

## 事件窗口回放

如果你刚调整了事件后规则，想验证“原来被事件挡住的样本里，现在到底能多放出多少单”，可以运行事件窗口回放器：

```powershell
python event_gate_replay.py --symbol XAUUSD --horizon-min 30 --start-time "2026-04-20 00:00:00" --end-time "2026-04-22 23:59:59" --output .runtime\event_gate_replay_xauusd.json
```

报告会输出：

- `total_event_rows`：事件后被拦截的历史样本数
- `released_rows / released_clusters`：当前新规则额外放行的样本数与去重簇数
- `released_outcomes`：按新规则方向回放后的结果分布
- `blocked_summary`：仍未放行的主要阻断原因

这个工具适合和 `missed_opportunity_auditor.py` 搭配使用：前者回答“新规则是否真的放宽了”，后者回答“系统整体还错过了哪些大波动，以及卡在哪一层”。

## 历史样本修复

如果知识库里的老快照来自旧版本运行时，`feature_json` 可能只有中文描述，没有原始结构字段。此时可以运行历史样本修复器，把旧样本里的文本字段反推回结构码值，并把提醒留痕里的风控/模型字段一并回填：

```powershell
python snapshot_feature_backfill.py --symbol XAUUSD --start-time "2026-04-20 00:00:00" --end-time "2026-04-22 23:59:59"
```

先只看覆盖率、不真正写回数据库：

```powershell
python snapshot_feature_backfill.py --symbol XAUUSD --start-time "2026-04-20 00:00:00" --end-time "2026-04-22 23:59:59" --dry-run
```

修复器当前会重点回填这些字段：

- 结构字段：`intraday_bias`、`intraday_volatility`、`intraday_location`、`multi_timeframe_alignment`、`multi_timeframe_bias`、`key_level_state`、`breakout_state`、`retest_state`
- 风控/模型字段：`risk_reward_ready`、`risk_reward_ratio`、`risk_reward_stop_price`、`risk_reward_target_price`、`model_ready`、`model_win_probability`

这一步的意义不是“美化历史数据”，而是避免学习系统继续把残缺样本当成真实约束，从而误判“最近都没有可出手行情”。

实盘风险提示：

- 实盘模式会直接调用 MT5 `order_send`，可能产生真实资金损失
- 建议先长期使用 `simulation`，确认提醒、信号、风控和品种规格稳定后，再考虑小资金实盘
- 开启实盘前，应先保持 `LIVE_ORDER_PRECHECK_ONLY=1` 跑预检，确认 `LIVE_MAX_DRAWDOWN_PCT`、最大持仓、日订单上限、MT5 登录账户和经纪商合约规格都正确

## 与老项目的配置迁移

首次启动时，独立项目会在当前 `.env` 缺少关键字段时，自动尝试从老项目迁移这些配置一次：

- `AI_API_KEY`
- `AI_API_BASE`
- `AI_MODEL`
- `DINGTALK_WEBHOOK`
- `PUSHPLUS_TOKEN`
- `MT5_PATH / MT5_LOGIN / MT5_PASSWORD / MT5_SERVER`
- `TARGET_SYMBOLS`

这样就不需要你在贵金属项目里重复填一遍模型、推送和 MT5 连接信息。后续如果你在本项目里保存过设置，将以当前项目配置为准，不再反复回灌老项目值。

## 依赖

建议安装：

```powershell
pip install -r requirements.txt
```

如果你把项目推到 GitHub，仓库内已经附带基础测试工作流：

- `push` 到 `main` 时会自动执行 `pytest -q`
- 创建或更新 `Pull Request` 时也会自动跑测试

## 说明

这个目录是独立项目雏形，不依赖当前仓库里的欧易、币安模块；模拟盘与可选 MT5 实盘链路已经在本项目内独立维护。
模型预设、AI 快速研判提示词、推送配置迁移逻辑也已经在这里独立收口。
