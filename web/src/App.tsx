// Ant Design 图标用于区分模型、工具、Agent、阶段和运行状态。
import {
  ApiOutlined,
  BarChartOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  CloseCircleFilled,
  CodeOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  LoadingOutlined,
  PauseCircleOutlined,
  PlayCircleFilled,
  RobotOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
  ToolOutlined,
} from '@ant-design/icons'
// Ant Design 组件构成表单、指标卡、时间线和报告标签页。
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  DatePicker,
  Divider,
  Empty,
  Form,
  Input,
  Progress,
  Radio,
  Row,
  Segmented,
  Select,
  Slider,
  Space,
  Statistic,
  Tabs,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from 'antd'
// dayjs 同时处理表单日期值和事件时间格式化。
import dayjs, { Dayjs } from 'dayjs'
// React Hooks 管理任务状态、SSE 引用、派生数据和副作用。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
// Agent 生成的 Markdown 报告由受控渲染器输出。
import ReactMarkdown from 'react-markdown'
// 所有 SSE 消息先进入共享事件总线，再由页面按主题订阅。
import { tradingEvents, type TradingEvent } from './events'

// 解构常用排版组件，减少 JSX 中的命名长度。
const { Title, Text, Paragraph } = Typography
// 生产环境使用同源 API；前后端分离开发时可通过 Vite 环境变量覆盖。
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// RunStatus 覆盖任务从未开始到三个终态的完整状态机。
type RunStatus = 'idle' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
// AgentStatus 用于流水线节点和阶段进度展示。
type AgentStatus = 'pending' | 'running' | 'completed' | 'failed'
// TraceFilter 控制事件面板显示全部、Agent、模型或工具事件。
type TraceFilter = 'all' | 'agent' | 'llm' | 'tool'

// 页面时间线直接使用统一 TradingEvent 协议。
type TraceEvent = TradingEvent

// RunInfo 对应 GET /api/runs/{id} 的任务恢复响应。
interface RunInfo {
  id: string
  status: RunStatus
  error?: string
  final_result?: RunResult
}

// RunResult 是任务完成事件和最终快照共享的结果结构。
interface RunResult {
  ticker: string
  trade_date: string
  rating: string
  final_trade_decision: string
  reports: Record<string, string>
  elapsed_ms: number
  stats: { llm_calls: number; tool_calls: number; completed_nodes: number }
}

// Meta 对应 /api/meta，用于初始化版本信息和模型选择器。
interface Meta {
  version: string
  providers: ProviderOption[]
  default_provider: string
  default_quick_model: string
  default_deep_model: string
}

// ModelOption 可直接作为 Ant Design Select 的 option。
interface ModelOption {
  label: string
  value: string
}

// ProviderOption 同时描述凭证状态及快、深两组模型。
interface ProviderOption {
  value: string
  label: string
  configured: boolean
  credential_env: string | null
  quick_models: ModelOption[]
  deep_models: ModelOption[]
}

// RunForm 定义浏览器表单值；提交前会把 Dayjs 转成日期字符串。
interface RunForm {
  ticker: string
  trade_date: Dayjs
  asset_type: 'stock' | 'crypto'
  analysts: string[]
  research_depth: number
  output_language: 'Chinese' | 'English'
  provider: string
  quick_model: string
  deep_model: string
}

// 分析师选项的 value 必须与后端 ANALYSTS 和图节点映射一致。
const analystOptions = [
  { label: '技术面', value: 'market' },
  { label: '市场情绪', value: 'social' },
  { label: '新闻宏观', value: 'news' },
  { label: '基本面', value: 'fundamentals' },
]

