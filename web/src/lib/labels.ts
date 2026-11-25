import type { IconName } from '../ui/iconNames'

export interface ActionKindInfo {
  label: string
  icon: IconName
  danger: boolean
}

const ACTION_KINDS: Record<string, ActionKindInfo> = {
  mark_read: { label: 'Marcar lido', icon: 'mailopen', danger: false },
  star: { label: 'Estrela', icon: 'star', danger: false },
  archive: { label: 'Arquivar', icon: 'archive', danger: false },
  trash: { label: 'Lixeira', icon: 'trash', danger: true },
  block_sender: { label: 'Bloquear', icon: 'ban', danger: true },
}

export function actionKind(kind: string): ActionKindInfo {
  return ACTION_KINDS[kind] ?? { label: kind, icon: 'zap', danger: false }
}

export const POLICY_LABELS: Record<string, string> = {
  auto: 'Automática',
  suggest: 'Sugerir',
  deny: 'Negar',
}

export const TRIGGER_LABELS: Record<string, string> = {
  webhook: 'E-mail novo',
  chat: 'Chat',
}

export type Tone = 'neutral' | 'accent' | 'warning' | 'danger' | 'info'

export interface SecurityKindInfo {
  label: string
  icon: IconName
  tone: Tone
}

// Mirrors SecurityKind in src/auth/security_log.py.
const SECURITY_KINDS: Record<string, SecurityKindInfo> = {
  login_ok: { label: 'login', icon: 'check', tone: 'accent' },
  login_fail: { label: 'senha errada', icon: 'alert', tone: 'warning' },
  totp_fail: { label: 'código errado', icon: 'alert', tone: 'warning' },
  ip_blocked: { label: 'IP bloqueado', icon: 'ban', tone: 'danger' },
  global_lock: { label: 'login fechado', icon: 'lock', tone: 'danger' },
  user_lock: { label: 'usuário fechado', icon: 'lock', tone: 'danger' },
  invite_created: { label: 'convite criado', icon: 'mail', tone: 'info' },
  signup: { label: 'conta criada', icon: 'check', tone: 'accent' },
  onboarding_done: { label: 'entrada concluída', icon: 'check', tone: 'accent' },
  refresh_reuse: { label: 'sessão reutilizada', icon: 'alert', tone: 'danger' },
  login_new_ip: { label: 'login de IP novo', icon: 'mail', tone: 'warning' },
  logout: { label: 'logout', icon: 'logout', tone: 'neutral' },
  session_revoked: { label: 'sessão encerrada', icon: 'logout', tone: 'neutral' },
  csrf_invalid: { label: 'CSRF recusado', icon: 'ban', tone: 'danger' },
  download: { label: 'download', icon: 'download', tone: 'info' },
  unlock: { label: 'desbloqueio', icon: 'lock', tone: 'neutral' },
  password_changed: { label: 'senha trocada', icon: 'keyround', tone: 'info' },
  totp_changed: { label: 'autenticador trocado', icon: 'keyround', tone: 'info' },
  sessions_revoked: { label: 'outras sessões encerradas', icon: 'logout', tone: 'neutral' },
  totp_reset: { label: '2FA resetado', icon: 'lock', tone: 'warning' },
}

export function securityKind(kind: string): SecurityKindInfo {
  return SECURITY_KINDS[kind] ?? { label: kind, icon: 'alert', tone: 'neutral' }
}

export const SECURITY_KIND_OPTIONS: ReadonlyArray<[string, string]> = Object.entries(SECURITY_KINDS).map(
  ([kind, info]) => [kind, info.label],
)
