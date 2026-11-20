import type { ReactNode } from 'react'

export function Page({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return <div className={wide ? 'page page--wide' : 'page'}>{children}</div>
}

export function PageHead({
  title,
  subtitle,
  kicker,
  actions,
}: {
  title: string
  subtitle?: ReactNode
  kicker?: string
  actions?: ReactNode
}) {
  return (
    <header className="page-head">
      <div className="page-head__text">
        {kicker ? <span className="t-small ia-subtle">{kicker}</span> : null}
        <h1 className="h-display">{title}</h1>
        {subtitle ? (
          <p className="t-body ia-muted" style={{ margin: 0 }}>
            {subtitle}
          </p>
        ) : null}
      </div>
      {actions ? <div className="toolbar">{actions}</div> : null}
    </header>
  )
}
