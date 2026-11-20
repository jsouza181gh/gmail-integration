import type { ReactNode } from 'react'
import { Icon } from './Icon'
import type { IconName } from './iconNames'

export type BadgeTone = 'neutral' | 'accent' | 'warning' | 'danger' | 'info' | 'strong'

export function Badge({ tone = 'neutral', icon, children }: { tone?: BadgeTone; icon?: IconName; children: ReactNode }) {
  const cls = tone === 'neutral' ? 'ia-badge' : `ia-badge ia-badge--${tone}`
  return (
    <span className={cls}>
      {icon ? <Icon name={icon} size={14} /> : null}
      {children}
    </span>
  )
}
