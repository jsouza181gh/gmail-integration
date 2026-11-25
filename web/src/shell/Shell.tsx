import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Outlet, useNavigate } from 'react-router'
import { logout } from '../api/endpoints'
import { useMe, usePendingCount } from '../api/hooks'
import { BottomNav } from './BottomNav'
import { MoreSheet } from './MoreSheet'
import { SideNav } from './SideNav'
import { WarningsBanner } from './WarningsBanner'

export function Shell() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const me = useMe()
  const pending = usePendingCount()
  const [moreOpen, setMoreOpen] = useState(false)

  const signOut = async () => {
    try {
      await logout()
    } finally {
      qc.clear()
      navigate('/login', { replace: true })
    }
  }

  return (
    <div className="shell ia-root">
      <div className="shell__side">
        <SideNav
          pending={pending}
          username={me.data?.username ?? ''}
          admin={me.data?.role === 'admin'}
          onSignOut={() => void signOut()}
        />
      </div>
      <main className="shell__main">
        <WarningsBanner />
        <Outlet />
      </main>
      <div className="shell__bottom">
        <BottomNav pending={pending} onMore={() => setMoreOpen(true)} />
      </div>
      {moreOpen ? <MoreSheet
          admin={me.data?.role === 'admin'}
          onClose={() => setMoreOpen(false)}
          onSignOut={() => void signOut()}
        /> : null}
    </div>
  )
}
