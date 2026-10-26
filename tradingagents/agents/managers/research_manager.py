"""研究经理：将看涨/看跌辩论转化为交易员的结构化投资计划。"""

from __future__ import annotations  # 启用后注解

from tradingagents.agents.context import get_instrument_context_from_state, get_language_instruction  # 导入上下文和语言指令
from tradingagents.agents.schemas import ResearchPlan, render_research_plan  # 导入研究计划模式和渲染函数
from tradingagents.agents.structured import (  # 导入结构化输出相关函数
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_structured_or_freetext,
)


def create_research_manager(llm):
    """创建研究经理节点"""
    structured_llm = bind_structured(llm, ResearchPlan, "Research Manager")  # 绑定结构化输出

    def research_manager_node(state) -> dict:
        """研究经理节点函数"""
        instrument_context = get_instrument_context_from_state(state)  # 获取工具上下文
        history = state["investment_debate_state"].get("history", "")  # 获取辩论历史

        investment_debate_state = state["investment_debate_state"]  # 获取投资辩论状态

        # 构建提示词
        prompt = f"""As the Research Manager and debate facilitator, your role is to critically evaluate this round of debate and deliver a clear, actionable investment plan for the trader.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction in the bull thesis; recommend taking or growing the position
- **Overweight**: Constructive view; recommend gradually increasing exposure
- **Hold**: Balanced view; recommend maintaining the current position
- **Underweight**: Cautious view; recommend trimming exposure
- **Sell**: Strong conviction in the bear thesis; recommend exiting or avoiding the position

The debate always contains conflicting arguments; deciding which side is stronger is the job, so conflict alone is not a reason to Hold. Commit to the side with the stronger case, sized by how decisively it wins. Choose Hold only when the evidence is still balanced after that weighing, or too thin to support a call; do not manufacture a direction to appear decisive. Weigh the bull and bear cases on their merits, independent of which side spoke first or last.

---

**Debate History:**
{history}

## Output

Write these sections, in this order, starting with the recommendation on its own line:

- **Recommendation**: exactly one of Buy / Overweight / Hold / Underweight / Sell
- **Rationale**: which arguments decided it
- **Strategic Actions**: concrete steps for the trader, sized against a standard allocation

{NO_EXTERNAL_TOOLS}""" + get_language_instruction()

        # 调用结构化输出或自由文本
        investment_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_research_plan,
            "Research Manager",
        )

        # 更新投资辩论状态
        new_investment_debate_state = {
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": investment_plan,
            "count": investment_debate_state["count"],
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": investment_plan,
        }

    return research_manager_node
