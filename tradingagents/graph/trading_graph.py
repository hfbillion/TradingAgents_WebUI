import hashlib  # 导入 hashlib 模块，用于生成配置签名的哈希值
import json  # 导入 json 模块，用于 JSON 序列化
import logging  # 导入 logging 模块，用于日志记录
import os  # 导入 os 模块，用于操作系统相关操作
from contextlib import contextmanager  # 导入 contextmanager，用于创建上下文管理器
from datetime import datetime  # 导入 datetime，用于日期时间处理
from pathlib import Path  # 导入 Path，用于路径操作
from typing import Any  # 导入 Any，用于类型注解

import tradingagents  # 导入 tradingagents 包本身，用于获取版本号
from tradingagents.agents.context import build_instrument_context, resolve_instrument_identity  # 导入工具上下文相关函数
from tradingagents.agents.rating import run_rating  # 导入评级运行函数
from tradingagents.dataflows.config import run_config, run_config_context, set_config  # 导入配置管理相关函数
from tradingagents.dataflows.date_window import get_current_date, is_historical  # 导入日期窗口相关函数
from tradingagents.dataflows.symbols import safe_ticker_component  # 导入安全股票代码组件函数
from tradingagents.default_config import DEFAULT_CONFIG  # 导入默认配置
from tradingagents.llm_clients import create_tier_client, tier_provider  # 导入 LLM 客户端创建函数
from tradingagents.memory import TradingMemoryLog, settlement  # 导入记忆日志和结算模块
from tradingagents.memory.reflection import Reflector  # 导入反思模块
from tradingagents.reporting import write_report_tree  # 导入报告写入函数

from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, thread_id  # 导入检查点相关函数
from .conditional_logic import ConditionalLogic  # 导入条件逻辑类
from .propagation import Propagator  # 导入传播器类
from .setup import GraphSetup  # 导入图设置类

# 创建日志记录器
logger = logging.getLogger(__name__)


def _validate_trade_date(trade_date) -> str:
    """验证交易日期，返回规范的 YYYY-MM-DD 格式字符串，不能晚于今天。"""
    value = str(trade_date)  # 转换为字符串
    try:
        # 尝试解析日期并检查格式是否规范
        canonical = datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        canonical = False  # 解析失败，格式无效
    if not canonical:
        # 如果格式不规范，抛出异常
        raise ValueError(f"trade_date must be a date in YYYY-MM-DD format, got {trade_date!r}")
    if value > get_current_date():
        # 如果日期在未来，抛出异常
        raise ValueError(f"trade_date cannot be in the future: {value}")
    return value  # 返回验证通过的日期


# 不影响运行输出的配置键：文件存储位置、是否检查点、重试次数
# 这些键不会改变运行生成的内容，因此不包括在检查点签名中
_NOT_IN_SIGNATURE = frozenset({
    "results_dir", "data_cache_dir", "memory_log_path", "checkpoint_enabled", "llm_max_retries",
})


