import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router'
import * as api from '../api/endpoints'
import { ApiError } from '../api/errors'
import { useMe } from '../api/hooks'
import type { AdminAccount, AdminInvite } from '../api/schemas'
import { formatDateTime } from '../lib/format'
import { securityKind } from '../lib/labels'
import { Badge, type BadgeTone } from '../ui/Badge'
import { Empty } from '../ui/Empty'
import { Icon } from '../ui/Icon'
import { Page, PageHead } from '../ui/Page'
import { useToast } from '../ui/Toast'

const message = (err: unknown, fallback: string): string => (err instanceof ApiError ? err.detail : fallback)

const STATUS: Record<string, { label: string; tone: BadgeTone }> = {
  connected: { label: 'conectado', tone: 'accent' },
  disconnected: { label: 'desconectado', tone: 'warning' },
  revoked: { label: 'revogado', tone: 'danger' },
  template: { label: 'modelo', tone: 'neutral' },
}

const ERROR_KIND: Record<NonNullable<AdminAccount['error_kind']>, string> = {
  revoked: 'acesso revogado',
  quota: 'limite do Google',
  network: 'falha de rede',
  other: 'outro erro',
}

const INVITE_STATE: Record<AdminInvite['state'], { label: string; tone: BadgeTone }> = {
  pending: { label: 'aguardando', tone: 'info' },
  accepted: { label: 'aceito', tone: 'accent' },
  revoked: { label: 'revogado', tone: 'neutral' },
}

const absolute = (link: string): string => (link.startsWith('/') ? `${window.location.origin}${link}` : link)

