from tradingagents.default_config import DEFAULT_CONFIG  # 导入默认配置
from tradingagents.graph.trading_graph import TradingAgentsGraph  # 导入交易智能体图

# DEFAULT_CONFIG 已经应用了 TRADINGAGENTS_* 环境变量覆盖
# （llm_provider, deep_think_llm, quick_think_llm, backend_url 等），
# 因此用户可以纯粹通过 .env 切换模型或端点而无需编辑此脚本。
# 仅当您想要一个应该忽略环境的硬编码值时，在此处覆盖单个键。
config = DEFAULT_CONFIG.copy()  # 复制默认配置

# 使用自定义配置初始化
ta = TradingAgentsGraph(debug=True, config=config)

# 前向传播
_, decision = ta.propagate("NVDA", "2026-09-01")  # 为 NVDA 在 2026-09-01 运行分析
print(decision)  # 打印决策