class TradingAgentsGraph:
    """协调交易智能体框架的主类。"""

    def __init__(
        self,
        selected_analysts=("market", "social", "news", "fundamentals"),  # 默认选择的分析师类型
        debug=False,  # 是否以调试模式运行
        config: dict[str, Any] = None,  # 配置字典，如果为 None 则使用默认配置
        callbacks: list | None = None,  # 可选的回调处理器列表（例如用于跟踪 LLM/工具统计）
    ):
        """初始化交易智能体图和组件。

        参数:
            selected_analysts: 要包含的分析师类型列表
            debug: 是否以调试模式运行
            config: 配置字典。如果为 None，使用默认配置
            callbacks: 可选的回调处理器列表（例如用于跟踪 LLM/工具统计）
        """
        self.debug = debug  # 保存调试模式标志
        self.config = config or DEFAULT_CONFIG  # 保存配置，使用默认配置作为后备
        self.callbacks = callbacks or []  # 保存回调列表

        set_config(self.config)  # 设置全局配置

        # 创建数据缓存目录和结果目录（如果不存在）
        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        # 准备额外的参数（如果有回调）
        extra = {"callbacks": self.callbacks} if self.callbacks else {}
        # 创建深度思考 LLM 客户端（用于研究经理、投资组合经理）
        self.deep_thinking_llm = create_tier_client(self.config, "deep", **extra).get_llm()
        # 创建快速思考 LLM 客户端（用于分析师、研究员、辩论者、交易员）
        self.quick_thinking_llm = create_tier_client(self.config, "quick", **extra).get_llm()

        # 创建记忆日志实例
        self.memory_log = TradingMemoryLog(self.config)

        # 创建条件逻辑实例（用于辩论和风险讨论的路由）
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        # 分析师每个工具轮次需要两个图步骤，加上第一轮和收尾
        # 如果限制超过递归限制，会在那里结束运行而不是正常完成
        max_tool_rounds, max_recur_limit = self.config["max_tool_rounds"], self.config["max_recur_limit"]
        if 2 * max_tool_rounds + 2 >= max_recur_limit:
            # 验证配置的合理性
            raise ValueError(
                f"max_tool_rounds={max_tool_rounds} needs max_recur_limit above {2 * max_tool_rounds + 2}"
            )
        # 创建图设置实例
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.conditional_logic,
            max_tool_rounds,
        )

        # 创建传播器实例（用于状态初始化和传播）
        self.propagator = Propagator(
            max_recur_limit=max_recur_limit,
        )
        # 创建反思器实例（用于生成决策反思）
        self.reflector = Reflector(self.quick_thinking_llm)

        # 保存影响图形状的运行选择，用于检查点签名
        self.selected_analysts = tuple(selected_analysts)

        # 设置图：保留工作流以便使用检查点重新编译
        self.workflow = self.graph_setup.setup_graph(selected_analysts, memory_node=self._memory_step)
        self.graph = self.workflow.compile()  # 编译图
        self._checkpointer_ctx = None  # 检查点上下文（初始为 None）
        self._resuming = False  # 是否正在恢复运行（初始为 False）

    def resolve_instrument_context(self, ticker: str, asset_type: str = "stock",
                                   trade_date: str | None = None) -> str:
        """解析股票代码标识一次并返回完整的工具上下文。

        确定性的 yfinance 查找（缓存，失败开放）注入到上下文字符串中
        以便每个智能体锚定到真实公司而不是从价格图表中产生幻觉（#814）。
        propagate() 路径和 CLI 都调用此函数，因此解析的标识符可以到达整个图
        无论入口点如何。
        """
        identity = resolve_instrument_identity(ticker)  # 解析股票代码标识
        return build_instrument_context(ticker, asset_type, identity, trade_date)  # 构建工具上下文

    def _memory_as_of(self, trade_date) -> str | None:
        """过去上下文教训的时间点截止（#1251）。

        历史/回测运行（交易日期在今天之前）将教训过滤到交易日期之前已解决的教训。
        当前日期运行返回 None，禁用过滤器，以便实时行为和迁移前条目
        （没有存储的解决日期）不受影响。
        """
        return str(trade_date) if is_historical(trade_date) else None  # 如果是历史日期则返回日期，否则返回 None

    def _run_signature(self, asset_type: str, portfolio=None) -> str:
        """如果更改则必须使检查点无效的运行输入。

        键入检查点线程 ID，以便在不同的分析师选择、辩论/风险深度或资产模式下
        恢复时重新开始而不是静默继续之前的图（#1089）。
        配置的其余部分也算在内（提供商、模型、端点、语言、供应商、限制）：
        恢复不能携带其他设置产生的报告。只有运行保持文件的位置和重试方式被排除。
        """
        # 过滤掉不影响运行的配置键
        settings = {k: v for k, v in self.config.items() if k not in _NOT_IN_SIGNATURE}
        # 生成配置的 SHA256 哈希值（取前12位）
        digest = hashlib.sha256(json.dumps(settings, sort_keys=True, default=str).encode()).hexdigest()[:12]
        # 构建运行签名字符串
        return "|".join([
            "analysts=" + ",".join(self.selected_analysts),  # 分析师列表
            f"debate={self.config['max_debate_rounds']}",  # 辩论轮数
            f"risk={self.config['max_risk_discuss_rounds']}",  # 风险讨论轮数
            f"asset={asset_type}",  # 资产类型
            # None、空书和更改的书是三种不同的运行
            f"portfolio={portfolio.fingerprint() if portfolio is not None else 'none'}",  # 投资组合指纹
            # 布局本身：当分析师一个接一个运行时保存的检查点
            # 具有此图不再有的待处理节点，在 Memory Log 步骤之前保存的
            # 检查点会在没有教训的情况下恢复
            "analysts=parallel",  # 分析师并行运行
            "memory=parallel",  # 记忆并行运行
            f"settings={digest}",  # 配置哈希
        ])

    def propagate(self, company_name, trade_date, asset_type: str = "stock", portfolio=None):
        """在特定日期为公司运行交易智能体图。

        ``asset_type`` 在股票管道（默认）和加密货币管道（``"crypto"``）之间选择
        该管道在 #567 中发布 — CLI 从股票代码自动检测；程序化调用者显式传递它。
        当在配置中设置 ``checkpoint_enabled`` 时，图使用每个股票的 SqliteSaver 重新编译
        以便崩溃的运行可以在后续使用相同股票+日期的调用中从最后成功的节点恢复。

        返回 ``(final_state, signal)`` 其中 ``signal`` 是 5 级评级之一
        （Buy / Overweight / Hold / Underweight / Sell）或当决策没有可解析的评级时返回 ``"REVIEW"``（#1170）；
        在将其映射到 PortfolioRating 枚举之前使用 ``tradingagents.agents.rating.is_review`` 保护。
        """
        trade_date = _validate_trade_date(trade_date)  # 验证交易日期

        # 使用配置上下文和检查点作用域运行图
        with run_config(self.config), \
                self.checkpoint_scope(company_name, trade_date, asset_type, portfolio) as thread_id_value:
            return self._run_graph(
                company_name, trade_date, asset_type=asset_type,
                checkpoint_thread_id=thread_id_value, portfolio=portfolio,
            )

    def begin_checkpoint(self, company_name, trade_date, asset_type: str = "stock", portfolio=None) -> str | None:
        """使用每个股票的检查点重新编译图并返回要注入到 stream/invoke ``config`` 中的
        ``thread_id``（当禁用检查点时返回 ``None``）。

        在 ``finally`` 中与 :meth:`end_checkpoint` 配对每次调用。
        ``propagate``（通过 :meth:`checkpoint_scope`）和 CLI 流路径都使用此函数
        以便 ``--checkpoint`` 实际上可以恢复（#1249）；
        之前设置只存在于 ``propagate`` 内部，CLI 流式传输无检查点的图，
        使标志无效。
        """
        self._resuming = False  # 重置恢复标志
        if not self.config.get("checkpoint_enabled"):
            return None  # 如果未启用检查点，返回 None
        signature = self._run_signature(asset_type, portfolio)  # 生成运行签名
        self._checkpointer_ctx = get_checkpointer(self.config["data_cache_dir"], company_name)  # 获取检查点上下文
        saver = self._checkpointer_ctx.__enter__()  # 进入检查点上下文
        self.graph = self.workflow.compile(checkpointer=saver)  # 使用检查点重新编译图

        # 检查是否存在检查点
        step = checkpoint_step(
            self.config["data_cache_dir"], company_name, str(trade_date), signature
        )
        self._resuming = step is not None  # 设置恢复标志
        if step is not None:
            logger.info("Resuming from step %d for %s on %s", step, company_name, trade_date)  # 记录恢复信息
        else:
            logger.info("Starting fresh for %s on %s", company_name, trade_date)  # 记录新运行信息
        return thread_id(company_name, str(trade_date), signature)  # 返回线程 ID

    def checkpoint_input(self, init_state):
        """要 stream/invoke 的值：``None`` 以恢复现有检查点，
        否则为新运行的初始状态。

        LangGraph 在使用 ``None`` 调用时恢复中断的线程；
        重新传递初始状态会通过消息缩减器附加它，
        在恢复的状态中重复消息（#1249）。
        """
        return None if self._resuming else init_state  # 如果正在恢复则返回 None，否则返回初始状态

    def end_checkpoint(self):
        """在检查点运行后恢复普通的无检查点图。"""
        if self._checkpointer_ctx is not None:
            self._checkpointer_ctx.__exit__(None, None, None)  # 退出检查点上下文
            self._checkpointer_ctx = None  # 清除检查点上下文
            self.graph = self.workflow.compile()  # 重新编译无检查点的图
        self._resuming = False  # 重置恢复标志

    @contextmanager
    def checkpoint_scope(self, company_name, trade_date, asset_type: str = "stock", portfolio=None):
        """begin/end_checkpoint 的上下文管理器形式，用于 propagate 路径。"""
        try:
            yield self.begin_checkpoint(company_name, trade_date, asset_type, portfolio)  # 开始检查点
        finally:
            self.end_checkpoint()  # 确保结束检查点

    def clear_checkpoint_on_success(self, company_name, trade_date, asset_type: str = "stock", portfolio=None):
        """删除已完成运行的检查点，以便后续运行重新开始（#1249）。"""
        if self.config.get("checkpoint_enabled"):
            clear_checkpoint(
                self.config["data_cache_dir"], company_name, str(trade_date),
                self._run_signature(asset_type, portfolio),
            )

    def run_settings(self) -> dict:
        """生成此图运行的内容，用于保存的报告和状态日志。

        允许列表：端点（backend_url 可以携带凭据）、密钥和本地路径永远不会被记录。
        """
        cfg = self.config
        return {
            "version": tradingagents.__version__,  # 版本号
            "llm_provider": cfg.get("llm_provider"),  # LLM 提供商
            "deep_think_provider": tier_provider(cfg, "deep") if cfg.get("llm_provider") else None,  # 深度思考提供商
            "deep_think_llm": cfg.get("deep_think_llm"),  # 深度思考模型
            "quick_think_provider": tier_provider(cfg, "quick") if cfg.get("llm_provider") else None,  # 快速思考提供商
            "quick_think_llm": cfg.get("quick_think_llm"),  # 快速思考模型
            "analysts": list(self.selected_analysts),  # 分析师列表
            "max_debate_rounds": cfg.get("max_debate_rounds"),  # 最大辩论轮数
            "max_risk_discuss_rounds": cfg.get("max_risk_discuss_rounds"),  # 最大风险讨论轮数
            "output_language": cfg.get("output_language"),  # 输出语言
            "data_vendors": dict(cfg.get("data_vendors") or {}),  # 数据供应商
            "tool_vendors": dict(cfg.get("tool_vendors") or {}),  # 工具供应商
        }

    def save_reports(self, final_state, ticker, save_path=None, html=True) -> Path:
        """为完成的运行写入报告树，就像 CLI 一样。

        程序化调用者获得与 CLI 产生的相同的磁盘报告。
        传递显式的 ``save_path`` 或让它在 ``results_dir`` 下默认；
        除非 ``html`` 为 False，否则报告也会写为一个 HTML 页面。
        """
        if save_path is None:
            save_path = self.default_report_path(ticker)  # 使用默认报告路径
        return write_report_tree(final_state, ticker, save_path, settings=self.run_settings(), html=html)

    def default_report_path(self, ticker) -> Path:
        """运行的报告保存位置（除非另有说明）：在 results_dir 下，带时间戳。"""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")  # 生成时间戳
        return Path(self.config["results_dir"]) / "reports" / f"{safe_ticker_component(ticker)}_{stamp}"

    def create_run_state(self, company_name, trade_date, asset_type: str = "stock", portfolio=None):
        """构建运行的初始状态；propagate() 和 CLI 都从这里开始。

        为每个智能体注入解析的工具标识（#814）。
        记忆日志的教训不在这里：图的 Memory Log 步骤与分析师一起结算并加载它们（见 ``_memory_step``）。
        """
        return self.propagator.create_initial_state(
            company_name,
            trade_date,
            asset_type=asset_type,
            instrument_context=self.resolve_instrument_context(company_name, asset_type, trade_date),  # 注入工具上下文
            portfolio_context=portfolio.render(company_name) if portfolio is not None else "",  # 注入投资组合上下文
        )

    def _memory_step(self, state):
        """图的 Memory Log 步骤，与分析师一起运行（#1428）：结算每个股票的到期决策（#1445），
        然后返回交易日期之前已知的教训，供投资组合经理使用（#1251）。

        结算获取价格并要求模型为每个决策进行反思，
        因此它与分析师一起运行而不是在它们之前运行。
        此处的失败不会结束运行：分析师的工作被保留，
        日志中已有的教训被使用，报告说明无法结算的内容。
        """
        note = ""  # 初始化备注
        try:
            # 另一个运行结算同一日志会完成工作；此运行继续
            done = self.settle_all_pending(wait=False)  # 结算所有待处理决策
            if done.failed:
                note = (f"{len(done.failed)} past decision(s) could not be settled this run "
                        "and stay pending.")  # 记录失败的决策
        except Exception as exc:
            logger.warning("Settling past decisions failed: %s", exc)  # 记录警告
            note = f"Past decisions could not be settled this run ({type(exc).__name__}); they stay pending."
        try:
            # 获取过去上下文（教训）
            past_context = self.memory_log.get_past_context(
                state["company_of_interest"], as_of=self._memory_as_of(state["trade_date"]))
        except Exception as exc:
            logger.warning("Reading the memory log failed: %s", exc)  # 记录警告
            past_context = ""
            note = note or f"The memory log could not be read this run ({type(exc).__name__})."
        return {"past_context": past_context, "memory_note": note}  # 返回过去上下文和备注

    def settle_pending(self, company_name) -> settlement.Settlement:
        """结算此股票持有窗口现已交易的决策。

        运行与其分析师一起结算每个到期决策，因此其自己的决策保持待处理直到后续运行。
        完成分析股票的调用者（回测扫描、计划作业）调用此函数立即结算它。
        返回已结算的内容和失败的内容。
        """
        with run_config(self.config):
            return settlement.settle_pending(company_name, self.memory_log, self.reflector, self.config)

    def settle_all_pending(self, wait: bool = True) -> settlement.Settlement:
        """结算每个股票持有窗口现已交易的决策（#1445）。

        对于股票轮换的调度器：它停止分析的股票会将其决策保持待处理，
        它们的教训将从后续运行中排除。
        使用 ``wait=False`` 不会等待已在同一日志上运行的通过。
        """
        with run_config(self.config):
            return settlement.settle_all_pending(self.memory_log, self.reflector, self.config, wait=wait)

    def record_decision(self, company_name, trade_date, final_state):
        """记录完成的运行：其状态日志，以及其在记忆日志中的决策，
        用于下一次相同股票运行的反思。propagate() 和 CLI 都在此结束。"""
        self._log_state(trade_date, final_state)  # 记录状态日志
        decision = final_state.get("final_trade_decision")  # 获取最终决策
        if not decision:
            logger.warning("No final decision for %s on %s; nothing added to the memory log",
                           company_name, trade_date)
            return
        self.memory_log.store_decision(
            ticker=company_name, trade_date=trade_date, final_trade_decision=decision,
            rating=run_rating(final_state),  # 存储决策到记忆日志
        )

    def _run_graph(self, company_name, trade_date, asset_type: str = "stock",
                   checkpoint_thread_id: str | None = None, portfolio=None):
        """执行图并将结果状态写入磁盘和记忆日志。"""
        init_agent_state = self.create_run_state(company_name, trade_date, asset_type, portfolio)  # 创建初始状态
        args = self.propagator.get_graph_args()  # 获取图参数

        # 注入检查点线程 ID（来自 checkpoint_scope）以便相同的股票+日期+图形状恢复；
        # 不同的重新开始（#1089）。
        if checkpoint_thread_id is not None:
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = checkpoint_thread_id

        # None 恢复现有检查点；init_agent_state 重新开始（#1249）。
        graph_input = self.checkpoint_input(init_agent_state)
        if self.debug:
            # 状态重复之前的消息，因此每个打印一次（#1027）。
            final_state, printed = {}, set()
            for messages, state in self.stream_run(graph_input, **args):
                for msg in messages:
                    key = getattr(msg, "id", None) or (type(msg).__name__, getattr(msg, "content", None))
                    if key not in printed:
                        printed.add(key)
                        msg.pretty_print()  # 打印消息
                if state is not None:
                    final_state.update(state)  # 更新状态
        else:
            final_state = self.graph.invoke(graph_input, **args)  # 调用图

        self.record_decision(company_name, trade_date, final_state)  # 记录决策

        # 成功完成后清除检查点以避免陈旧状态。
        self.clear_checkpoint_on_success(company_name, trade_date, asset_type, portfolio)

        return final_state, run_rating(final_state)  # 返回最终状态和评级

    def stream_run(self, graph_input, **args):
        """将运行流式传输为 ``(messages, state)`` 对。

        ``messages`` 是智能体的消息，包括分析师的消息。
        ``state`` 是顶级步骤后的运行状态；
        对于分析师图内的步骤，它是分析师提交后的报告，否则为 None。

        每个分析师在自己的图中工作，运行的状态仅在最慢的分析师完成后才采用分析师的报告，
        因此它们的消息和报告来自它们自己完成的步骤（"tasks"），当它们发生时。
        """
        args = {**args, "stream_mode": ["values", "tasks"]}  # 设置流模式
        # 图自己的配置服务于每个工具调用，就像在 propagate() 中一样，
        # 即使进程范围的配置自那时起已更改。每个步骤在运行的上下文中运行，
        # 因此调用者在步骤之间保持自己的配置。
        context = run_config_context(self.config)
        stream = context.run(self.graph.stream, graph_input, subgraphs=True, **args)
        try:
            while (step := context.run(next, stream, None)) is not None:
                namespace, mode, chunk = step
                if namespace:
                    result = chunk.get("result") if mode == "tasks" else None
                    if isinstance(result, dict):
                        report = {k: v for k, v in result.items() if k != "messages" and v}
                        if result.get("messages") or report:
                            yield result.get("messages", []), report or None
                elif mode == "values":
                    yield chunk.get("messages", []), chunk
        finally:
            context.run(stream.close)  # 关闭流

    def _log_state(self, trade_date, final_state):
        """将运行的最终状态写入 JSON，保存在运行自己的股票下。"""
        entry = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
            },
            "trader_investment_plan": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
            "final_rating": run_rating(final_state),
            "run_settings": self.run_settings(),
        }

        # 拒绝会转义结果目录的股票代码。
        safe_ticker = safe_ticker_component(final_state["company_of_interest"])
        directory = Path(self.config["results_dir"]) / safe_ticker / "TradingAgentsStrategy_logs"
        directory.mkdir(parents=True, exist_ok=True)

        log_path = directory / f"full_states_log_{trade_date}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            # 报告可以是任何语言，此文件由人阅读。
            json.dump(entry, f, indent=4, ensure_ascii=False)