// phases 把底层节点组合成用户能理解的五个决策阶段。
const phases = [
  {
    title: '信息采集',
    subtitle: 'Analyst team',
    icon: <DatabaseOutlined />,
    nodes: ['Market Analyst', 'Sentiment Analyst', 'News Analyst', 'Fundamentals Analyst', 'Memory Log'],
  },
  {
    title: '多空研究',
    subtitle: 'Research debate',
    icon: <ExperimentOutlined />,
    nodes: ['Bull Researcher', 'Bear Researcher', 'Research Manager'],
  },
  {
    title: '交易计划',
    subtitle: 'Trader',
    icon: <BarChartOutlined />,
    nodes: ['Trader'],
  },
  {
    title: '风险辩论',
    subtitle: 'Risk team',
    icon: <SafetyCertificateOutlined />,
    nodes: ['Aggressive Analyst', 'Neutral Analyst', 'Conservative Analyst'],
  },
  {
    title: '最终审批',
    subtitle: 'Portfolio manager',
    icon: <CheckCircleFilled />,
    nodes: ['Portfolio Manager'],
  },
]

// reportOrder 固定报告标签页顺序，避免按事件到达顺序跳动。
const reportOrder = [
  ['market_report', '技术面'],
  ['sentiment_report', '情绪'],
  ['news_report', '新闻'],
  ['fundamentals_report', '基本面'],
  ['investment_plan', '多空研究'],
  ['trader_investment_plan', '交易计划'],
  ['final_trade_decision', '最终决策'],
] as const

function eventCategory(type: string): TraceFilter {
  // 模型和工具有专属筛选，其余运行、节点、消息、报告归为 Agent 视角。
  if (type.startsWith('llm.')) return 'llm'
  if (type.startsWith('tool.')) return 'tool'
  return 'agent'
}

function displayNode(node: unknown): string {
  // 缺失节点时显示 System，内部 snake_case 名称则替换为空格。
  if (!node) return 'System'
  return String(node).replaceAll('_', ' ')
}

function eventTitle(event: TraceEvent): string {
  // 节点名被多种动态标题复用。
  const node = displayNode(event.node)
  // type 到中文可读标题的映射保持时间线文案集中管理。
  const names: Record<string, string> = {
    'run.created': '任务已进入队列',
    'run.started': '分析正式开始',
    'run.completed': '分析完成',
    'run.failed': '分析失败',
    'run.cancelled': '分析已取消',
    'run.cancellation_requested': '正在请求取消',
    'node.started': `${node} 开始工作`,
    'node.completed': `${node} 完成`,
    'node.failed': `${node} 失败`,
    'llm.started': `${node} 调用模型`,
    'llm.completed': `${node} 返回结果`,
    'llm.failed': `${node} 模型调用失败`,
    'tool.started': `调用工具 ${String(event.tool ?? '')}`,
    'tool.completed': `${node} 工具返回`,
    'tool.failed': `${node} 工具失败`,
    'message.created': `${displayNode(event.name ?? event.role)} 消息`,
    'report.updated': `${String(event.title ?? '报告')}已更新`,
    'state.updated': '工作流状态更新',
    'checkpoint.ready': event.resumed ? '已恢复检查点' : '检查点已启用',
  }
  // 未知扩展事件直接展示 type，保证新事件仍可观察。
  return names[event.type] ?? event.type
}

