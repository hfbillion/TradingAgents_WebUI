import logging  # 导入日志模块

from tradingagents.dataflows.config import get_config  # 导入配置获取函数
from tradingagents.dataflows.errors import (  # 导入错误类
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorUnavailableError,
)
# 导入 Alpha Vantage 供应商实现
from tradingagents.dataflows.vendors.alpha_vantage import (
    get_balance_sheet as get_alpha_vantage_balance_sheet,
    get_cashflow as get_alpha_vantage_cashflow,
    get_fundamentals as get_alpha_vantage_fundamentals,
    get_global_news as get_alpha_vantage_global_news,
    get_income_statement as get_alpha_vantage_income_statement,
    get_indicator as get_alpha_vantage_indicator,
    get_insider_transactions as get_alpha_vantage_insider_transactions,
    get_news as get_alpha_vantage_news,
    get_stock as get_alpha_vantage_stock,
)
# 导入 FRED 供应商实现
from tradingagents.dataflows.vendors.fred import get_macro_data as get_fred_macro_data
# 导入 Polymarket 供应商实现
from tradingagents.dataflows.vendors.polymarket import (
    get_prediction_markets as get_polymarket_prediction_markets,
)
# 导入 SEC EDGAR 供应商实现
from tradingagents.dataflows.vendors.sec_edgar import (
    get_balance_sheet as get_sec_edgar_balance_sheet,
    get_cashflow as get_sec_edgar_cashflow,
    get_income_statement as get_sec_edgar_income_statement,
)
# 导入 Yahoo Finance 基本面实现
from tradingagents.dataflows.vendors.yahoo.fundamentals import (
    get_balance_sheet as get_yfinance_balance_sheet,
    get_cashflow as get_yfinance_cashflow,
    get_fundamentals as get_yfinance_fundamentals,
    get_income_statement as get_yfinance_income_statement,
    get_insider_transactions as get_yfinance_insider_transactions,
)
# 导入 Yahoo Finance 市场数据实现
from tradingagents.dataflows.vendors.yahoo.market import (
    get_stock_stats_indicators_window,
    get_YFin_data_online,
)
# 导入 Yahoo Finance 新闻实现
from tradingagents.dataflows.vendors.yahoo.news import get_global_news_yfinance, get_news_yfinance

# 创建日志记录器
logger = logging.getLogger(__name__)

# 按类别组织的工具
TOOLS_CATEGORIES = {
    "core_stock_apis": {  # 核心股票 API
        "description": "OHLCV 股票价格数据",
        "tools": [
            "get_stock_data"
        ]
    },
    "technical_indicators": {  # 技术指标
        "description": "技术分析指标",
        "tools": [
            "get_indicators"
        ]
    },
    "fundamental_data": {  # 基本面数据
        "description": "公司基本面",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement"
        ]
    },
    "news_data": {  # 新闻数据
        "description": "新闻和内部交易数据",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
        ]
    },
    "macro_data": {  # 宏观数据
        "description": "宏观经济指标（利率、通胀、就业、增长）",
        "tools": [
            "get_macro_indicators",
        ]
    },
    "prediction_markets": {  # 预测市场
        "description": "前瞻性事件的市场隐含概率",
        "tools": [
            "get_prediction_markets",
        ]
    }
}

# 可选的增强类别。这些为新闻分析师添加宏观/事件上下文，
# 但不是决策的核心，因此此处的供应商失败会降级为哨兵而不是中止运行
# （一个错误的 LLM 提供的指标、缺少的密钥或网络故障不应该因为风味数据而使分析崩溃）。
# 核心类别（价格、基本面、新闻）仍然会引发异常，以便损坏的主供应商是明显的。
OPTIONAL_CATEGORIES = {"macro_data", "prediction_markets"}

