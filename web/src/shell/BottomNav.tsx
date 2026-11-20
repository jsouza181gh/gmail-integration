import { NavLink, useLocation } from 'react-router'
import { Icon, type IconName } from '../ui/Icon'

const ITEMS: Array<{ to: string; label: string; icon: IconName; end?: boolean }> = [
  { to: '/', label: 'Relevantes', icon: 'sparkles', end: true },
  { to: '/caixa', label: 'Caixa', icon: 'inbox' },
  { to: '/aprovacoes', label: 'Aprovar', icon: 'checkcheck' },
  { to: '/documentos', label: 'Docs', icon: 'filetext' },
]

const SYSTEM_PATHS = ['/chat', '/agente', '/execucoes', '/seguranca']

export function BottomNav({ pending, onMore }: { pending: number; onMore: () => void }) {
  const { pathname } = useLocation()
  const inMore = SYSTEM_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))
  return (
    <nav className="ia-bottomnav" aria-label="Principal">
      {ITEMS.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.end} className="ia-bottomnav__item">
          <Icon name={item.icon} size={22} />
          <span>{item.label}</span>
          {item.to === '/aprovacoes' && pending ? <span className="ia-pill">{pending}</span> : null}
        </NavLink>
      ))}
      <button
        type="button"
        className="ia-bottomnav__item"
        aria-haspopup="dialog"
        aria-current={inMore ? 'page' : undefined}
        onClick={onMore}
      >
        <Icon name="menu" size={22} />
        <span>Mais</span>
      </button>
    </nav>
  )
}