function eventSummary(event: TraceEvent): string {
  // 按可读优先级选取事件最重要的内容字段。
  const value =
    event.content ?? event.output ?? event.error ?? event.input ??
    (Array.isArray(event.keys) ? `更新字段：${event.keys.join('、')}` : '')
  // 没有摘要内容时避免渲染空段落。
  if (!value) return ''
  // 对象和数组使用缩进 JSON，字符串保持原样。
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

function eventIcon(event: TraceEvent) {
  // 图标和颜色快速表达失败、完成、工具、模型和 Agent 类别。
  if (event.type.includes('failed')) return <CloseCircleFilled className="event-icon error" />
  if (event.type === 'run.completed') return <CheckCircleFilled className="event-icon success" />
  if (event.type.startsWith('tool.')) return <ToolOutlined className="event-icon tool" />
  if (event.type.startsWith('llm.')) return <ApiOutlined className="event-icon llm" />
  if (event.type.startsWith('node.')) return <RobotOutlined className="event-icon agent" />
  return <CodeOutlined className="event-icon muted" />
}

function statusBadge(status: RunStatus) {
  // 英文内部状态映射成中文标签。
  const labels: Record<RunStatus, string> = {
    idle: '待命', queued: '排队中', running: '分析中', completed: '已完成', failed: '失败', cancelled: '已取消',
  }
  // Ant Design Tag 颜色与状态语义保持一致。
  const colors: Record<RunStatus, string> = {
    idle: 'default', queued: 'processing', running: 'cyan', completed: 'success', failed: 'error', cancelled: 'warning',
  }
  return <Tag color={colors[status]}>{status === 'running' && <LoadingOutlined />} {labels[status]}</Tag>
}

function phaseState(nodes: string[], statuses: Record<string, AgentStatus>): AgentStatus {
  // 只统计本次运行实际注册过的节点。
  const values = nodes.map((node) => statuses[node]).filter(Boolean)
  // 失败优先级最高，其次运行中；全部完成后阶段才完成。
  if (values.some((value) => value === 'failed')) return 'failed'
  if (values.some((value) => value === 'running')) return 'running'
  if (values.length > 0 && values.every((value) => value === 'completed')) return 'completed'
  return 'pending'
}

function App() {
  // Ant Design Form 实例用于读取监听值和编程式更新 provider/model。
  const [form] = Form.useForm<RunForm>()
  // useWatch 只在对应字段变化时触发需要这些值的页面更新。
  const watchedTicker = Form.useWatch('ticker', form)
  const watchedProvider = Form.useWatch('provider', form)
  const watchedQuickModel = Form.useWatch('quick_model', form)
  const watchedDeepModel = Form.useWatch('deep_model', form)
  // meta 保存服务端能力；runId/status 描述当前分析任务。
  const [meta, setMeta] = useState<Meta | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [status, setStatus] = useState<RunStatus>('idle')
  // connection 独立描述 SSE 传输状态，不与任务业务状态混淆。
  const [connection, setConnection] = useState<'offline' | 'live' | 'reconnecting'>('offline')
  // events 是最多五百条的时间线历史；reports 保存逐步生成的 Markdown。
  const [events, setEvents] = useState<TraceEvent[]>([])
  const [reports, setReports] = useState<Record<string, string>>({})
  const [result, setResult] = useState<RunResult | null>(null)
  // agentStatuses 驱动五阶段流水线；traceFilter 驱动事件筛选。
  const [agentStatuses, setAgentStatuses] = useState<Record<string, AgentStatus>>({})
  const [traceFilter, setTraceFilter] = useState<TraceFilter>('all')
  // elapsed/startedAt 分别保存显示耗时和客户端计时起点。
  const [elapsed, setElapsed] = useState(0)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  // ref 保存跨渲染稳定且无需触发 UI 更新的连接与去重状态。
  const eventSource = useRef<EventSource | null>(null)
  const seenEvents = useRef(new Set<number>())
  const statusRef = useRef(status)
  // 共享总线让 App 和后续子模块订阅同一批统一事件。
  const eventBus = tradingEvents

  // EventSource.onerror 闭包通过 ref 读取最新任务状态。
  useEffect(() => { statusRef.current = status }, [status])

  // 首次加载时读取 provider/model 元数据，并填充服务端选出的安全默认值。
  useEffect(() => {
    fetch(`${API_BASE}/api/meta`)
      .then((response) => {
        // 非 2xx 响应统一进入错误提示分支。
        if (!response.ok) throw new Error('无法读取模型配置')
        return response.json() as Promise<Meta>
      })
      .then((data) => {
        // 先保存下拉框目录，再设置三个相关表单值。
        setMeta(data)
        form.setFieldsValue({
          provider: data.default_provider,
          quick_model: data.default_quick_model,
          deep_model: data.default_deep_model,
        })
      })
      .catch(() => message.error('无法读取服务端模型配置'))
  }, [form])

  // 任务排队或运行时每秒刷新本地耗时，进入终态后销毁计时器。
  useEffect(() => {
    if (!startedAt || !['queued', 'running'].includes(status)) return
    const timer = window.setInterval(() => setElapsed(Date.now() - startedAt), 1000)
    return () => window.clearInterval(timer)
  }, [startedAt, status])

  // 页面通过事件总线注册所有状态归约器，而不是在 SSE 回调中写条件分支。
  useEffect(() => {
    // 每个 subscribe 都返回取消函数，effect 清理时统一调用。
    const unsubscribers = [
      // 无模式订阅接收全部事件，并只保留最近五百条。
      eventBus.subscribe((event) => {
        setEvents((current) => [...current.slice(-499), event])
      }),
      // run.started 初始化所有参与 Agent 为 pending。
      eventBus.subscribe('run.started', (event) => {
        setStatus('running')
        const agents = (event.agents as string[]) ?? []
        setAgentStatuses(Object.fromEntries(agents.map((agent) => [agent, 'pending'])))
      }),
      // agent.* 生命周期主题由后端从 node.* 事件派生。
      eventBus.subscribe('agent.started', (event) => {
        const agent = event.agent?.name ?? String(event.node ?? '')
        if (agent) setAgentStatuses((current) => ({ ...current, [agent]: 'running' }))
      }),
      // 节点完成时更新对应圆点和阶段状态。
      eventBus.subscribe('agent.completed', (event) => {
        const agent = event.agent?.name ?? String(event.node ?? '')
        if (agent) setAgentStatuses((current) => ({ ...current, [agent]: 'completed' }))
      }),
      // 节点异常优先显示为失败状态。
      eventBus.subscribe('agent.failed', (event) => {
        const agent = event.agent?.name ?? String(event.node ?? '')
        if (agent) setAgentStatuses((current) => ({ ...current, [agent]: 'failed' }))
      }),
      // 报告事件同时更新报告正文并标记负责 Agent 已完成。
      eventBus.subscribe('report.updated', (event) => {
        setReports((current) => ({ ...current, [String(event.report_key)]: String(event.content ?? '') }))
        const agent = event.agent?.name ?? String(event.agent_name ?? '')
        if (agent) setAgentStatuses((current) => ({ ...current, [agent]: 'completed' }))
      }),
      // 完成事件提供完整结果，并主动关闭无需继续保持的 SSE。
      eventBus.subscribe('run.completed', (event) => {
        const completed = event.result as unknown as RunResult
        setStatus('completed')
        setResult(completed)
        setReports(completed.reports)
        setElapsed(completed.elapsed_ms)
        localStorage.removeItem('tradingagents.activeRun')
        eventSource.current?.close()
        setConnection('offline')
      }),
      // 失败事件清理恢复标记并向用户展示后端错误。
      eventBus.subscribe('run.failed', (event) => {
        setStatus('failed')
        localStorage.removeItem('tradingagents.activeRun')
        eventSource.current?.close()
        setConnection('offline')
        message.error(String(event.error ?? '分析失败'))
      }),
      // 取消事件与失败一样进入终态，但不弹出错误。
      eventBus.subscribe('run.cancelled', () => {
        setStatus('cancelled')
        localStorage.removeItem('tradingagents.activeRun')
        eventSource.current?.close()
        setConnection('offline')
      }),
    ]
    // React 卸载或依赖变化时移除全部处理器，避免重复订阅。
    return () => unsubscribers.forEach((unsubscribe) => unsubscribe())
  }, [eventBus])

  // consumeEvent 是 SSE 到统一事件总线之间唯一入口。
  const consumeEvent = useCallback((event: TraceEvent) => {
    // SSE 重连会重放历史，通过递增 id 防止重复归约状态。
    if (seenEvents.current.has(event.id)) return
    seenEvents.current.add(event.id)
    eventBus.dispatch(event)
  }, [eventBus])

  // connect 管理指定任务的 EventSource 生命周期和重连显示。
  const connect = useCallback((id: string) => {
    // 新连接建立前关闭旧连接，保证页面只有一个事件来源。
    eventSource.current?.close()
    setConnection('reconnecting')
    // EventSource 会自动处理 SSE 的 Last-Event-ID 和网络重连。
    const source = new EventSource(`${API_BASE}/api/runs/${id}/events`)
    eventSource.current = source
    source.onopen = () => setConnection('live')
    // 每条 data 都应是后端统一事件 JSON；心跳注释不会触发 onmessage。
    source.onmessage = (messageEvent) => {
      try { consumeEvent(JSON.parse(messageEvent.data) as TraceEvent) } catch { /* heartbeat or malformed proxy chunk */ }
    }
    // 终态后的关闭是正常情况，运行中断线则显示正在重连。
    source.onerror = () => {
      if (['completed', 'failed', 'cancelled'].includes(statusRef.current)) {
        source.close()
        setConnection('offline')
      } else {
        setConnection('reconnecting')
      }
    }
  }, [consumeEvent])

  // 页面刷新后根据 localStorage 中的活动任务 ID 恢复快照和 SSE。
  useEffect(() => {
    const saved = localStorage.getItem('tradingagents.activeRun')
    // 没有保存任务时保持初始 idle 状态。
    if (!saved) return
    fetch(`${API_BASE}/api/runs/${saved}`)
      .then((response) => {
        // 任务已经被内存历史淘汰时移除失效 ID。
        if (!response.ok) throw new Error('missing')
        return response.json() as Promise<RunInfo>
      })
      .then((run) => {
        setRunId(run.id)
        setStatus(run.status)
        setStartedAt(Date.now())
        // 快照可能已经包含最终结果，无需等待 SSE 重放才能先显示报告。
        if (run.final_result) {
          setResult(run.final_result)
          setReports(run.final_result.reports)
        }
        connect(run.id)
      })
      .catch(() => localStorage.removeItem('tradingagents.activeRun'))
    // 组件卸载时释放浏览器连接。
    return () => eventSource.current?.close()
  }, [connect])

  // startRun 校验通过后创建任务，并立即连接其事件流。
  const startRun = async (values: RunForm) => {
    // 每次新运行先清空上一轮的事件、报告、结果和 Agent 状态。
    setEvents([])
    seenEvents.current.clear()
    setReports({})
    setResult(null)
    setAgentStatuses({})
    setStatus('queued')
    setStartedAt(Date.now())
    setElapsed(0)
    // Dayjs 不能直接作为 API 契约，提交前格式化为 ISO 日期。
    const payload = { ...values, trade_date: values.trade_date.format('YYYY-MM-DD') }
    try {
      // FastAPI 使用 JSON 请求体接收创建参数。
      const response = await fetch(`${API_BASE}/api/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      // 错误响应也包含 JSON detail，因此先统一解析。
      const body = await response.json()
      if (!response.ok) {
        const activeId = body?.detail?.active_run_id
        // 409 表示已有活动任务；页面直接接管并连接该任务。
        if (response.status === 409 && activeId) {
          setRunId(activeId)
          localStorage.setItem('tradingagents.activeRun', activeId)
          connect(activeId)
          message.info('已连接到正在执行的分析')
          return
        }
        throw new Error(body?.detail?.message ?? body?.detail ?? '无法创建分析')
      }
      // 保存任务 ID 让刷新页面后仍可恢复。
      setRunId(body.id)
      localStorage.setItem('tradingagents.activeRun', body.id)
      connect(body.id)
    } catch (error) {
      setStatus('failed')
      message.error(error instanceof Error ? error.message : '无法连接服务端')
    }
  }

  // cancelRun 只发出协作式取消请求，不会强杀正在进行的网络调用。
  const cancelRun = async () => {
    if (!runId) return
    await fetch(`${API_BASE}/api/runs/${runId}/cancel`, { method: 'POST' })
    message.info('将在当前模型或工具调用结束后停止')
  }

  // 根据表单当前 provider 找到对应凭证状态和模型候选项。
  const selectedProvider = useMemo(
    () => meta?.providers.find((provider) => provider.value === watchedProvider),
    [meta, watchedProvider],
  )

  // 切换供应商时同步切换快、深模型，避免跨供应商模型组合。
  const changeProvider = (provider: string) => {
    const selected = meta?.providers.find((item) => item.value === provider)
    if (!selected) return
    form.setFieldsValue({
      quick_model: selected.quick_models[0]?.value,
      deep_model: selected.deep_models[0]?.value,
    })
  }

  // 从统一事件和 Agent 状态派生顶部四个实时指标。
  const stats = useMemo(() => ({
    llm: events.filter((event) => event.type === 'llm.started').length,
    tools: events.filter((event) => event.type === 'tool.started').length,
    nodes: Object.values(agentStatuses).filter((value) => value === 'completed').length,
  }), [events, agentStatuses])

  // 时间线倒序显示，并应用用户选择的事件类别筛选。
  const filteredEvents = useMemo(
    () => events.filter((event) => traceFilter === 'all' || eventCategory(event.type) === traceFilter).slice().reverse(),
    [events, traceFilter],
  )

  // 五阶段完成数驱动进度条；最终决策优先取正式结果，运行中取增量报告。
  const completedPhases = phases.filter((phase) => phaseState(phase.nodes, agentStatuses) === 'completed').length
  const finalDecision = result?.final_trade_decision ?? reports.final_trade_decision

  return (
    // app-shell 提供整页背景和最大宽度布局。
    <div className="app-shell">
      {/* 顶栏展示品牌、SSE 连接状态、当前模型和版本。 */}
      <header className="topbar">
        <div className="brand-block">
          <div className="brand-mark"><ThunderboltOutlined /></div>
          <div>
            <div className="brand-title">TRADING<span>AGENTS</span></div>
            <div className="brand-subtitle">LIVE RESEARCH DESK</div>
          </div>
        </div>
        <Space size="middle" className="runtime-meta">
          <span className={`connection-dot ${connection}`} />
          <Text type="secondary">{connection === 'live' ? '实时链路已连接' : connection === 'reconnecting' ? '正在重连' : '未连接'}</Text>
          {selectedProvider && <Tag bordered={false}>{selectedProvider.label} · {watchedQuickModel}</Tag>}
          {meta && <Text className="version">v{meta.version}</Text>}
        </Space>
      </header>

      {/* 主区域依次包含介绍、实时指标、最终决策和双栏工作区。 */}
      <main>
        {/* Hero 左侧解释产品，右侧浓缩当前任务进度。 */}
        <section className="hero">
          <div>
            <Text className="eyebrow">MULTI-AGENT MARKET INTELLIGENCE</Text>
            <Title level={1}>把每一次判断，<br /><span>放到明面上。</span></Title>
            <Paragraph>实时观察分析师、研究员、交易员和风险团队如何调用模型、使用工具、交换证据并形成最终决策。</Paragraph>
          </div>
          <div className="hero-status">
            <div className="hero-status-top">
              <Text type="secondary">CURRENT RUN</Text>
              {statusBadge(status)}
            </div>
            <div className="hero-symbol">{watchedTicker || 'NVDA'}</div>
            <Progress
              percent={Math.round((completedPhases / phases.length) * 100)}
              showInfo={false}
              strokeColor="#48d597"
              trailColor="#1b2c27"
            />
            <div className="hero-status-bottom">
              <span>{completedPhases}/{phases.length} 阶段完成</span>
              <span>{Math.round(elapsed / 1000)}s</span>
            </div>
          </div>
        </section>

        {/* 四张指标卡由当前事件历史实时派生。 */}
        <Row gutter={[16, 16]} className="metrics-row">
          <Col xs={12} lg={6}><Card className="metric-card"><Statistic title="模型调用" value={stats.llm} prefix={<ApiOutlined />} /></Card></Col>
          <Col xs={12} lg={6}><Card className="metric-card"><Statistic title="工具调用" value={stats.tools} prefix={<ToolOutlined />} /></Card></Col>
          <Col xs={12} lg={6}><Card className="metric-card"><Statistic title="完成节点" value={stats.nodes} prefix={<RobotOutlined />} /></Card></Col>
          <Col xs={12} lg={6}><Card className="metric-card"><Statistic title="执行时间" value={(elapsed / 1000).toFixed(0)} suffix="s" prefix={<ClockCircleOutlined />} /></Card></Col>
        </Row>

        {/* 只有产生最终决策后才展示高亮摘要条。 */}
        {finalDecision && (
          <Card className="decision-strip">
            <div className="decision-label"><CheckCircleFilled /> PORTFOLIO DECISION</div>
            <div className="decision-copy">{finalDecision.slice(0, 280)}{finalDecision.length > 280 ? '…' : ''}</div>
            {result?.rating && <Tag color="success" className="rating-tag">{result.rating}</Tag>}
          </Card>
        )}

        {/* 左侧配置任务，右侧观察 LangGraph 执行和报告。 */}
        <div className="workspace-grid">
          {/* 分析参数表单。 */}
          <aside>
            <Card title="分析配置" className="setup-card" extra={<CodeOutlined />}>
              <Form<RunForm>
                form={form}
                layout="vertical"
                onFinish={startRun}
                initialValues={{
                  ticker: 'NVDA', trade_date: dayjs(), asset_type: 'stock',
                  analysts: analystOptions.map((item) => item.value), research_depth: 1, output_language: 'Chinese',
                }}
              >
                <Form.Item name="ticker" label="标的代码" rules={[{ required: true }, { pattern: /^[A-Za-z0-9.=_^\-]+$/, message: '请输入有效代码' }]}>
                  <Input size="large" placeholder="NVDA" prefix={<BarChartOutlined />} />
                </Form.Item>
                <Form.Item name="trade_date" label="分析日期" rules={[{ required: true }]}>
                  <DatePicker size="large" style={{ width: '100%' }} disabledDate={(current) => current?.isAfter(dayjs(), 'day')} />
                </Form.Item>
                <Form.Item name="asset_type" label="资产类型">
                  <Segmented block options={[{ label: '股票', value: 'stock' }, { label: '加密资产', value: 'crypto' }]} />
                </Form.Item>
                <Form.Item name="provider" label="模型供应商" rules={[{ required: true, message: '请选择模型供应商' }]}>
                  <Select
                    size="large"
                    placeholder="选择模型供应商"
                    onChange={changeProvider}
                    options={meta?.providers.map((provider) => ({
                      value: provider.value,
                      disabled: !provider.configured,
                      label: provider.configured
                        ? provider.label
                        : `${provider.label}（未配置 ${provider.credential_env}）`,
                    }))}
                  />
                </Form.Item>
                <Form.Item name="quick_model" label="快速模型" rules={[{ required: true, message: '请选择快速模型' }]}>
                  <Select size="large" showSearch optionFilterProp="label" options={selectedProvider?.quick_models} />
                </Form.Item>
                <Form.Item name="deep_model" label="深度模型" rules={[{ required: true, message: '请选择深度模型' }]}>
                  <Select size="large" showSearch optionFilterProp="label" options={selectedProvider?.deep_models} />
                </Form.Item>
                <Form.Item name="analysts" label="分析团队" rules={[{ required: true, message: '至少选择一名分析师' }]}>
                  <Checkbox.Group className="analyst-grid" options={analystOptions} />
                </Form.Item>
                <Form.Item name="research_depth" label="辩论深度">
                  <Slider min={1} max={5} marks={{ 1: '快速', 3: '深入', 5: '审慎' }} />
                </Form.Item>
                <Form.Item name="output_language" label="报告语言">
                  <Radio.Group optionType="button" buttonStyle="solid" options={[{ label: '中文', value: 'Chinese' }, { label: 'English', value: 'English' }]} />
                </Form.Item>
                <Divider />
                <Button
                  type="primary"
                  size="large"
                  block
                  htmlType="submit"
                  icon={<PlayCircleFilled />}
                  disabled={!meta || !selectedProvider?.configured || status === 'queued' || status === 'running'}
                >
                  启动多智能体分析
                </Button>
                {(status === 'queued' || status === 'running') && (
                  <Button block danger type="text" icon={<PauseCircleOutlined />} onClick={cancelRun}>停止分析</Button>
                )}
              </Form>
              {/* model-note 让用户在提交前再次确认最终模型组合。 */}
              <div className="model-note">
                <div><span>当前供应商</span><strong>{selectedProvider?.label ?? '—'}</strong></div>
                <div><span>模型组合</span><strong>{watchedQuickModel ?? '—'} / {watchedDeepModel ?? '—'}</strong></div>
              </div>
            </Card>
          </aside>

          {/* 运行状态、事件时间线和研究报告。 */}
          <section className="execution-column">
            {/* 五个阶段根据其子节点状态自动聚合。 */}
            <Card className="pipeline-card" title="决策流水线" extra={<Text type="secondary">LANGGRAPH EXECUTION</Text>}>
              <div className="pipeline">
                {/* 每个阶段展示序号、状态图标、说明和参与 Agent 圆点。 */}
                {phases.map((phase, index) => {
                  const phaseStatus = phaseState(phase.nodes, agentStatuses)
                  return (
                    <div className={`phase ${phaseStatus}`} key={phase.title}>
                      <div className="phase-index">0{index + 1}</div>
                      <div className="phase-icon">{phaseStatus === 'running' ? <LoadingOutlined spin /> : phaseStatus === 'completed' ? <CheckCircleFilled /> : phase.icon}</div>
                      <div className="phase-copy"><strong>{phase.title}</strong><span>{phase.subtitle}</span></div>
                      <div className="phase-agents">
                        {phase.nodes.filter((node) => node in agentStatuses).map((node) => (
                          <Tooltip title={node} key={node}>
                            <i className={agentStatuses[node] ?? 'pending'} />
                          </Tooltip>
                        ))}
                      </div>
                    </div>
                  )
                })}
              </div>
            </Card>

            {/* 失败时在主工作区顶部固定显示最后错误。 */}
            {status === 'failed' && <Alert type="error" showIcon message="分析未完成" description={events.slice().reverse().find((event) => event.type === 'run.failed')?.error as string} />}

            {/* 活动卡在实时轨迹与逐步生成的 Markdown 报告间切换。 */}
            <Card className="activity-card">
              <Tabs
                defaultActiveKey="trace"
                items={[
                  {
                    key: 'trace',
                    label: `实时轨迹 ${events.length ? `(${events.length})` : ''}`,
                    children: (
                      <>
                        {/* 工具栏筛选统一事件总线中的不同事件族。 */}
                        <div className="trace-toolbar">
                          <Segmented<TraceFilter>
                            value={traceFilter}
                            onChange={setTraceFilter}
                            options={[{ label: '全部', value: 'all' }, { label: 'Agent', value: 'agent' }, { label: '模型', value: 'llm' }, { label: '工具', value: 'tool' }]}
                          />
                          <Text type="secondary">最新事件优先</Text>
                        </div>
                        {/* 时间线采用最新事件优先，长内容可展开。 */}
                        <div className="trace-scroll">
                          {filteredEvents.length ? (
                            <Timeline
                              items={filteredEvents.map((event) => ({
                                dot: eventIcon(event),
                                children: (
                                  <div className="trace-event">
                                    <div className="trace-heading">
                                      <strong>{eventTitle(event)}</strong>
                                      <time>{dayjs(event.timestamp).format('HH:mm:ss')}</time>
                                    </div>
                                    {eventSummary(event) && <Paragraph ellipsis={{ rows: 4, expandable: true, symbol: '展开' }} className="trace-summary">{eventSummary(event)}</Paragraph>}
                                    <Tag bordered={false}>{event.type}</Tag>
                                  </div>
                                ),
                              }))}
                            />
                          ) : (
                            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="启动分析后，LangChain 中间过程会实时出现在这里" />
                          )}
                        </div>
                      </>
                    ),
                  },
                  {
                    key: 'reports',
                    label: `研究报告 ${Object.keys(reports).length ? `(${Object.keys(reports).length})` : ''}`,
                    // 仅为已经生成内容的报告创建标签页。
                    children: Object.keys(reports).length ? (
                      <Tabs
                        className="report-tabs"
                        tabPosition="left"
                        items={reportOrder.filter(([key]) => reports[key]).map(([key, label]) => ({
                          key,
                          label,
                          children: <article className="markdown-report"><ReactMarkdown>{reports[key]}</ReactMarkdown></article>,
                        }))}
                      />
                    ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Agent 完成阶段任务后，报告会在这里持续更新" />,
                  },
                ]}
              />
            </Card>
          </section>
        </div>
      </main>

      {/* 页脚明确产品定位与投资风险边界。 */}
      <footer>
        <span>TradingAgents · Observable multi-agent research</span>
        <span>仅供研究，不构成投资建议</span>
      </footer>
    </div>
  )
}

// 默认导出供 main.tsx 挂载。
export default App
