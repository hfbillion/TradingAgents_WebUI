import logging  # 导入日志模块
from collections import Counter  # 导入 Counter，用于计数
from typing import Any, TypedDict  # 导入类型注解

from langchain_core.messages import HumanMessage  # 导入人类消息
from langgraph.graph import END, START, StateGraph  # 导入 LangGraph 的图组件
from langgraph.prebuilt import ToolNode  # 导入工具节点

from tradingagents.agents import (  # 导入所有智能体创建函数
    create_aggressive_debator,
    create_bear_researcher,
    create_bull_researcher,
    create_conservative_debator,
    create_fundamentals_analyst,
    create_market_analyst,
    create_neutral_debator,
    create_news_analyst,
    create_portfolio_manager,
    create_research_manager,
    create_sentiment_analyst,
    create_trader,
)
from tradingagents.agents.analysts.turn import WRAP_UP  # 导入 WRAP_UP 常量
from tradingagents.agents.state import AgentState  # 导入智能体状态

from .analyst_execution import build_analyst_execution_plan  # 导入分析师执行计划构建函数
from .conditional_logic import ConditionalLogic  # 导入条件逻辑

# 创建日志记录器
logger = logging.getLogger(__name__)

# 共享条件路由可以返回的每个目标。由路由驱动的每条边都映射所有目标，
# 因此穿透返回（例如在提示/i18n/重构漂移的说话者标签下）永远不会
# 击中缺失的 path_map 条目并在运行中使 LangGraph 崩溃（#1088）。
DEBATE_PATH_MAP = {
    "Bull Researcher": "Bull Researcher",  # 看涨研究员路由
    "Bear Researcher": "Bear Researcher",  # 看跌研究员路由
    "Research Manager": "Research Manager",  # 研究经理路由
}
RISK_ANALYSIS_PATH_MAP = {
    "Aggressive Analyst": "Aggressive Analyst",  # 激进分析师路由
    "Conservative Analyst": "Conservative Analyst",  # 保守分析师路由
    "Neutral Analyst": "Neutral Analyst",  # 中立分析师路由
    "Portfolio Manager": "Portfolio Manager",  # 投资组合经理路由
}


def _tools_or_done(state) -> str:
    """路由分析师的轮次：运行其工具调用，或用其报告完成。"""
    return "tools" if state["messages"][-1].tool_calls else END  # 如果有工具调用则转到 tools，否则结束


def _analyst_graph(spec, agent, max_tool_rounds: int):
    """一个分析师作为自己的图：模型及其工具，在私有消息历史记录上。

    它只返回其报告，因此并行运行的分析师永远不会写入相同的键，
    其工具调用永远不会到达其他分析师的消息。
    在 ``max_tool_rounds`` 轮工具调用后，它被告知编写其报告，
    并且无论它回答什么，该轮次都会结束它，因此一个不断调用工具的模型
    不会将图运行到其递归限制（#1420）。
    """
    # 定义输出模式（仅包含报告键）
    output = TypedDict(f"{spec.key.capitalize()}Report", {spec.report_key: str})
    graph = StateGraph(AgentState, output_schema=output)  # 创建状态图
    graph.add_node("agent", agent)  # 添加智能体节点
    graph.add_edge(START, "agent")  # 添加从 START 到 agent 的边
    if not spec.tools:
        # 如果没有工具，直接连接到 END
        graph.add_edge("agent", END)
        return graph.compile()

    def calls(messages):
        """提取消息中的工具调用名称列表"""
        return [call["name"] for m in messages for call in (getattr(m, "tool_calls", None) or [])]

    def rounds(messages) -> int:
        """计算消息中的工具调用轮次"""
        return sum(1 for m in messages if getattr(m, "tool_calls", None))

    def more_or_wrap_up(state) -> str:
        """决定是继续调用工具还是结束"""
        return "wrap_up" if rounds(state["messages"]) >= max_tool_rounds else "agent"

    def wrap_up(state):
        """强制分析师生成报告"""
        repeated = ", ".join(f"{name} x{n}" for name, n in Counter(calls(state["messages"])).most_common())
        logger.warning("%s used its %d tool rounds (%s); asking for its report",
                       spec.agent_node, max_tool_rounds, repeated)
        return agent({**state, "messages": [*state["messages"], HumanMessage(WRAP_UP)]})

    graph.add_node("tools", ToolNode(list(spec.tools)))  # 添加工具节点
    graph.add_node("wrap_up", wrap_up)  # 添加收尾节点
    graph.add_conditional_edges("agent", _tools_or_done, ["tools", END])  # 添加条件边
    graph.add_conditional_edges("tools", more_or_wrap_up, ["agent", "wrap_up"])  # 添加条件边
    graph.add_edge("wrap_up", END)  # 添加从 wrap_up 到 END 的边
    return graph.compile()  # 编译并返回图


