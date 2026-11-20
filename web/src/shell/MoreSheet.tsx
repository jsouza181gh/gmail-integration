import { useEffect } from 'react'
import { Link } from 'react-router'
import { useBackfill } from '../api/hooks'
import { Badge } from '../ui/Badge'
import { Icon, type IconName } from '../ui/Icon'

const LINKS: Array<{ to: string; label: string; icon: IconName }> = [
  { to: '/chat', label: 'Chat', icon: 'message' },
  { to: '/agente', label: 'Agente', icon: 'bot' },
  { to: '/execucoes', label: 'Execuções', icon: 'activity' },
  { to: '/seguranca', label: 'Segurança', icon: 'shield' },
]

export function MoreSheet({ onClose, onSignOut }: { onClose: () => void; onSignOut: () => void }) {
  const backfill = useBackfill()
  const paused = backfill.data?.deferred_until != null

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="sheet-scrim" onClick={onClose}>
      <nav className="ia-card sheet" aria-label="Mais" onClick={(e) => e.stopPropagation()}>
        <span className="sheet__grip" aria-hidden="true" />
        {LINKS.map((l) => (
          <Link key={l.to} to={l.to} className="ia-sidenav__item sheet__item" onClick={onClose}>
            <Icon name={l.icon} size={18} />
            {l.label}
            {l.to === '/execucoes' && paused ? (
              <span style={{ marginLeft: 'auto' }}>
                <Badge tone="warning" icon="pause">
                  pausado
                </Badge>
              </span>
            ) : null}
          </Link>
        ))}
        <hr className="divider" style={{ margin: '6px 0' }} />
        <button type="button" className="ia-sidenav__item sheet__item sheet__btn" onClick={onSignOut}>
          <span className="ia-ico-danger">
            <Icon name="logout" size={18} />
          </span>
          Sair
        </button>
      </nav>
    </div>
  )
}
