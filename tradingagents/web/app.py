"""TradingAgents Web 服务入口：负责参数校验、任务管理、SSE 与静态页面托管。"""

from __future__ import annotations

import os
import threading
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tradingagents import __version__
from tradingagents.default_config import build_default_config
from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS

from .events import RunRegistry, TERMINAL_STATUSES
from .runner import execute_run


# 从当前模块向上两级定位仓库根目录，避免依赖进程启动目录。
ROOT_DIR = Path(__file__).resolve().parents[2]
# Vite 生产构建目录由 FastAPI 直接托管。
WEB_DIST = ROOT_DIR / "web" / "dist"
# Web 表单允许启用的四类分析师，与图构造器使用的 key 保持一致。
ANALYSTS = ("market", "social", "news", "fundamentals")
# 将内部 provider key 转成适合页面展示的名称。
PROVIDER_LABELS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google Gemini",
    "xai": "xAI",
    "deepseek": "DeepSeek",
    "qwen": "Qwen（国际）",
    "qwen-cn": "Qwen（中国）",
    "glm": "GLM（国际）",
    "glm-cn": "GLM（中国）",
    "kimi": "Kimi",
    "minimax": "MiniMax（国际）",
    "minimax-cn": "MiniMax（中国）",
    "mistral": "Mistral",
    "ollama": "Ollama",
}

# 创建整个 Web 服务共享的 FastAPI 应用实例。
app = FastAPI(
    title="TradingAgents Live Research API",
    version=__version__,
    description="Streams observable LangChain and LangGraph activity to the TradingAgents web UI.",
)
# 任务注册表保存内存中的运行记录，并限制同一时间只能运行一个分析任务。
registry = RunRegistry()

# 从环境变量读取允许跨域访问 API 的前端地址；默认覆盖本地 Vite 地址。
origins = os.getenv(
    "TRADINGAGENTS_WEB_ORIGINS",
    "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4173,http://localhost:4173",
).split(",")
# 注册 CORS 中间件，使前后端分端口开发时也能请求 API。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in origins if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class CreateRunRequest(BaseModel):
    """创建分析任务时由浏览器提交的、经过严格校验的参数。"""

    # 禁止浏览器提交未声明字段，避免拼写错误被静默忽略。
    model_config = ConfigDict(extra="forbid")

    # 股票或数字资产代码只允许常见行情符号字符。
    ticker: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9.=_^\-]+$")
    # 分析日期最终会序列化成 YYYY-MM-DD。
    trade_date: date
    # 资产类型决定数据工具采用股票还是加密资产路径。
    asset_type: Literal["stock", "crypto"] = "stock"
    # 至少启用一名分析师，默认启用完整团队。
    analysts: list[Literal["market", "social", "news", "fundamentals"]] = Field(
        default_factory=lambda: list(ANALYSTS), min_length=1
    )
    # 研究深度同时控制多空辩论和风险辩论轮数。
    research_depth: int = Field(default=1, ge=1, le=5)
    # 输出语言直接传给 TradingAgents 图配置。
    output_language: Literal["English", "Chinese"] = "Chinese"
    # provider 与两个模型允许省略，服务端会根据已配置密钥补全默认值。
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    quick_model: str | None = Field(default=None, min_length=1, max_length=200)
    deep_model: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: object) -> object:
        """去掉代码首尾空格并统一大写，保证缓存 key 和行情查询一致。"""
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("trade_date")
    @classmethod
    def reject_future_date(cls, value: date) -> date:
        """行情数据尚未产生时拒绝未来日期。"""
        if value > date.today():
            raise ValueError("trade_date cannot be in the future")
        return value

    @field_validator("analysts")
    @classmethod
    def unique_analysts(cls, value: list[str]) -> list[str]:
        """按原顺序去重，避免同一个分析节点被重复装配。"""
        return list(dict.fromkeys(value))


def _model_options(provider: str, mode: str) -> list[dict[str, str]]:
    """把共享模型目录转换成 Select 组件格式，并排除 CLI 专用 custom 哨兵。"""
    return [
        {"label": label, "value": value}
        for label, value in MODEL_OPTIONS[provider][mode]
        if value != "custom"
    ]