# 方法到其供应商特定实现的映射
VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "alpha_vantage": get_alpha_vantage_stock,
        "yfinance": get_YFin_data_online,
    },
    # technical_indicators
    "get_indicators": {
        "alpha_vantage": get_alpha_vantage_indicator,
        "yfinance": get_stock_stats_indicators_window,
    },
    # fundamental_data
    "get_fundamentals": {
        "alpha_vantage": get_alpha_vantage_fundamentals,
        "yfinance": get_yfinance_fundamentals,
    },
    "get_balance_sheet": {
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "sec_edgar": get_sec_edgar_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
    },
    "get_cashflow": {
        "alpha_vantage": get_alpha_vantage_cashflow,
        "sec_edgar": get_sec_edgar_cashflow,
        "yfinance": get_yfinance_cashflow,
    },
    "get_income_statement": {
        "alpha_vantage": get_alpha_vantage_income_statement,
        "sec_edgar": get_sec_edgar_income_statement,
        "yfinance": get_yfinance_income_statement,
    },
    # news_data
    "get_news": {
        "alpha_vantage": get_alpha_vantage_news,
        "yfinance": get_news_yfinance,
    },
    "get_global_news": {
        "yfinance": get_global_news_yfinance,
        "alpha_vantage": get_alpha_vantage_global_news,
    },
    "get_insider_transactions": {
        "alpha_vantage": get_alpha_vantage_insider_transactions,
        "yfinance": get_yfinance_insider_transactions,
    },
    # macro_data
    "get_macro_indicators": {
        "fred": get_fred_macro_data,
    },
    # prediction_markets
    "get_prediction_markets": {
        "polymarket": get_polymarket_prediction_markets,
    },
}


def get_category_for_method(method: str) -> str:
    """获取包含指定方法的类别。"""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")  # 如果找不到，抛出异常


def get_vendor(category: str, method: str = None) -> str:
    """获取数据类别或特定工具方法的配置供应商。
    工具级别配置优先于类别级别配置。
    """
    config = get_config()  # 获取配置

    # 首先检查工具级别配置（如果提供了方法）
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]  # 返回工具级别配置

    # 回退到类别级别配置
    return config.get("data_vendors", {}).get(category, "default")  # 返回类别级别配置或默认值


def vendor_unavailable(method: str, error: Exception) -> str:
    """当每个供应商都被限流或无法访问时调用的返回值。"""
    return (
        f"DATA_UNAVAILABLE: no configured vendor could serve {method} right now "
        f"({error}). This says nothing about the instrument; report the "
        f"data as unavailable and do not estimate or fabricate values."
    )


def no_data_available(error: NoMarketDataError) -> str:
    """当每个回答的供应商都没有可用数据时调用的返回值。"""
    resolved = "" if error.canonical == error.symbol else f" (resolved to '{error.canonical}')"
    # 显示类型错误的详细信息（例如 "latest row is 2025-06-11 ... stale"）
    # 以便智能体看到具体原因 — 无效符号、无覆盖或陈旧数据 — 而不仅仅是通用的"不可用"。
    reason = f" ({error.detail})" if error.detail else ""
    return (
        f"NO_DATA_AVAILABLE: No usable market data for '{error.symbol}'{resolved} from "
        f"any configured vendor{reason}. The symbol may be invalid, delisted, "
        f"not covered, or the vendor returned stale data. Do not estimate or "
        f"fabricate values — report that data is unavailable for this symbol."
    )


