import type { ReactNode } from 'react'
import { Link } from 'react-router'

interface RowProps {
  from: string
  date: string
  subject: string
  snippet?: string | null
  unread?: boolean
  selected?: boolean
  meta?: ReactNode
  to?: string
  onClick?: () => void
}

/** Email list row (`ia-row`): a link when `to` is given, otherwise a button. */
export function EmailRow({ from, date, subject, snippet, unread, selected, meta, to, onClick }: RowProps) {
  const cls = unread ? 'ia-row ia-row--unread' : 'ia-row'
  const body = (
    <>
      <span className="ia-row__from">{from}</span>
      <span className="ia-num ia-row__date">{date}</span>
      <span className="ia-row__subject">{subject}</span>
      {snippet ? <span className="ia-row__snippet">{snippet}</span> : null}
      {meta ? <span className="ia-row__meta">{meta}</span> : null}
    </>
  )
  if (to) {
    return (
      <Link className={cls} to={to} aria-selected={selected}>
        {body}
      </Link>
    )
  }
  return (
    <button type="button" className={cls} aria-selected={selected} onClick={onClick}>
      {body}
    </button>
  )
}