function InvitesCard() {
  const qc = useQueryClient()
  const toast = useToast()
  const [email, setEmail] = useState('')
  const [link, setLink] = useState<string | null>(null)
  const invites = useQuery({ queryKey: ['admin-invites'], queryFn: api.adminInvites })
  const refresh = () => qc.invalidateQueries({ queryKey: ['admin-invites'] })

  const create = useMutation({
    mutationFn: () => api.createInvite(email.trim()),
    onSuccess: async (out) => {
      setLink(absolute(out.link))
      setEmail('')
      await refresh()
    },
  })
  const revoke = useMutation({
    mutationFn: api.revokeInvite,
    onSuccess: async () => {
      toast.show('Convite revogado.')
      await refresh()
    },
    onError: (err) => toast.show(message(err, 'Não foi possível revogar.')),
  })

  const copy = async () => {
    if (!link) return
    try {
      await navigator.clipboard.writeText(link)
      toast.show('Link copiado.')
    } catch {
      toast.show('Não deu para copiar sozinho: selecione o link e copie.')
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    create.mutate()
  }

  return (
    <section className="ia-card stack" aria-label="Convites">
      <h2 className="h-heading">Convites</h2>
      <p className="t-small ia-muted" style={{ margin: 0 }}>
        Só quem tem convite entra. Digite o e-mail da pessoa e mande o link que aparecer (WhatsApp, por exemplo).
      </p>
      <form className="copy-box" onSubmit={submit}>
        <label className="ia-input">
          <input
            type="email"
            aria-label="E-mail da pessoa"
            placeholder="amigo@gmail.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </label>
        <button type="submit" className="ia-btn ia-btn--primary ia-btn--sm" disabled={create.isPending}>
          <Icon name="plus" />
          Convidar
        </button>
      </form>
      {create.isError ? <p className="warn-line" role="alert">{message(create.error, 'Não foi possível criar o convite.')}</p> : null}
      {link ? (
        <div className="stack">
          <span className="ia-label">Link do convite</span>
          <div className="copy-box">
            <label className="ia-input">
              <input readOnly aria-label="Link do convite" value={link} onFocus={(e) => e.currentTarget.select()} />
            </label>
            <button type="button" className="ia-btn ia-btn--sm" onClick={() => void copy()}>
              <Icon name="copy" />
              Copiar
            </button>
          </div>
        </div>
      ) : null}
      {invites.isSuccess && invites.data.length === 0 ? (
        <p className="t-small ia-muted" style={{ margin: 0 }}>Nenhum convite ainda.</p>
      ) : null}
      {invites.data?.map((i) => (
        <div key={i.id} className="spread t-small">
          <span className="row" style={{ minWidth: 0 }}>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{i.email}</span>
            <Badge tone={INVITE_STATE[i.state].tone}>{INVITE_STATE[i.state].label}</Badge>
          </span>
          {i.state === 'pending' ? (
            <button
              type="button"
              className="ia-btn ia-btn--danger ia-btn--sm"
              aria-label={`Revogar convite de ${i.email}`}
              disabled={revoke.isPending}
              onClick={() => revoke.mutate(i.id)}
            >
              Revogar
            </button>
          ) : null}
        </div>
      ))}
    </section>
  )
}

function ResetButton({ account }: { account: AdminAccount }) {
  const toast = useToast()
  const [asking, setAsking] = useState(false)
  const reset = useMutation({
    mutationFn: () => api.resetTotp(account.user_id),
    onSuccess: () => {
      setAsking(false)
      toast.show(`2FA de ${account.email} resetado. Ela cadastra um novo no próximo login.`)
    },
    onError: (err) => toast.show(message(err, 'Não foi possível resetar.')),
  })
  if (!asking) {
    return (
      <button type="button" className="ia-btn ia-btn--sm" aria-label={`Resetar 2FA de ${account.email}`} onClick={() => setAsking(true)}>
        Resetar 2FA
      </button>
    )
  }
  return (
    <span className="row">
      <button type="button" className="ia-btn ia-btn--danger ia-btn--sm" disabled={reset.isPending} onClick={() => reset.mutate()}>
        Confirmar
      </button>
      <button type="button" className="ia-btn ia-btn--ghost ia-btn--sm" onClick={() => setAsking(false)}>
        Cancelar
      </button>
    </span>
  )
}

function AccountsCard() {
  const accounts = useQuery({ queryKey: ['admin-accounts'], queryFn: api.adminAccounts, refetchInterval: 30_000 })
  return (
    <section className="ia-card stack" aria-label="Contas">
      <h2 className="h-heading">Contas</h2>
      <p className="t-small ia-muted" style={{ margin: 0 }}>
        Só o estado de cada conta. Nenhum e-mail, assunto ou conversa aparece aqui.
      </p>
      {accounts.isError ? <Empty text="Não foi possível carregar as contas." /> : null}
      {accounts.data ? (
        <div className="tablebox">
          <table className="table">
            <thead>
              <tr>
                <th>Conta</th>
                <th>Gmail</th>
                <th>Último sync</th>
                <th>Erro</th>
                <th>Fila</th>
                <th>Chave</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {accounts.data.map((a) => {
                const st = STATUS[a.status] ?? { label: a.status, tone: 'neutral' as const }
                return (
                  <tr key={a.user_id}>
                    <td>
                      {a.email}
                      {a.role === 'admin' ? <> <Badge>admin</Badge></> : null}
                    </td>
                    <td><Badge tone={st.tone}>{st.label}</Badge></td>
                    <td className="ia-num">{a.last_sync_at ? formatDateTime(a.last_sync_at) : '—'}</td>
                    <td>{a.error_kind ? <Badge tone="warning">{ERROR_KIND[a.error_kind]}</Badge> : '—'}</td>
                    <td className="ia-num">
                      {a.queue_depth}
                      {a.dead_jobs > 0 ? <> <Badge tone="danger">{`${a.dead_jobs} com falha`}</Badge></> : null}
                    </td>
                    <td>
                      {a.key_ok === null ? <Badge tone="warning">sem chave</Badge> : a.key_ok ? <Badge tone="accent">ok</Badge> : <Badge tone="danger">inválida</Badge>}
                    </td>
                    <td><ResetButton account={a} /></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  )
}

function PlatformEventsCard() {
  const events = useQuery({ queryKey: ['admin-events'], queryFn: api.platformEvents })
  return (
    <section className="ia-card stack" aria-label="Eventos da plataforma">
      <h2 className="h-heading">Eventos da plataforma</h2>
      <p className="t-small ia-muted" style={{ margin: 0 }}>
        Bloqueios de IP e travas de login. Para desbloquear, use o terminal no servidor.
      </p>
      {events.isSuccess && events.data.length === 0 ? <p className="t-small ia-muted" style={{ margin: 0 }}>Nenhum evento.</p> : null}
      {events.data && events.data.length > 0 ? (
        <div className="tablebox">
          <table className="table">
            <thead>
              <tr><th>Quando</th><th>Evento</th><th>IP</th><th>Detalhe</th></tr>
            </thead>
            <tbody>
              {events.data.map((e) => {
                const info = securityKind(e.kind)
                return (
                  <tr key={e.id}>
                    <td className="ia-num">{formatDateTime(e.created_at)}</td>
                    <td><Badge tone={info.tone} icon={info.icon}>{info.label}</Badge></td>
                    <td className="ia-num">{e.ip ?? '—'}</td>
                    <td className="ia-muted">{e.detail || '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  )
}

export default function Admin() {
  const me = useMe()
  if (me.data && me.data.role !== 'admin') return <Navigate to="/" replace />
  if (!me.data) return null
  return (
    <Page>
      <PageHead title="Administração" subtitle="Quem entra e como cada conta está. Sem conteúdo de e-mail." />
      <div className="stack">
        <InvitesCard />
        <AccountsCard />
        <PlatformEventsCard />
      </div>
    </Page>
  )
}
