import { NavLink } from 'react-router'
import { Icon, type IconName } from '../ui/Icon'

export interface NavItem {
  to: string
  label: string
  icon: IconName
  end?: boolean
}

export const MAIN_ITEMS: NavItem[] = [
  { to: '/', label: 'Relevantes', icon: 'sparkles', end: true },
  { to: '/caixa', label: 'Caixa', icon: 'inbox' },
  { to: '/aprovacoes', label: 'Aprovações', icon: 'checkcheck' },
  { to: '/documentos', label: 'Documentos', icon: 'filetext' },
  { to: '/chat', label: 'Chat', icon: 'message' },
]

export const SYSTEM_ITEMS: NavItem[] = [
  { to: '/agente', label: 'Agente', icon: 'bot' },
  { to: '/execucoes', label: 'Execuções', icon: 'activity' },
  { to: '/seguranca', label: 'Segurança', icon: 'shield' },
]

function Item({ item, count }: { item: NavItem; count?: number }) {
  return (
    <NavLink
      to={item.to}
      end={item.end}
      className="ia-sidenav__item"
      aria-label={item.label}
      title={item.label}
    >
      <Icon name={item.icon} size={18} />
      <span className="sn-txt">{item.label}</span>
      {count ? <span className="ia-pill">{count}</span> : null}
    </NavLink>
  )
}

export function SideNav({ pending, username, onSignOut }: { pending: number; username: string; onSignOut: () => void }) {
  return (
    <nav className="ia-sidenav sn" aria-label="Principal">
      <div className="ia-sidenav__brand">
        <span className="ia-sidenav__mark">
          <Icon name="mail" />
        </span>
        <span className="sn-txt">Inbox Agent</span>
      </div>
      {MAIN_ITEMS.map((item) => (
        <Item key={item.to} item={item} count={item.to === '/aprovacoes' ? pending : undefined} />
      ))}
      <span className="ia-label ia-sidenav__section">Sistema</span>
      {SYSTEM_ITEMS.map((item) => (
        <Item key={item.to} item={item} />
      ))}
      <div className="sn-foot">
        <span className="sn-avatar" aria-hidden="true">
          {username.slice(0, 1).toUpperCase()}
        </span>
        <span className="sn-user-txt">{username}</span>
        <button type="button" className="ia-btn ia-btn--ghost ia-btn--icon" aria-label="Sair" onClick={onSignOut}>
          <Icon name="logout" />
        </button>
      </div>
    </nav>
  )
}
