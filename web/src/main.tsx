// React 提供 StrictMode，用于开发时发现不安全的副作用。
import React from 'react'
// createRoot 是 React 18 的并发渲染入口。
import ReactDOM from 'react-dom/client'
// ConfigProvider 统一注入 Ant Design 的语言和设计令牌。
import { ConfigProvider, theme } from 'antd'
// 使用 Ant Design 官方简体中文组件文案。
import zhCN from 'antd/locale/zh_CN'
// App 是页面的根业务组件。
import App from './App'
// 全局样式包含页面布局、主题补充和响应式规则。
import './styles.css'

// Vite 的 index.html 保证存在 #root；非空断言告诉 TypeScript 该节点有效。
ReactDOM.createRoot(document.getElementById('root')!).render(
  // StrictMode 只在开发环境额外检查，不改变生产页面结构。
  <React.StrictMode>
    {/* ConfigProvider 让所有后代组件共享中文 locale 与暗色主题。 */}
    <ConfigProvider
      locale={zhCN}
      theme={{
        // 以 Ant Design 暗色算法为基础生成完整色阶。
        algorithm: theme.darkAlgorithm,
        // token 控制全局品牌色、背景、边框、文字和字体。
        token: {
          colorPrimary: '#48d597',
          colorInfo: '#61d7e8',
          colorBgBase: '#07100e',
          colorBgContainer: '#0d1916',
          colorBorder: '#20342e',
          colorText: '#e7f5ef',
          colorTextSecondary: '#86a49a',
          borderRadius: 10,
          fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        },
        // components 只微调特定 Ant Design 组件。
        components: {
          Card: { headerBg: 'transparent' },
          Button: { primaryShadow: 'none' },
          Tabs: { itemSelectedColor: '#73e8b4', itemHoverColor: '#73e8b4' },
        },
      }}
    >
      {/* 挂载 TradingAgents 的完整页面。 */}
      <App />
    </ConfigProvider>
  </React.StrictMode>,
)
