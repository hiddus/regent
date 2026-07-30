import { useState, useEffect } from 'react'
import { invoke } from '@tauri-apps/api/core'

function App() {
  const [greetMsg, setGreetMsg] = useState('')
  const [name, setName] = useState('')
  const [apiUrl, setApiUrl] = useState('http://localhost:8000')
  const [loading, setLoading] = useState(false)

  async function greet() {
    setLoading(true)
    try {
      const message = await invoke('greet', { name })
      setGreetMsg(message)
    } catch (error) {
      console.error('Error calling greet:', error)
      setGreetMsg(`Error: ${error}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="desktop-app">
      <header className="desktop-header">
        <div className="header-content">
          <h1>🤖 Regent Desktop</h1>
          <div className="api-config">
            <label>API URL:</label>
            <input
              type="text"
              value={apiUrl}
              onChange={(e) => setApiUrl(e.target.value)}
              placeholder="http://localhost:8000"
            />
          </div>
        </div>
      </header>

      <main className="desktop-main">
        <div className="console-frame">
          <iframe
            src={`${apiUrl}/console/`}
            title="Regent Console"
            className="console-iframe"
          />
        </div>
      </main>

      <footer className="desktop-footer">
        <p>Connected to: {apiUrl}</p>
      </footer>
    </div>
  )
}

export default App
