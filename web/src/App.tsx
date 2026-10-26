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
import dayjs, { Dayjs } from 'dayjs'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'

const { Title, Text, Paragraph } = Typography
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

type RunStatus = 'idle' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
type AgentStatus = 'pending' | 'running' | 'completed' | 'failed'
type TraceFilter = 'all' | 'agent' | 'llm' | 'tool'

interface TraceEvent {
  id: number
  type: string
  timestamp: string
  [key: string]: unknown
}

interface RunInfo {
  id: string
  status: RunStatus
  error?: string
  final_result?: RunResult
}

interface RunResult {
  ticker: string
  trade_date: string
  rating: string
  final_trade_decision: string
  reports: Record<string, string>
  elapsed_ms: number
  stats: { llm_calls: number; tool_calls: number; completed_nodes: number }
}

interface Meta {
  version: string
  provider: string
  quick_model: string
  deep_model: string
}

interface RunForm {
  ticker: string
  trade_date: Dayjs
  asset_type: 'stock' | 'crypto'
  analysts: string[]
  research_depth: number
  output_language: 'Chinese' | 'English'
}

const analystOptions = [
  { label: '技术面', value: 'market' },
  { label: '市场情绪', value: 'social' },
  { label: '新闻宏观', value: 'news' },
  { label: '基本面', value: 'fundamentals' },
]

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
  if (type.startsWith('llm.')) return 'llm'
  if (type.startsWith('tool.')) return 'tool'
  return 'agent'
}

function displayNode(node: unknown): string {
  if (!node) return 'System'
  return String(node).replaceAll('_', ' ')
}

function eventTitle(event: TraceEvent): string {
  const node = displayNode(event.node)
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
  return names[event.type] ?? event.type
}