class GraphSetup:
    """处理智能体图的设置和配置。"""

    def __init__(
        self,
        quick_thinking_llm: Any,  # 快速思考 LLM
        deep_thinking_llm: Any,  # 深度思考 LLM
        conditional_logic: ConditionalLogic,  # 条件逻辑
        max_tool_rounds: int,  # 最大工具调用轮次
    ):
        """使用必需的组件初始化。"""
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.conditional_logic = conditional_logic
        self.max_tool_rounds = max_tool_rounds

    def setup_graph(
        self, selected_analysts=("market", "social", "news", "fundamentals"), memory_node=None
    ):
        """设置并编译智能体工作流图。

        参数:
            selected_analysts (list): 要包含的分析师类型列表。选项包括：
                - "market": 市场分析师
                - "social": 情绪分析师
                - "news": 新闻分析师
                - "fundamentals": 基本面分析师
            memory_node: 结算过去决策并返回运行的 ``past_context`` 的节点。
                它与分析师一起运行。
        """
        plan = build_analyst_execution_plan(selected_analysts)  # 构建分析师执行计划

        # 分析师工厂函数映射
        analyst_factories = {
            "market": lambda: create_market_analyst(self.quick_thinking_llm),
            "social": lambda: create_sentiment_analyst(self.quick_thinking_llm),
            "news": lambda: create_news_analyst(self.quick_thinking_llm),
            "fundamentals": lambda: create_fundamentals_analyst(self.quick_thinking_llm),
        }

        # 创建研究团队节点
        bull_researcher_node = create_bull_researcher(self.quick_thinking_llm)
        bear_researcher_node = create_bear_researcher(self.quick_thinking_llm)
        research_manager_node = create_research_manager(self.deep_thinking_llm)
        trader_node = create_trader(self.quick_thinking_llm)

        # 创建风险管理团队节点
        aggressive_analyst = create_aggressive_debator(self.quick_thinking_llm)
        neutral_analyst = create_neutral_debator(self.quick_thinking_llm)
        conservative_analyst = create_conservative_debator(self.quick_thinking_llm)
        portfolio_manager_node = create_portfolio_manager(self.deep_thinking_llm)

        workflow = StateGraph(AgentState)  # 创建工作流状态图

        # 为每个分析师添加节点（每个分析师是一个子图）
        for spec in plan.specs:
            workflow.add_node(spec.agent_node,
                              _analyst_graph(spec, analyst_factories[spec.key](), self.max_tool_rounds))

        # 添加研究团队节点
        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)
        workflow.add_node("Trader", trader_node)
        # 添加风险管理团队节点
        workflow.add_node("Aggressive Analyst", aggressive_analyst)
        workflow.add_node("Neutral Analyst", neutral_analyst)
        workflow.add_node("Conservative Analyst", conservative_analyst)
        workflow.add_node("Portfolio Manager", portfolio_manager_node)

        # 分析师同时工作；研究辩论在它们都提交报告后开始。
        # 记忆日志与它们一起结算过去决策：只有投资组合经理读取其教训。
        first_steps = [spec.agent_node for spec in plan.specs]
        if memory_node is not None:
            workflow.add_node("Memory Log", memory_node)  # 添加记忆日志节点
            first_steps.append("Memory Log")
        for node in first_steps:
            workflow.add_edge(START, node)  # 从 START 到每个第一步节点的边
        workflow.add_edge(first_steps, "Bull Researcher")  # 所有第一步完成后转到 Bull Researcher

        # 两个研究辩论边共享完整的 DEBATE_PATH_MAP（#1088）。
        for debate_node in ("Bull Researcher", "Bear Researcher"):
            workflow.add_conditional_edges(
                debate_node,
                self.conditional_logic.should_continue_debate,  # 条件路由函数
                DEBATE_PATH_MAP,  # 路径映射
            )
        workflow.add_edge("Research Manager", "Trader")  # 研究经理到交易员
        workflow.add_edge("Trader", "Aggressive Analyst")  # 交易员到激进分析师
        # 所有三个风险边共享完整的 RISK_ANALYSIS_PATH_MAP（#1088）。
        for risk_node in ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"):
            workflow.add_conditional_edges(
                risk_node,
                self.conditional_logic.should_continue_risk_analysis,  # 条件路由函数
                RISK_ANALYSIS_PATH_MAP,  # 路径映射
            )

        workflow.add_edge("Portfolio Manager", END)  # 投资组合经理到结束

        return workflow  # 返回工作流
