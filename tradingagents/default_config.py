import os  # 导入操作系统模块，用于处理文件路径和环境变量

# 定义 TradingAgents 的主目录路径，位于用户主目录下的 .tradingagents 文件夹
_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")

# 环境变量到配置键的映射表（单一事实来源）
# 如果要添加新的配置键的环境变量覆盖，只需在此添加一行
# 无需修改入口点脚本。类型转换由现有默认值的类型驱动
# 因此用户可以在 .env 文件中继续使用纯字符串
_ENV_OVERRIDES = {
    "TRADINGAGENTS_LLM_PROVIDER":         "llm_provider",           # LLM 提供商
    "TRADINGAGENTS_DEEP_THINK_LLM":       "deep_think_llm",         # 深度思考模型（用于复杂推理）
    "TRADINGAGENTS_QUICK_THINK_LLM":      "quick_think_llm",        # 快速思考模型（用于快速任务）
    "TRADINGAGENTS_DEEP_THINK_PROVIDER":       "deep_think_provider",      # 深度思考层级提供商
    "TRADINGAGENTS_QUICK_THINK_PROVIDER":      "quick_think_provider",     # 快速思考层级提供商
    "TRADINGAGENTS_DEEP_THINK_BACKEND_URL":    "deep_think_backend_url",    # 深度思考后端 URL
    "TRADINGAGENTS_QUICK_THINK_BACKEND_URL":   "quick_think_backend_url",   # 快速思考后端 URL
    "TRADINGAGENTS_LLM_BACKEND_URL":      "backend_url",              # LLM 后端 URL
    "TRADINGAGENTS_OUTPUT_LANGUAGE":      "output_language",          # 输出语言
    "TRADINGAGENTS_MAX_DEBATE_ROUNDS":    "max_debate_rounds",        # 最大辩论轮数
    "TRADINGAGENTS_MAX_RISK_ROUNDS":      "max_risk_discuss_rounds",   # 最大风险讨论轮数
    "TRADINGAGENTS_MAX_TOOL_ROUNDS":      "max_tool_rounds",          # 最大工具调用轮数
    "TRADINGAGENTS_CHECKPOINT_ENABLED":   "checkpoint_enabled",       # 是否启用检查点
    "TRADINGAGENTS_BENCHMARK_TICKER":     "benchmark_ticker",         # 基准股票代码
    "TRADINGAGENTS_TEMPERATURE":          "temperature",              # 采样温度
    "TRADINGAGENTS_LLM_MAX_RETRIES":      "llm_max_retries",          # LLM 最大重试次数
    "TRADINGAGENTS_MAX_TOKENS":           "max_tokens",               # 最大输出 token 数
    # 提供商特定的推理/思考控制（None = 使用各提供商自己的默认值）
    # 可在此设置用于非交互式运行；CLI 也提供交互式选择
    # 当设置了匹配的环境变量时，将跳过交互式选择
    "TRADINGAGENTS_GOOGLE_THINKING_LEVEL":   "google_thinking_level",   # Google 思考级别
    "TRADINGAGENTS_OPENAI_REASONING_EFFORT": "openai_reasoning_effort", # OpenAI 推理努力程度
    "TRADINGAGENTS_ANTHROPIC_EFFORT":        "anthropic_effort",        # Anthropic 努力程度
}


# 布尔值的真值表示（用于环境变量解析）
_BOOL_TRUE = ("true", "1", "yes", "on")
# 布尔值的假值表示（用于环境变量解析）
_BOOL_FALSE = ("false", "0", "no", "off")


def _coerce(value: str, reference):
    """将环境变量字符串强制转换为现有默认值的类型。

    无效的值会抛出 ValueError 而不是静默回退到默认值
    拼写错误的布尔值（如 "treu"）或非数字整数应该在启动时
    明确失败，而不是静默地错误配置无人值守的运行。
    """
    # 如果参考值是布尔类型，则将字符串转换为布尔值
    if isinstance(reference, bool):
        normalized = value.strip().lower()  # 去除空格并转为小写
        if normalized in _BOOL_TRUE:  # 检查是否为真值
            return True
        if normalized in _BOOL_FALSE:  # 检查是否为假值
            return False
        # 如果都不是，抛出异常
        raise ValueError(
            f"expected a boolean ({'/'.join(_BOOL_TRUE + _BOOL_FALSE)}), got {value!r}"
        )
    # 如果参考值是整数类型（且不是布尔），则转换为整数
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    # 如果参考值是浮点数类型，则转换为浮点数
    if isinstance(reference, float):
        return float(value)
    # 其他情况直接返回字符串
    return value


