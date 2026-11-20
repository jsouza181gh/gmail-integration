import type { ReactNode } from 'react'

export function Empty({ text, action }: { text: string; action?: ReactNode }) {
  return (
    <div className="empty empty-box">
      <p style={{ margin: 0 }}>{text}</p>
      {action}
    </div>
  )
}
