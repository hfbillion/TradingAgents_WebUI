"""交易员：将研究经理的投资计划转化为具体的交易提案。"""

from __future__ import annotations  # 启用后注解

from langchain_core.messages import AIMessage  # 导入 AI 消息

from tradingagents.agents.context import (  # 导入上下文相关函数
    get_instrument_context_from_state,
    get_language_instruction,
    get_portfolio_context_from_state,
)
from tradingagents.agents.schemas import TraderProposal, render_trader_proposal  # 导入交易员提案模式和渲染函数
from tradingagents.agents.structured import (  # 导入结构化输出相关函数
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_structured_or_freetext,
)


def create_trader(llm):
    """创建交易员节点"""
    structured_llm = bind_structured(llm, TraderProposal, "Trader")  # 绑定结构化输出

    def trader_node(state):
        """交易员节点函数"""
        company_name = state["company_of_interest"]  # 获取公司名称
        instrument_context = get_instrument_context_from_state(state)  # 获取工具上下文
        investment_plan = state["investment_plan"]  # 获取投资计划
        # 研究计划消化了辩论但丢失了确切的价格结构；
        # 给交易员技术市场报告，以便入场/止损水平建立在真实的 ATR / 支撑阻力 / 当前价格上（#1167）。
        # 当用户未选择市场分析师时，报告为空，因此仅在它有内容时提供它（和接地指令）。
        market_report = (state["market_report"] or "").strip()  # 获取市场报告
        portfolio_context = get_portfolio_context_from_state(state)  # 获取投资组合上下文

        if market_report:
            # 接地指令：使用技术市场报告的价格结构
            grounding = (
                "Ground concrete price levels (entry, stop-loss, position sizing) in the technical "
                "market report's price structure -- current price, support/resistance, ATR, and "
                "volatility -- and use the research plan for direction and strategy. "
            )
            report_section = f"Technical Market Report:\n{market_report}\n\n"
        else:
            grounding = ""
            report_section = ""

        # 构建消息
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent analyzing market data to make investment decisions. "
                    "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                    + grounding
                    # 入场/止损是数字价格字段。要求具体水平会邀请百分比（"15%"），
                    # 这不是价格并且使结构化解析失败（#1288）。
                    + "State entry price and stop-loss as absolute price levels in the "
                    "instrument's quote currency (for example 189.5), never a percentage "
                    "or a range; convert a percentage distance to the price level it "
                    "implies, or omit the field if you cannot state a number. "
                    + NO_EXTERNAL_TOOLS
                    + get_language_instruction()
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Here is the research team's investment plan for {company_name}. "
                    f"{instrument_context}\n\n"
                    f"{report_section}"
                    f"{portfolio_context}\n\n"
                    f"Proposed Investment Plan:\n{investment_plan}\n\n"
                    "Make an informed, strategic trading decision.\n\n"
                    "## Output\n\n"
                    "Write these sections, in this order, starting with the action "
                    "on its own line:\n\n"
                    "- **Action**: exactly one of Buy / Hold / Sell. A research "
                    "recommendation of Overweight is a Buy and Underweight is a Sell, "
                    "sized by how strong the case is; conflict alone is not a Hold.\n"
                    "- **Reasoning**: why, against the plan and the price structure\n"
                    "- **Entry Price**, **Stop Loss**, **Position Sizing**: when you can state them"
                ),
            },
        ]

        # 调用结构化输出或自由文本
        trader_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            messages,
            render_trader_proposal,
            "Trader",
        )

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
        }

    return trader_node
