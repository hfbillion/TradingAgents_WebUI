"""投资组合经理：将风险分析师辩论综合为最终决策。

使用 LangChain 的 ``with_structured_output``，以便 LLM 直接生成类型化的
``PortfolioDecision``，在一次调用中。其评级是运行的 5 级 ``final_rating``，
决策被渲染为 markdown 作为 ``final_trade_decision``，用于记忆日志、CLI 显示和保存的报告。
当提供商不暴露结构化输出时，智能体回退到自由文本生成，
并从该文本中读取评级。
"""

from __future__ import annotations  # 启用后注解

from tradingagents.agents.context import (  # 导入上下文相关函数
    get_instrument_context_from_state,
    get_language_instruction,
    get_portfolio_context_from_state,
)
from tradingagents.agents.rating import parse_rating  # 导入评级解析函数
from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision  # 导入投资组合决策模式和渲染函数
from tradingagents.agents.structured import NO_EXTERNAL_TOOLS, bind_structured, invoke_structured  # 导入结构化输出相关函数


def create_portfolio_manager(llm):
    """创建投资组合经理节点"""
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")  # 绑定结构化输出

    def portfolio_manager_node(state) -> dict:
        """投资组合经理节点函数"""
        instrument_context = get_instrument_context_from_state(state)  # 获取工具上下文
        portfolio_context = get_portfolio_context_from_state(state)  # 获取投资组合上下文

        history = state["risk_debate_state"]["history"]  # 获取风险辩论历史
        risk_debate_state = state["risk_debate_state"]  # 获取风险辩论状态
        research_plan = state["investment_plan"]  # 获取研究计划
        trader_plan = state["trader_investment_plan"]  # 获取交易员提案

        past_context = state.get("past_context", "")  # 获取过去上下文（教训）
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        # 构建提示词
        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

{portfolio_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}
**Risk Analysts Debate History:**
{history}

---

Ground every conclusion in specific evidence from the analysts. The risk debate always contains conflicting stances; deciding which is stronger is the job, so conflict alone is not a reason to Hold. Commit to the stronger case, sized by how decisively it wins. Choose Hold only when the evidence is still balanced after that weighing, or too thin to support a call; do not force a direction to appear decisive. Weigh the analysts on their merits, independent of speaking order.

## Output

Write these sections, in this order, starting with the rating on its own line:

- **Rating**: exactly one of Buy / Overweight / Hold / Underweight / Sell
- **Executive Summary**: the call and how to act on it
- **Investment Thesis**: the evidence that decided it, and what would change it

{NO_EXTERNAL_TOOLS}{get_language_instruction()}"""

        # 类型化评级是决策；渲染的文本只携带它。
        # 从文本读回，论点引用的评级可以替换它。
        decision = invoke_structured(structured_llm, prompt, "Portfolio Manager")
        if decision is not None:
            final_trade_decision = render_pm_decision(decision)  # 渲染决策
            final_rating = decision.rating.value  # 获取评级值
        else:
            final_trade_decision = llm.invoke(prompt).content  # 回退到自由文本
            final_rating = parse_rating(final_trade_decision)  # 从文本解析评级

        # 更新风险辩论状态
        new_risk_debate_state = {
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",  # 设置为 "Judge" 表示已评判
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
            "final_rating": final_rating,
        }

    return portfolio_manager_node
