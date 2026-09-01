import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App'
import './styles.css'

const rootElement = document.getElementById('root')

if (rootElement === null) {
  throw new Error('Application root element is unavailable')
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
