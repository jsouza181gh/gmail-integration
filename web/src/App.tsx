import { QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { onNoAccount, onSessionLost } from './api/client'
import { makeQueryClient } from './api/hooks'
import { AppRoutes } from './routes'
import { ToastProvider } from './ui/Toast'

const PUBLIC = new Set(['/login', '/entrar', '/nao-convidado'])

// A failed refresh means the session is gone: forget cached data and go to login,
// remembering where the person was.
function SessionWatcher() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const where = useRef(location)
  useEffect(() => {
    where.current = location
  })
  useEffect(() => {
    onSessionLost(() => {
      const here = where.current
      qc.clear()
      if (PUBLIC.has(here.pathname)) return
      navigate('/login', { replace: true, state: { from: here.pathname + here.search } })
    })
    // logged in, but no Gmail yet: onboarding (phase 3) picks the user up where they stopped
    onNoAccount(() => {
      if (!where.current.pathname.startsWith('/onboarding')) navigate('/onboarding', { replace: true })
    })
  }, [qc, navigate])
  return null
}

export default function App() {
  const [client] = useState(makeQueryClient)
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <SessionWatcher />
        <AppRoutes />
      </ToastProvider>
    </QueryClientProvider>
  )
}
