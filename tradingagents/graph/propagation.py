from typing import Any  # 导入类型注解

from tradingagents.agents.state import InvestDebateState, RiskDebateState  # 导入辩论状态类


class Propagator:
    """处理状态初始化和通过图的传播。"""

    def __init__(self, max_recur_limit=100):
        """使用配置参数初始化。"""
        self.max_recur_limit = max_recur_limit  # 最大递归限制

    def create_initial_state(
        self,
        company_name: str,  # 公司名称/股票代码
        trade_date: str,  # 交易日期
        asset_type: str = "stock",  # 资产类型（默认为股票）
        past_context: str = "",  # 过去上下文（来自记忆日志的教训）
        instrument_context: str = "",  # 工具上下文（股票代码的确定性标识）
        portfolio_context: str = "",  # 投资组合上下文（当前持仓和现金）
    ) -> dict[str, Any]:
        """为智能体图创建初始状态。

        ``instrument_context`` 是在运行开始时解析一次的确定性股票标识字符串
        （见 ``TradingAgentsGraph.resolve_instrument_context``）。
        当为空时，智能体通过 ``get_instrument_context_from_state`` 回退到仅股票代码的上下文。
        """
        return {
            "messages": [("human", company_name)],  # 初始消息：公司名称
            "company_of_interest": company_name,  # 感兴趣的公司
            "asset_type": asset_type,  # 资产类型
            "instrument_context": instrument_context,  # 工具上下文
            "trade_date": str(trade_date),  # 交易日期
            "past_context": past_context,  # 过去上下文（教训）
            "memory_note": "",  # 记忆备注（记录无法结算的内容）
            "portfolio_context": portfolio_context,  # 投资组合上下文
            # 投资辩论状态（看涨/看跌研究员之间的辩论）
            "investment_debate_state": InvestDebateState(
                {
                    "bull_history": "",  # 看涨历史
                    "bear_history": "",  # 看跌历史
                    "history": "",  # 完整历史
                    "current_response": "",  # 当前回复
                    "count": 0,  # 计数
                }
            ),
            # 风险讨论状态（激进/保守/中立分析师之间的讨论）
            "risk_debate_state": RiskDebateState(
                {
                    "aggressive_history": "",  # 激进历史
                    "conservative_history": "",  # 保守历史
                    "neutral_history": "",  # 中立历史
                    "history": "",  # 完整历史
                    "latest_speaker": "",  # 最后发言者
                    "current_aggressive_response": "",  # 当前激进回复
                    "current_conservative_response": "",  # 当前保守回复
                    "current_neutral_response": "",  # 当前中立回复
                    "count": 0,  # 计数
                }
            ),
            # 分析师报告（初始为空）
            "market_report": "",  # 市场报告
            "fundamentals_report": "",  # 基本面报告
            "sentiment_report": "",  # 情绪报告
            "news_report": "",  # 新闻报告
        }

    def get_graph_args(self, callbacks: list | None = None) -> dict[str, Any]:
        """获取图调用的参数。

        参数:
            callbacks: 用于工具执行跟踪的可选回调处理器列表。
                       注意：LLM 回调通过 LLM 构造函数单独处理。
        """
        config = {"recursion_limit": self.max_recur_limit}  # 设置递归限制
        if callbacks:
            config["callbacks"] = callbacks  # 添加回调（如果有）
        return {
            "stream_mode": "values",  # 流模式：返回值
            "config": config,  # 配置
        }
