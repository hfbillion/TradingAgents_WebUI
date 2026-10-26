import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.darkAlgorithm,
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
        components: {
          Card: { headerBg: 'transparent' },
          Button: { primaryShadow: 'none' },
          Tabs: { itemSelectedColor: '#73e8b4', itemHoverColor: '#73e8b4' },
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>,
)