function eventSummary(event: TraceEvent): string {
  const value =
    event.content ?? event.output ?? event.error ?? event.input ??
    (Array.isArray(event.keys) ? `更新字段：${event.keys.join('、')}` : '')
  if (!value) return ''
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

function eventIcon(event: TraceEvent) {
  if (event.type.includes('failed')) return <CloseCircleFilled className="event-icon error" />
  if (event.type === 'run.completed') return <CheckCircleFilled className="event-icon success" />
  if (event.type.startsWith('tool.')) return <ToolOutlined className="event-icon tool" />
  if (event.type.startsWith('llm.')) return <ApiOutlined className="event-icon llm" />
  if (event.type.startsWith('node.')) return <RobotOutlined className="event-icon agent" />
  return <CodeOutlined className="event-icon muted" />
}

function statusBadge(status: RunStatus) {
  const labels: Record<RunStatus, string> = {
    idle: '待命', queued: '排队中', running: '分析中', completed: '已完成', failed: '失败', cancelled: '已取消',
  }
  const colors: Record<RunStatus, string> = {
    idle: 'default', queued: 'processing', running: 'cyan', completed: 'success', failed: 'error', cancelled: 'warning',
  }
  return <Tag color={colors[status]}>{status === 'running' && <LoadingOutlined />} {labels[status]}</Tag>
}

function phaseState(nodes: string[], statuses: Record<string, AgentStatus>): AgentStatus {
  const values = nodes.map((node) => statuses[node]).filter(Boolean)
  if (values.some((value) => value === 'failed')) return 'failed'
  if (values.some((value) => value === 'running')) return 'running'
  if (values.length > 0 && values.every((value) => value === 'completed')) return 'completed'
  return 'pending'
}

function App() {
  const [form] = Form.useForm<RunForm>()
  const watchedTicker = Form.useWatch('ticker', form)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [status, setStatus] = useState<RunStatus>('idle')
  const [connection, setConnection] = useState<'offline' | 'live' | 'reconnecting'>('offline')
  const [events, setEvents] = useState<TraceEvent[]>([])
  const [reports, setReports] = useState<Record<string, string>>({})
  const [result, setResult] = useState<RunResult | null>(null)
  const [agentStatuses, setAgentStatuses] = useState<Record<string, AgentStatus>>({})
  const [traceFilter, setTraceFilter] = useState<TraceFilter>('all')
  const [elapsed, setElapsed] = useState(0)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const eventSource = useRef<EventSource | null>(null)
  const seenEvents = useRef(new Set<number>())
  const statusRef = useRef(status)

  useEffect(() => { statusRef.current = status }, [status])

  useEffect(() => {
    fetch(`${API_BASE}/api/meta`).then((response) => response.json()).then(setMeta).catch(() => undefined)
  }, [])

  useEffect(() => {
    if (!startedAt || !['queued', 'running'].includes(status)) return
    const timer = window.setInterval(() => setElapsed(Date.now() - startedAt), 1000)
    return () => window.clearInterval(timer)
  }, [startedAt, status])

  const consumeEvent = useCallback((event: TraceEvent) => {
    if (seenEvents.current.has(event.id)) return
    seenEvents.current.add(event.id)
    setEvents((current) => [...current.slice(-499), event])

    if (event.type === 'run.started') {
      setStatus('running')
      const agents = (event.agents as string[]) ?? []
      setAgentStatuses(Object.fromEntries(agents.map((agent) => [agent, 'pending'])))
    }
    if (event.type === 'node.started' && event.node) {
      setAgentStatuses((current) => ({ ...current, [String(event.node)]: 'running' }))
    }
    if (event.type === 'node.completed' && event.node) {
      setAgentStatuses((current) => ({ ...current, [String(event.node)]: 'completed' }))
    }
    if (event.type === 'node.failed' && event.node) {
      setAgentStatuses((current) => ({ ...current, [String(event.node)]: 'failed' }))
    }
    if (event.type === 'report.updated') {
      setReports((current) => ({ ...current, [String(event.report_key)]: String(event.content ?? '') }))
      if (event.agent) {
        setAgentStatuses((current) => ({ ...current, [String(event.agent)]: 'completed' }))
      }
    }
    if (event.type === 'run.completed') {
      const completed = event.result as unknown as RunResult
      setStatus('completed')
      setResult(completed)
      setReports(completed.reports)
      setElapsed(completed.elapsed_ms)
      localStorage.removeItem('tradingagents.activeRun')
      eventSource.current?.close()
      setConnection('offline')
    }
    if (event.type === 'run.failed') {
      setStatus('failed')
      localStorage.removeItem('tradingagents.activeRun')
      eventSource.current?.close()
      setConnection('offline')
      message.error(String(event.error ?? '分析失败'))
    }
    if (event.type === 'run.cancelled') {
      setStatus('cancelled')
      localStorage.removeItem('tradingagents.activeRun')
      eventSource.current?.close()
      setConnection('offline')
    }
  }, [])

  const connect = useCallback((id: string) => {
    eventSource.current?.close()
    setConnection('reconnecting')
    const source = new EventSource(`${API_BASE}/api/runs/${id}/events`)
    eventSource.current = source
    source.onopen = () => setConnection('live')
    source.onmessage = (messageEvent) => {
      try { consumeEvent(JSON.parse(messageEvent.data) as TraceEvent) } catch { /* heartbeat or malformed proxy chunk */ }
    }
    source.onerror = () => {
      if (['completed', 'failed', 'cancelled'].includes(statusRef.current)) {
        source.close()
        setConnection('offline')
      } else {
        setConnection('reconnecting')
      }
    }
  }, [consumeEvent])

  useEffect(() => {
    const saved = localStorage.getItem('tradingagents.activeRun')
    if (!saved) return
    fetch(`${API_BASE}/api/runs/${saved}`)
      .then((response) => {
        if (!response.ok) throw new Error('missing')
        return response.json() as Promise<RunInfo>
      })
      .then((run) => {
        setRunId(run.id)
        setStatus(run.status)
        setStartedAt(Date.now())
        if (run.final_result) {
          setResult(run.final_result)
          setReports(run.final_result.reports)
        }
        connect(run.id)
      })
      .catch(() => localStorage.removeItem('tradingagents.activeRun'))
    return () => eventSource.current?.close()
  }, [connect])

  const startRun = async (values: RunForm) => {
    setEvents([])
    seenEvents.current.clear()
    setReports({})
    setResult(null)
    setAgentStatuses({})
    setStatus('queued')
    setStartedAt(Date.now())
    setElapsed(0)
    const payload = { ...values, trade_date: values.trade_date.format('YYYY-MM-DD') }
    try {
      const response = await fetch(`${API_BASE}/api/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const body = await response.json()
      if (!response.ok) {
        const activeId = body?.detail?.active_run_id
        if (response.status === 409 && activeId) {
          setRunId(activeId)
          localStorage.setItem('tradingagents.activeRun', activeId)
          connect(activeId)
          message.info('已连接到正在执行的分析')
          return
        }
        throw new Error(body?.detail?.message ?? body?.detail ?? '无法创建分析')
      }
      setRunId(body.id)
      localStorage.setItem('tradingagents.activeRun', body.id)
      connect(body.id)
    } catch (error) {
      setStatus('failed')
      message.error(error instanceof Error ? error.message : '无法连接服务端')
    }
  }

  const cancelRun = async () => {
    if (!runId) return
    await fetch(`${API_BASE}/api/runs/${runId}/cancel`, { method: 'POST' })
    message.info('将在当前模型或工具调用结束后停止')
  }

  const stats = useMemo(() => ({
    llm: events.filter((event) => event.type === 'llm.started').length,
    tools: events.filter((event) => event.type === 'tool.started').length,
    nodes: Object.values(agentStatuses).filter((value) => value === 'completed').length,
  }), [events, agentStatuses])

  const filteredEvents = useMemo(
    () => events.filter((event) => traceFilter === 'all' || eventCategory(event.type) === traceFilter).slice().reverse(),
    [events, traceFilter],
  )

  const completedPhases = phases.filter((phase) => phaseState(phase.nodes, agentStatuses) === 'completed').length
  const finalDecision = result?.final_trade_decision ?? reports.final_trade_decision

  return (
    <div className="app-shell">
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
          {meta && <Tag bordered={false}>{meta.provider} · {meta.quick_model}</Tag>}
          {meta && <Text className="version">v{meta.version}</Text>}
        </Space>
      </header>

      <main>
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

        <Row gutter={[16, 16]} className="metrics-row">
          <Col xs={12} lg={6}><Card className="metric-card"><Statistic title="模型调用" value={stats.llm} prefix={<ApiOutlined />} /></Card></Col>
          <Col xs={12} lg={6}><Card className="metric-card"><Statistic title="工具调用" value={stats.tools} prefix={<ToolOutlined />} /></Card></Col>
          <Col xs={12} lg={6}><Card className="metric-card"><Statistic title="完成节点" value={stats.nodes} prefix={<RobotOutlined />} /></Card></Col>
          <Col xs={12} lg={6}><Card className="metric-card"><Statistic title="执行时间" value={(elapsed / 1000).toFixed(0)} suffix="s" prefix={<ClockCircleOutlined />} /></Card></Col>
        </Row>

        {finalDecision && (
          <Card className="decision-strip">
            <div className="decision-label"><CheckCircleFilled /> PORTFOLIO DECISION</div>
            <div className="decision-copy">{finalDecision.slice(0, 280)}{finalDecision.length > 280 ? '…' : ''}</div>
            {result?.rating && <Tag color="success" className="rating-tag">{result.rating}</Tag>}
          </Card>
        )}

        <div className="workspace-grid">
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
                  disabled={status === 'queued' || status === 'running'}
                >
                  启动多智能体分析
                </Button>
                {(status === 'queued' || status === 'running') && (
                  <Button block danger type="text" icon={<PauseCircleOutlined />} onClick={cancelRun}>停止分析</Button>
                )}
              </Form>
              <div className="model-note">
                <div><span>快速模型</span><strong>{meta?.quick_model ?? '—'}</strong></div>
                <div><span>深度模型</span><strong>{meta?.deep_model ?? '—'}</strong></div>
              </div>
            </Card>
          </aside>

          <section className="execution-column">
            <Card className="pipeline-card" title="决策流水线" extra={<Text type="secondary">LANGGRAPH EXECUTION</Text>}>
              <div className="pipeline">
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

            {status === 'failed' && <Alert type="error" showIcon message="分析未完成" description={events.slice().reverse().find((event) => event.type === 'run.failed')?.error as string} />}

            <Card className="activity-card">
              <Tabs
                defaultActiveKey="trace"
                items={[
                  {
                    key: 'trace',
                    label: `实时轨迹 ${events.length ? `(${events.length})` : ''}`,
                    children: (
                      <>
                        <div className="trace-toolbar">
                          <Segmented<TraceFilter>
                            value={traceFilter}
                            onChange={setTraceFilter}
                            options={[{ label: '全部', value: 'all' }, { label: 'Agent', value: 'agent' }, { label: '模型', value: 'llm' }, { label: '工具', value: 'tool' }]}
                          />
                          <Text type="secondary">最新事件优先</Text>
                        </div>
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

      <footer>
        <span>TradingAgents · Observable multi-agent research</span>
        <span>仅供研究，不构成投资建议</span>
      </footer>
    </div>
  )
}

export default App