def _apply_env_overrides(config: dict) -> dict:
    """将 TRADINGAGENTS_* 环境变量应用到配置字典中（原地修改）。"""
    # 遍历环境变量映射表
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)  # 获取环境变量的值
        # 如果环境变量未设置或为空，跳过
        if raw is None or raw == "":
            continue
        try:
            # 使用 _coerce 函数将环境变量值转换为正确的类型
            # 并更新配置字典
            config[key] = _coerce(raw, config.get(key))
        except ValueError as exc:
            # 如果转换失败，抛出带有环境变量名的异常
            raise ValueError(f"Invalid value for {env_var}: {exc}") from exc
    return config


def build_default_config() -> dict:
    """构建默认配置字典，并应用 TRADINGAGENTS_* 环境变量覆盖。

    在包导入时读取为 DEFAULT_CONFIG；再次调用可以查看当前环境状态。
    """
    return _apply_env_overrides({
        # 结果目录：存储分析报告和日志
        # 优先使用环境变量 TRADINGAGENTS_RESULTS_DIR，否则使用默认路径
        "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR") or os.path.join(_TRADINGAGENTS_HOME, "logs"),
        # 数据缓存目录：存储从数据供应商获取的缓存数据
        # 优先使用环境变量 TRADINGAGENTS_CACHE_DIR，否则使用默认路径
        "data_cache_dir": os.getenv("TRADINGAGENTS_CACHE_DIR") or os.path.join(_TRADINGAGENTS_HOME, "cache"),
        # 记忆日志路径：存储交易决策和反思的 markdown 文件
        # 优先使用环境变量 TRADINGAGENTS_MEMORY_LOG_PATH，否则使用默认路径
        "memory_log_path": os.getenv("TRADINGAGENTS_MEMORY_LOG_PATH") or os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md"),
        # 已解析记忆日志条目的可选上限
        # 设置后，一旦超过此限制，最旧的已解析条目将被修剪
        # 待处理条目永远不会被修剪。None 表示完全禁用轮换
        "memory_log_max_entries": None,

        # ==================== LLM 设置 ====================
        # LLM 提供商：默认使用 OpenAI
        "llm_provider": "openai",
        # 深度思考模型：用于复杂推理任务（研究经理、投资组合经理）
        "deep_think_llm": "gpt-6-sol",
        # 快速思考模型：用于快速任务（分析师、研究员、辩论者、交易员）
        "quick_think_llm": "gpt-6-luna",
        # 后端 URL：当为 None 时，每个提供商的客户端使用自己的默认端点
        # （OpenAI 用 api.openai.com，Gemini 用 generativelanguage.googleapis.com 等）
        # CLI 在用户选择提供商会覆盖此设置
        # 在此处保留提供商特定的 URL 会导致泄漏（例如 OpenAI 的 /v1 之前被转发到 Gemini，产生错误的请求 URL）
        "backend_url": None,
        # 分层可能指定自己的提供商和端点（#1440）：
        # 快速层级服务于分析师、研究员、辩论者和交易员
        # 深度层级服务于经理
        # None 表示该层级使用 llm_provider 和 backend_url
        "quick_think_provider": None,
        "deep_think_provider": None,
        "quick_think_backend_url": None,
        "deep_think_backend_url": None,
        # 提供商特定的思考配置
        "google_thinking_level": None,      # "high", "minimal" 等
        "openai_reasoning_effort": None,    # "medium", "high", "low"
        "anthropic_effort": None,           # "high", "medium", "low"
        # 采样温度：设置时转发给每个提供商
        # None 表示每个提供商使用自己的默认值
        # 较低的值减少遵守此模型的运行间变化
        # 推理模型很大程度上忽略它，没有设置能使 LLM 输出在运行间完全相同（见 README）
        "temperature": None,
        # SDK 重试预算：转发给每个提供商聊天客户端
        # None 表示每个提供商/SDK 使用自己的默认值（通常为 2）
        # 提高此值可以在速率限制的部署中度过突发 429 限流，而不是中止运行（#1091）
        "llm_max_retries": None,
        # 输出 token 上限：转发给每个提供商聊天客户端
        # None 表示每个提供商使用自己的默认值
        # 设置此值可以限制发出无限推理/输出并挂起或触发网关空闲超时的模型（例如某些 deepseek-v4-flash 部署，#1204）
        "max_tokens": None,

        # ==================== 检查点/恢复 ====================
        # 检查点/恢复：当为 True 时，LangGraph 在每个节点后保存状态
        # 以便崩溃的运行可以从最后成功的步骤恢复
        "checkpoint_enabled": False,

        # ==================== 输出语言 ====================
        # 分析师报告和最终决策的输出语言
        # 内部智能体辩论保持英语以保证推理质量
        "output_language": "English",

        # ==================== 辩论和讨论设置 ====================
        # 最大辩论轮数：看涨/看跌研究员之间的辩论轮数
        "max_debate_rounds": 1,
        # 最大风险讨论轮数：激进/保守/中立分析师之间的讨论轮数
        "max_risk_discuss_rounds": 1,
        # 最大递归限制：图的最大递归深度
        "max_recur_limit": 100,
        # 最大工具调用轮数：分析师在被要求生成报告之前可以进行的工具调用轮数
        "max_tool_rounds": 20,

        # ==================== 新闻/数据获取参数 ====================
        # 增加此值以延长回溯策略或扩大宏观覆盖范围
        # 减少此值以减少智能体提示中的 token 使用量
        "news_article_limit": 20,             # 每个股票的最大文章数（股票新闻）
        "global_news_article_limit": 10,      # 全球/宏观新闻的最大文章数
        "global_news_lookback_days": 7,       # 宏观新闻回溯窗口（天）
        # get_global_news 用于获取宏观标题的搜索查询
        # 扩展或替换以扩大地理/行业覆盖范围
        "global_news_queries": [
            "Federal Reserve interest rates inflation",      # 美联储利率通胀
            "S&P 500 earnings GDP economic outlook",          # 标普500收益GDP经济展望
            "geopolitical risk trade war sanctions",         # 地缘政治风险贸易战制裁
            "ECB Bank of England BOJ central bank policy",  # 欧央行英央行日央行央行政策
            "oil commodities supply chain energy",           # 石油大宗商品供应链能源
        ],

        # ==================== 数据供应商配置 ====================
        # 类别级别配置（类别中所有工具的默认值）
        # 配置的值是确切的供应商链 — 请求不会静默路由到您未选择的供应商
        # 对于有序回退，列出多个，例如 "yfinance,alpha_vantage"
        # "default" 使用所有可用的供应商
        "data_vendors": {
            "core_stock_apis": "yfinance",       # 核心股票 API：选项 alpha_vantage, yfinance
            "technical_indicators": "yfinance",  # 技术指标：选项 alpha_vantage, yfinance
            # 财务报表来自 SEC EDGAR（美国申报者），然后是 Yahoo
            # 概述和内部交易工具（SEC EDGAR 不提供）来自 Yahoo
            "fundamental_data": "sec_edgar,yfinance",  # 基本面数据：选项 sec_edgar, alpha_vantage, yfinance
            "news_data": "yfinance",             # 新闻数据：选项 alpha_vantage, yfinance
            "macro_data": "fred",                # 宏观数据：选项 fred（需要 FRED_API_KEY）
            "prediction_markets": "polymarket",  # 预测市场：选项 polymarket（无需密钥）
        },
        # 工具级别配置（优先于类别级别）
        "tool_vendors": {
            # 示例："get_stock_data": "alpha_vantage",  # 覆盖类别默认值
        },

        # ==================== 基准配置 ====================
        # 反思层中 alpha 计算的基准
        # ``benchmark_ticker``（设置时）覆盖所有股票的后缀映射
        # 保持为 None 以使用 ``benchmark_map`` 根据股票交易所后缀自动检测
        # SPY 仍然是美国默认值，因此对于美国股票
        # 反思标签保持读取 "Alpha vs SPY"
        # 而非美国股票自动获得其区域指数
        # 从分析日期后的第一个收盘价开始测量决策结果的交易日数
        # 用于反思和回测数据
        "holding_period_days": 5,
        "benchmark_ticker": None,
        "benchmark_map": {
            ".NS":  "^NSEI",       # NSE 印度（Nifty 50）
            ".BO":  "^BSESN",      # BSE 印度（Sensex）
            ".T":   "^N225",       # 东京（日经 225）
            ".TW":  "^TWII",       # 台湾（TAIEX）
            ".TWO": "^TWII",       # 台北 OTC（TPEx 没有 Yahoo 指数；使用 TAIEX）
            ".KS":  "^KS11",       # 韩国（KOSPI）
            ".KQ":  "^KQ11",       # 韩国（KOSDAQ）
            ".HK":  "^HSI",        # 香港（恒生指数）
            ".SI":  "^STI",        # 新加坡（海峡时报）
            ".L":   "^FTSE",       # 伦敦（富时 100）
            ".DE":  "^GDAXI",      # 德国（DAX）
            ".PA":  "^FCHI",       # 巴黎（CAC 40）
            ".AS":  "^AEX",        # 阿姆斯特丹（AEX）
            ".SW":  "^SSMI",       # 瑞士（SMI）
            ".MI":  "FTSEMIB.MI",  # 米兰（FTSE MIB）
            ".TO":  "^GSPTSE",     # 多伦多（TSX 综合指数）
            ".AX":  "^AXJO",       # 澳大利亚（ASX 200）
            ".SS":  "000001.SS",   # 上海（上证综指）
            ".SZ":  "399001.SZ",   # 深圳（深证成指）
            ".SA":  "^BVSP",       # B3 巴西（Ibovespa）
            "":     "SPY",         # 美国上市股票的默认值（无后缀）
        },

    })


# 构建默认配置并应用环境变量覆盖
# 在模块导入时执行，DEFAULT_CONFIG 作为全局配置使用
DEFAULT_CONFIG = build_default_config()
