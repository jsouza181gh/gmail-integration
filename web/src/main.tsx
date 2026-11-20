import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import './styles/fonts'
import './styles/tokens.css'
import './styles/bundle.css'
import './styles/app.css'
import './styles/web.css'
import App from './App'

const root = document.getElementById('root')
if (!root) throw new Error('#root ausente')

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
