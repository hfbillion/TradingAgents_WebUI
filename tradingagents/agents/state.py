from typing import Annotated  # 导入类型注解

from langgraph.graph import MessagesState  # 导入 LangGraph 的消息状态
from typing_extensions import TypedDict  # 导入 TypedDict


# 研究团队状态（看涨/看跌研究员之间的辩论）
class InvestDebateState(TypedDict):
    bull_history: Annotated[
        str, "看涨对话历史"
    ]
    bear_history: Annotated[
        str, "看跌对话历史"
    ]
    history: Annotated[str, "完整对话历史"]
    current_response: Annotated[str, "最新回复"]
    count: Annotated[int, "当前对话长度（轮次）"]


# 风险管理团队状态（激进/保守/中立分析师之间的讨论）
class RiskDebateState(TypedDict):
    aggressive_history: Annotated[
        str, "激进分析师的对话历史"
    ]
    conservative_history: Annotated[
        str, "保守分析师的对话历史"
    ]
    neutral_history: Annotated[
        str, "中立分析师的对话历史"
    ]
    history: Annotated[str, "完整对话历史"]
    latest_speaker: Annotated[str, "最后发言的分析师"]
    current_aggressive_response: Annotated[
        str, "激进分析师的最新回复"
    ]
    current_conservative_response: Annotated[
        str, "保守分析师的最新回复"
    ]
    current_neutral_response: Annotated[
        str, "中立分析师的最新回复"
    ]
    count: Annotated[int, "当前对话长度（轮次）"]


# 智能体状态（主状态模式）
class AgentState(MessagesState):
    company_of_interest: Annotated[str, "我们感兴趣交易的公司"]
    asset_type: Annotated[str, "正在分析的资产类型，如股票或加密货币"]
    instrument_context: Annotated[str, "在运行开始时解析的确定性股票标识"]
    trade_date: Annotated[str, "分析日期；数据以此日期为准"]

    # 研究步骤
    market_report: Annotated[str, "市场分析师的报告"]
    sentiment_report: Annotated[str, "情绪分析师的报告"]
    news_report: Annotated[str, "新闻分析师关于公司和世界新闻的报告"]
    fundamentals_report: Annotated[str, "基本面分析师的报告"]

    # 研究团队讨论步骤
    investment_debate_state: Annotated[
        InvestDebateState, "关于是否投资的辩论的当前状态"
    ]
    investment_plan: Annotated[str, "研究经理的投资计划"]

    trader_investment_plan: Annotated[str, "交易员的交易提案"]

    # 风险管理团队讨论步骤
    risk_debate_state: Annotated[
        RiskDebateState, "关于评估风险的辩论的当前状态"
    ]
    final_trade_decision: Annotated[str, "投资组合经理的最终决策"]
    final_rating: Annotated[str, "投资组合经理的 5 级评级，如果没有则为 REVIEW"]
    past_context: Annotated[str, "投资组合经理的记忆日志上下文（同股票决策 + 跨股票教训），由记忆日志步骤写入"]
  # 记忆备注：记忆日志步骤本运行无法结算或读取的内容，用于报告；一切顺利时为空
    memory_note: Annotated[str, "What the Memory Log step could not settle or read this run, for the report; empty when all went well"]
    portfolio_context: Annotated[str, "调用者提供的持仓和现金，在运行开始时渲染；未提供时为空"]