def _runtime_meta() -> dict:
    """生成不含密钥值的运行时元数据，供前端构造 provider/model 下拉框。"""
    # 每次请求重新构建配置，确保进程环境变量的覆盖规则得到应用。
    config = build_default_config()
    # providers 最终只包含 Web 下拉框可直接选择具体模型的供应商。
    providers = []
    for provider in MODEL_OPTIONS:
        # 分别取得快速任务和深度推理任务的模型候选项。
        quick_models = _model_options(provider, "quick")
        deep_models = _model_options(provider, "deep")
        # 只有 custom 模型的 provider 暂不适合固定下拉框，因此跳过。
        if not quick_models or not deep_models:
            continue
        # 这里只读取环境变量名称和是否存在，绝不返回密钥内容。
        credential_env = get_api_key_env(provider)
        providers.append(
            {
                "value": provider,
                "label": PROVIDER_LABELS.get(provider, provider),
                "configured": credential_env is None or bool(os.getenv(credential_env)),
                "credential_env": credential_env,
                "quick_models": quick_models,
                "deep_models": deep_models,
            }
        )

    # 优先选中真正可用的供应商，避免默认 OpenAI 但只配置 DeepSeek 的情况。
    configured = [item for item in providers if item["configured"]]
    configured_values = {item["value"] for item in configured}
    configured_default = str(config.get("llm_provider") or "").lower()
    if configured_default not in configured_values:
        configured_default = configured[0]["value"] if configured else providers[0]["value"]

    # 找到默认供应商对应的模型集合，用于修正跨供应商的不兼容默认模型。
    selected = next(item for item in providers if item["value"] == configured_default)
    quick_values = {item["value"] for item in selected["quick_models"]}
    deep_values = {item["value"] for item in selected["deep_models"]}
    quick_model = str(config.get("quick_think_llm") or "")
    deep_model = str(config.get("deep_think_llm") or "")
    if str(config.get("llm_provider") or "").lower() != configured_default or quick_model not in quick_values:
        quick_model = selected["quick_models"][0]["value"]
    if str(config.get("llm_provider") or "").lower() != configured_default or deep_model not in deep_values:
        deep_model = selected["deep_models"][0]["value"]

    # 兼容字段 provider/quick_model/deep_model 保留给可能缓存的旧前端。
    return {
        "version": __version__,
        "analysts": list(ANALYSTS),
        "providers": providers,
        "default_provider": configured_default,
        "default_quick_model": quick_model,
        "default_deep_model": deep_model,
        # Keep the original keys for older frontend builds during rolling upgrades.
        "provider": configured_default,
        "quick_model": quick_model,
        "deep_model": deep_model,
        "output_language": config.get("output_language"),
    }


def _resolve_run_request(payload: CreateRunRequest) -> dict:
    """补全默认模型并在创建线程前完成 provider、凭证和模型组合校验。"""
    # 基于当前环境而不是模块导入时的快照解析默认值。
    runtime = _runtime_meta()
    providers = {item["value"]: item for item in runtime["providers"]}
    provider = (payload.provider or runtime["default_provider"]).lower()
    selected = providers.get(provider)
    # 拒绝客户端伪造目录之外的 provider。
    if selected is None:
        raise HTTPException(status_code=422, detail={"message": f"不支持的模型供应商：{provider}"})
    # 在 LangChain 真正调用模型前给出清晰的缺失密钥提示。
    if not selected["configured"]:
        env_var = selected["credential_env"]
        raise HTTPException(
            status_code=422,
            detail={
                "code": "provider_not_configured",
                "message": f"{selected['label']} 尚未配置，请在 .env 中设置 {env_var}",
                "provider": provider,
                "env_var": env_var,
            },
        )

    # 未显式选择模型时，采用目录中该供应商的首选项。
    quick_model = payload.quick_model or selected["quick_models"][0]["value"]
    deep_model = payload.deep_model or selected["deep_models"][0]["value"]
    quick_values = {item["value"] for item in selected["quick_models"]}
    deep_values = {item["value"] for item in selected["deep_models"]}
    # 禁止把 A 供应商的模型 ID 与 B 供应商组合提交。
    if quick_model not in quick_values or deep_model not in deep_values:
        raise HTTPException(
            status_code=422,
            detail={"message": f"模型与供应商 {selected['label']} 不匹配，请重新选择"},
        )

    # JSON 模式会把 date 等对象转换成后台执行器可直接消费的值。
    request = payload.model_dump(mode="json")
    request.update(provider=provider, quick_model=quick_model, deep_model=deep_model)
    return request


