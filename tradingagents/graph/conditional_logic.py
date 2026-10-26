from tradingagents.agents.state import AgentState  # 导入智能体状态


class ConditionalLogic:
    """处理确定图流的条件逻辑。"""

    def __init__(self, max_debate_rounds=1, max_risk_discuss_rounds=1):
        """使用配置参数初始化。"""
        self.max_debate_rounds = max_debate_rounds  # 最大辩论轮数
        self.max_risk_discuss_rounds = max_risk_discuss_rounds  # 最大风险讨论轮数

    def should_continue_debate(self, state: AgentState) -> str:
        """确定辩论是否应该继续。"""

        # 如果计数达到最大轮数（每轮 bull 和 bear 各发言一次，所以是 2 * max_debate_rounds）
        if (
            state["investment_debate_state"]["count"] >= 2 * self.max_debate_rounds
        ):  # max_debate_rounds 轮，每轮 bull 和 bear 各一次
            return "Research Manager"  # 转到研究经理
        # 如果当前回复以 "Bull" 开头，则轮到 Bear
        if state["investment_debate_state"]["current_response"].startswith("Bull"):
            return "Bear Researcher"
        # 否则轮到 Bull
        return "Bull Researcher"

    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """确定风险分析是否应该继续。"""
        # 如果计数达到最大轮数（每轮三个风险分析师各发言一次，所以是 3 * max_risk_discuss_rounds）
        if (
            state["risk_debate_state"]["count"] >= 3 * self.max_risk_discuss_rounds
        ):  # max_risk_discuss_rounds 轮，每轮三个风险分析师各一次
            return "Portfolio Manager"  # 转到投资组合经理
        # 如果最后发言者是 Aggressive，则轮到 Conservative
        if state["risk_debate_state"]["latest_speaker"].startswith("Aggressive"):
            return "Conservative Analyst"
        # 如果最后发言者是 Conservative，则轮到 Neutral
        if state["risk_debate_state"]["latest_speaker"].startswith("Conservative"):
            return "Neutral Analyst"
        # 否则轮到 Aggressive
        return "Aggressive Analyst"
