# 贵金属监控终端

这是从原有综合项目中抽离出的独立 MVP，目标很明确：

- 只做贵金属 / 宏观品种监控
- 只用 MT5 数据源
- 只做报价、点差、宏观窗口与风险提醒
- 暂不包含虚拟币、欧易、自动交易和模拟盘
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
- 独立桌面终端界面
- 本地 `.env` 配置保存

补充说明：

- 当前“事件窗口面板”提供的是结构性提醒，不是实时经济日历
- `EVENT_RISK_MODE` 目前仍以手动切换为主，尚未接入自动事件识别
- 现在已支持“事件计划驱动的自动纪律切换”，但事件时间仍需你手动维护

## 启动方式

先确认本机已安装并运行 MetaTrader 5 客户端，再执行：

```powershell
cd C:\Users\Administrator\Desktop\贵金属机器人
python main.py
```

## 配置说明

项目优先读取当前目录下的 `.env` 文件。可参考 `.env.example` 创建：

- `TARGET_SYMBOLS`
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
- `DINGTALK_WEBHOOK`
- `PUSHPLUS_TOKEN`
- `NOTIFY_COOLDOWN_MIN`
- `AI_API_KEY`
- `AI_API_BASE`
- `AI_MODEL`
- `AI_PUSH_ENABLED`
- `AI_PUSH_SUMMARY_ONLY`

其中：

- 如果 MT5 终端已经在本机登录完成，`MT5_LOGIN / MT5_PASSWORD / MT5_SERVER` 可以暂时留空
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
- 如果你希望显式指定终端位置，可以填写 `MT5_PATH`
- 如果需要主动提醒，可以填写 `DINGTALK_WEBHOOK` 或 `PUSHPLUS_TOKEN`
- 推送默认只针对关键提醒（例如点差异常、MT5 断连、休市/流动性异常），并按 `NOTIFY_COOLDOWN_MIN` 节流
- 如果需要手动 AI 研判，可以填写 `AI_API_KEY`；当前独立项目默认使用与老项目一致的硅基流动配置：
  - `AI_API_BASE=https://api.siliconflow.cn/v1`
  - `AI_MODEL=deepseek-ai/DeepSeek-R1`
- `AI_PUSH_ENABLED=1` 后，手动触发的 AI 研判可同步推送到钉钉 / PushPlus
- `AI_PUSH_SUMMARY_ONLY=1` 时，只推送 AI 摘要首句，避免长文本刷屏

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

## 说明

这个目录是独立项目雏形，不依赖当前仓库里的欧易、币安或模拟盘模块。
模型预设、AI 快速研判提示词、推送配置迁移逻辑也已经在这里独立收口。
