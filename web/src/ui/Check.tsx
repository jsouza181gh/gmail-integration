import type { ReactNode } from 'react'
import { Icon } from './Icon'

export function Check({
  checked,
  onChange,
  label,
  children,
}: {
  checked: boolean
  onChange: () => void
  label?: string
  children?: ReactNode
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      className="ia-check check-btn"
      onClick={onChange}
    >
      <span className="ia-check__box">
        <Icon name="check" />
      </span>
      {children}
    </button>
  )
}
