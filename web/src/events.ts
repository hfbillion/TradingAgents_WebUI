// 所有事件都归一化成开始、更新、结束或错误四种阶段。
export type EventPhase = 'start' | 'update' | 'end' | 'error'

// AgentDescriptor 是后端根据 LangGraph 节点名生成的稳定身份信息。
export interface AgentDescriptor {
  // id 用于主题匹配，不随页面展示语言变化。
  id: string
  // name 是 UI 显示的原始节点名称。
  name: string
  // role 支持按分析师、研究员、风控等职责订阅。
  role: 'analyst' | 'researcher' | 'manager' | 'trader' | 'risk' | 'system' | 'agent'
  // side 区分多头、空头及风险偏好；不是所有 Agent 都有该字段。
  side?: 'bull' | 'bear' | 'aggressive' | 'neutral' | 'conservative' | string
}

// TradingEvent 对应 FastAPI SSE 推送的版本化统一事件信封。
export interface TradingEvent {
  // schema_version 允许未来升级协议并保留兼容分支。
  schema_version: number
  // id 是当前任务内严格递增的事件序号，也用于 SSE 断线续传。
  id: number
  // type 是主判别字段，例如 tool.started 或 run.completed。
  type: string
  // family 是 type 点号前的事件族，例如 tool、run、node。
  family: string
  // phase 是归一化生命周期阶段。
  phase: EventPhase
  // timestamp 是后端生成的 UTC ISO 时间。
  timestamp: string
  // session_id 关联产生该事件的分析任务。
  session_id: string
  // correlation_id 关联同一次节点、模型或工具调用的开始与结束。
  correlation_id?: string
  // topics 提供按 Agent 角色、多空方向等维度订阅的具体主题。
  topics: string[]
  // 只有能够归属到图节点的事件才包含 agent。
  agent?: AgentDescriptor
  // 索引签名保留各事件类型独有的业务载荷。
  [key: string]: unknown
}

// 事件处理器同步接收一个标准化事件。
export type TradingEventHandler<T extends TradingEvent = TradingEvent> = (event: T) => void
// 每次订阅都返回无参数取消函数，便于 React effect 清理。
export type Unsubscribe = () => void

// 转义正则保留字符，确保订阅模式中只有 * 具有通配含义。
function escapeRegExp(value: string): string {
  return value.replace(/[|\\{}()[\]^$+?.]/g, '\\$&')
}

// 判断事件的任一具体主题是否匹配用户提供的 glob 模式。
export function matchesEvent(event: TradingEvent, pattern: string): boolean {
  // 先按 * 分段转义，再用 .* 拼成完整匹配正则。
  const matcher = new RegExp(`^${pattern.split('*').map(escapeRegExp).join('.*')}$`)
  // 兼容没有 topics 的旧事件，回退到 type。
  return (event.topics?.length ? event.topics : [event.type]).some((topic) => matcher.test(topic))
}

// 同步事件总线参考 pi-agent 的 subscribe 模式：注册后返回取消订阅函数。
export class TradingEventBus {
  // Map 保留订阅顺序，并允许通过数字 ID 快速移除。
  private subscriptions = new Map<number, { pattern: string; handler: TradingEventHandler }>()
  // 自增 ID 在当前页面生命周期内唯一即可。
  private nextId = 1

  // 单参数重载表示订阅全部事件。
  subscribe(handler: TradingEventHandler): Unsubscribe
  // 双参数重载表示只订阅匹配指定模式的事件。
  subscribe(pattern: string, handler: TradingEventHandler): Unsubscribe
  // 实现签名统一处理两个公开重载。
  subscribe(patternOrHandler: string | TradingEventHandler, maybeHandler?: TradingEventHandler): Unsubscribe {
    // 只传函数时默认使用 * 通配模式。
    const pattern = typeof patternOrHandler === 'string' ? patternOrHandler : '*'
    // 根据第一个参数类型选择真正处理器。
    const handler = typeof patternOrHandler === 'function' ? patternOrHandler : maybeHandler
    // 运行时防御 JavaScript 调用方绕过 TypeScript 类型。
    if (!handler) throw new Error('TradingEventBus.subscribe requires a handler')

    // 分配 ID、保存订阅，并返回闭包式取消函数。
    const id = this.nextId++
    this.subscriptions.set(id, { pattern, handler })
    return () => this.subscriptions.delete(id)
  }

  // once 在第一次匹配后先取消自己，再执行业务处理器。
  once(pattern: string, handler: TradingEventHandler): Unsubscribe {
    let unsubscribe: Unsubscribe = () => undefined
    unsubscribe = this.subscribe(pattern, (event) => {
      unsubscribe()
      handler(event)
    })
    return unsubscribe
  }

  // dispatch 按注册顺序同步调用所有匹配订阅者。
  dispatch(event: TradingEvent): void {
    // 展开成快照，允许处理器在执行过程中安全取消订阅。
    for (const { pattern, handler } of [...this.subscriptions.values()]) {
      if (matchesEvent(event, pattern)) handler(event)
    }
  }

  // clear 用于测试、热更新或整体销毁时清空订阅。
  clear(): void {
    this.subscriptions.clear()
  }
}

// 共享单例让任意上层 UI 模块都能注册事件，而无需层层传递 props。
export const tradingEvents = new TradingEventBus()
