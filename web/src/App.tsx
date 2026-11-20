import { QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { onSessionLost } from './api/client'
import { makeQueryClient } from './api/hooks'
import { AppRoutes } from './routes'
import { ToastProvider } from './ui/Toast'

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
      if (here.pathname === '/login') return
      navigate('/login', { replace: true, state: { from: here.pathname + here.search } })
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