def route_to_vendor(method: str, *args, **kwargs):
    """将方法调用路由到适当的供应商实现，支持回退。"""
    category = get_category_for_method(method)  # 获取方法类别
    vendor_config = get_vendor(category, method)  # 获取供应商配置
    primary_vendors = [v.strip() for v in vendor_config.split(',')]  # 解析供应商列表

    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")  # 如果方法不支持，抛出异常

    all_available_vendors = list(VENDOR_METHODS[method].keys())  # 获取所有可用供应商

    # 配置的供应商列表就是链：我们不会静默回退到用户未选择的供应商（#988/#289）
    # — 这会从意外来源返回数据并导致跨供应商不一致。
    # 对于多供应商回退，按顺序列出它们，例如 data_vendors="yfinance,alpha_vantage"。
    # "default" 哨兵（无显式配置）使用所有可用供应商。
    explicit = [v for v in primary_vendors if v and v != "default"]
    if explicit:
        vendor_chain = [v for v in explicit if v in VENDOR_METHODS[method]]  # 过滤有效的供应商
        if not vendor_chain:
            raise ValueError(
                f"Configured vendor(s) {explicit} not available for '{method}'. "
                f"Available: {all_available_vendors}."
            )
    else:
        vendor_chain = all_available_vendors  # 使用所有可用供应商

    last_no_data: NoMarketDataError | None = None  # 最后的无数据错误
    last_unavailable: VendorUnavailableError | None = None  # 最后的不可用错误
    failed: Exception | None = None  # 引发未类型化错误的供应商
    first_error: Exception | None = None  # 第一个错误
    for vendor in vendor_chain:  # 按顺序尝试供应商
        vendor_impl = VENDOR_METHODS[method][vendor]
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl

        try:
            return impl_func(*args, **kwargs)  # 执行供应商实现
        except VendorUnavailableError as e:
            logger.warning("Vendor %r unavailable for %s: %s; trying next vendor.", vendor, method, e)
            # 保留它，以便全不可用链可以说供应商是问题，而不是报告关于符号的任何内容。
            last_unavailable = e
            continue  # 尝试下一个供应商
        except VendorNotConfiguredError as e:
            logger.warning("Vendor %r not configured for %s; trying next vendor.", vendor, method)
            if first_error is None:
                first_error = e  # 如果没有其他供应商可以服务调用，则显示它。
            continue  # 尝试下一个供应商
        except NoMarketDataError as e:
            last_no_data = e  # 这里没有数据；另一个配置的供应商可能有
            continue  # 尝试下一个供应商
        except Exception as e:
            # 不要让一个供应商的失败在另一个可以服务时使调用崩溃，
            # 但永远不要静默吞没：损坏的主供应商必须在日志中可见（#989），
            # 而不是隐藏在回退的裁决后面。
            logger.warning("Vendor %r failed for %s: %s", vendor, method, e)
            if first_error is None:
                first_error = e
            failed = e
            continue  # 尝试下一个供应商

    # 限流或失败请求的供应商从未说过它是否有该符号，
    # 因此没有其他供应商的"无数据"可以代表整个链：
    # 将供应商报告为问题，而不是工具。它也绝不能结束运行。
    if last_unavailable is not None:
        return vendor_unavailable(method, last_unavailable)
    if failed is not None and last_no_data is not None:
        return vendor_unavailable(method, failed)

    # 每个回答的供应商都报告"无数据"：该符号实际上不可用。
    # 返回一个明确的、指导性的哨兵，而不是供应商特定的空字符串，
    # 以便智能体报告"不可用"而不是发明一个值。
    # 这优先于偶然的回退错误。
    if last_no_data is not None:
        if first_error is not None:
            # 供应商也遇到了真正的错误；在日志中显示它，
            # 以便无数据裁决不能隐藏损坏的主供应商（网络/认证等）。
            logger.warning(
                "Returning NO_DATA for %s, but a vendor errored earlier: %s",
                method, first_error,
            )
        return no_data_available(last_no_data)

    # 没有供应商返回数据，也没有报告干净的"无数据" — 显示第一个真正的错误
    # （例如主供应商的网络故障）。可选的增强类别降级为哨兵，
    # 以便风味数据不能中止运行。
    if first_error is not None:
        if category in OPTIONAL_CATEGORIES:
            logger.warning("Optional %s unavailable for %s: %s", category, method, first_error)
            return (
                f"DATA_UNAVAILABLE: optional {category} could not be retrieved "
                f"({first_error}). Proceed without it; do not fabricate values."
            )
        raise first_error  # 对于核心类别，抛出异常

    raise RuntimeError(f"No available vendor for '{method}'")  # 如果没有可用供应商，抛出运行时错误
