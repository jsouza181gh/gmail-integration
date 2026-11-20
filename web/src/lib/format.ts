const DASH = '—'
const WEEKDAYS = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb']
const DAY_MS = 86_400_000

const pad = (n: number): string => String(n).padStart(2, '0')
const PLAIN_DATE = /^(\d{4})-(\d{2})-(\d{2})$/

export function formatMoney(cents: number | null): string {
  if (cents === null) return DASH
  const value = (cents / 100).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return `R$ ${value}`
}

function toDate(value: string | Date | null): Date | null {
  if (value === null) return null
  const d = value instanceof Date ? value : new Date(value)
  return Number.isNaN(d.getTime()) ? null : d
}

export function formatDate(value: string | Date | null): string {
  if (typeof value === 'string') {
    const m = PLAIN_DATE.exec(value)
    if (m) return `${m[3]}/${m[2]}/${m[1]}`
  }
  const d = toDate(value)
  return d ? `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}` : DASH
}

export function formatDateTime(value: string | Date | null): string {
  const d = toDate(value)
  if (!d) return DASH
  return `${formatDate(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function dayNumber(d: Date): number {
  return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / DAY_MS
}

export function formatWhen(value: string | Date | null, now: Date): string {
  const d = toDate(value)
  if (!d) return DASH
  const ago = dayNumber(now) - dayNumber(d)
  if (ago === 0) return `${pad(d.getHours())}:${pad(d.getMinutes())}`
  if (ago === 1) return 'ontem'
  if (ago > 1 && ago < 7) return WEEKDAYS[d.getDay()] ?? DASH
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}`
}

export interface DueLabel {
  text: string
  soon: boolean
}

export function dueLabel(deadline: string | null, now: Date): DueLabel | null {
  if (deadline === null) return null
  const m = PLAIN_DATE.exec(deadline)
  if (!m) return null
  const due = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) / DAY_MS
  const diff = due - dayNumber(now)
  if (diff < 0) {
    const n = -diff
    return { text: `Venceu há ${n} ${n === 1 ? 'dia' : 'dias'}`, soon: true }
  }
  if (diff === 0) return { text: 'Vence hoje', soon: true }
  if (diff === 1) return { text: 'Vence amanhã', soon: true }
  if (diff <= 3) return { text: `Vence em ${diff} dias`, soon: true }
  return { text: `Vence ${m[3]}/${m[2]}`, soon: false }
}

export function subjectOf(e: { subject: string | null }): string {
  return e.subject?.trim() ? e.subject : '(sem assunto)'
}

const NAMED = /^\s*"?([^"<]+?)"?\s*<([^>]+)>\s*$/

export function senderName(sender: string | null): string {
  const raw = sender?.trim()
  if (!raw) return '(sem remetente)'
  const m = NAMED.exec(raw)
  return m?.[1]?.trim() || raw
}

export function senderAddress(sender: string | null): string | null {
  const raw = sender?.trim()
  if (!raw) return null
  const m = NAMED.exec(raw)
  return (m?.[2] ?? raw).trim().toLowerCase()
}

export function formatPercent(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return DASH
  return `${value.toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%`
}

const WEEKDAYS_LONG = ['Domingo', 'Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado']
const MONTHS = [
  'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
  'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
]

export function longDate(d: Date): string {
  return `${WEEKDAYS_LONG[d.getDay()]}, ${d.getDate()} de ${MONTHS[d.getMonth()]}`
}

export function formatInt(n: number): string {
  return n.toLocaleString('pt-BR')
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1).replace('.', ',')} s`
}

export function formatTime(value: string | Date | null): string {
  const d = toDate(value)
  return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : DASH
}

export function formatEta(hours: number | null): string {
  if (hours === null || !Number.isFinite(hours)) return DASH
  return hours >= 48 ? `≈ ${Math.round(hours / 24)} dias` : `≈ ${Math.round(hours)} h`
}
