import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary'
import { iniciarPwa } from './lib/pwa'
import { ProveedorUI } from './ui'

// Installable app: service worker (production builds) and the install prompt.
iniciarPwa()

const raiz = document.getElementById('root')
if (!raiz) throw new Error('index.html has no #root element')

createRoot(raiz).render(
  <StrictMode>
    <ProveedorUI>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </ProveedorUI>
  </StrictMode>,
)