@app.get("/api/health")
def health() -> dict[str, str]:
    """供本地启动脚本或监控检查服务是否存活。"""
    return {"status": "ok", "version": __version__}


@app.get("/api/meta")
def meta() -> dict:
    """返回前端初始化表单需要的非敏感运行时信息。"""
    return _runtime_meta()


@app.get("/api/runs")
def list_runs() -> list[dict]:
    """按创建时间倒序返回内存中的任务快照。"""
    return registry.list()


@app.post("/api/runs", status_code=202)
def create_run(payload: CreateRunRequest) -> dict:
    """创建后台分析线程并立即以 HTTP 202 返回任务快照。"""
    # 先校验配置，防止无效任务占据唯一运行槽位。
    request = _resolve_run_request(payload)
    try:
        session = registry.create(request)
    except RuntimeError as exc:
        # 注册表用当前活动任务 ID 作为冲突异常内容，方便页面自动重连。
        raise HTTPException(
            status_code=409,
            detail={
                "message": "An analysis is already running",
                "active_run_id": str(exc),
            },
        ) from exc
    # 图执行可能耗时数分钟，因此放到守护线程，API 请求无需同步等待。
    worker = threading.Thread(
        target=execute_run,
        args=(session,),
        name=f"tradingagents-web-{session.id[:8]}",
        daemon=True,
    )
    worker.start()
    return session.snapshot()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    """读取单个任务状态，供页面刷新后恢复连接。"""
    session = registry.get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return session.snapshot()


@app.post("/api/runs/{run_id}/cancel", status_code=202)
def cancel_run(run_id: str) -> dict:
    """设置协作式取消标记；当前模型或工具调用结束后执行器会停止。"""
    session = registry.get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Run not found")
    # 已结束任务是幂等操作，直接返回原快照。
    if session.status in TERMINAL_STATUSES:
        return session.snapshot()
    session.cancel_requested = True
    session.publish("run.cancellation_requested", status=session.status)
    return session.snapshot()


@app.get("/api/runs/{run_id}/events")
def run_events(
    run_id: str,
    after: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """以 SSE 重放历史事件并持续推送新事件，直到任务进入终态。"""
    session = registry.get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Run not found")
    # 浏览器自动重连时会携带 Last-Event-ID，从下一条事件继续发送。
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))
    # 关闭代理缓冲，保证 LangChain 中间过程到达后立即展示。
    return StreamingResponse(
        session.iter_events(after=after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# 仅在前端已构建时挂载带 hash 的静态资源目录。
if (WEB_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="web-assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str, request: Request):
    """实现单页应用回退：真实文件直接返回，其余路由返回 index.html。"""
    # 未匹配的 API 路由不能回退到 HTML，否则调用方会误解析响应。
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    target = WEB_DIST / path
    # 路径安全检查确保目标仍在 dist 内，阻止目录穿越读取任意文件。
    if path and target.is_file() and WEB_DIST in target.resolve().parents:
        return FileResponse(target)
    index = WEB_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(
        status_code=404,
        detail="Web UI is not built. Run `cd web && npm install && npm run build`.",
    )


def main() -> None:
    """`uv run tradingagents-web` 命令对应的进程入口。"""
    # 延迟导入使单元测试导入 app 时无需启动 Uvicorn。
    import uvicorn

    # host/port 可由环境变量覆盖，默认只监听本机以降低暴露风险。
    uvicorn.run(
        "tradingagents.web.app:app",
        host=os.getenv("TRADINGAGENTS_WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("TRADINGAGENTS_WEB_PORT", "8000")),
        reload=False,
    )


# 同时支持 `python -m tradingagents.web.app` 的直接运行方式。
if __name__ == "__main__":
    main()
