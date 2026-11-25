import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import * as api from '../api/endpoints'
import { ApiError } from '../api/errors'
import { useMe } from '../api/hooks'
import type { GmailStatus, Session } from '../api/schemas'
import { formatDateTime } from '../lib/format'
import { SECURITY_KIND_OPTIONS, securityKind } from '../lib/labels'
import { navigateTo } from '../lib/nav'
import { Badge } from '../ui/Badge'
import { Empty } from '../ui/Empty'
import { Icon } from '../ui/Icon'
import { Page, PageHead } from '../ui/Page'
import { useToast } from '../ui/Toast'

const DAY_MS = 86_400_000

function describeAgent(ua: string | null): string {
  if (!ua) return 'Navegador desconhecido'
  const browser = ['Edg', 'Chrome', 'Firefox', 'Safari'].find((b) => ua.includes(b))
  const os = ['Windows', 'iPhone', 'Android', 'Mac', 'Linux'].find((o) => ua.includes(o))
  const parts = [browser === 'Edg' ? 'Edge' : browser, os].filter((p): p is string => Boolean(p))
  return parts.length ? parts.join(', ') : ua.slice(0, 40)
}

const isPhone = (ua: string | null): boolean => /iPhone|Android|Mobile/i.test(ua ?? '')

function expiresIn(s: Session, now: Date): string {
  const days = Math.ceil((Date.parse(s.expires_at) - now.getTime()) / DAY_MS)
  if (Number.isNaN(days)) return 'expira em data desconhecida'
  return days <= 0 ? 'expira hoje' : `expira em ${days} ${days === 1 ? 'dia' : 'dias'}`
}

const GMAIL_RESULTS: Record<string, string> = {
  ok: 'Gmail reconectado.',
  cancelado: 'Você cancelou a conexão. Nada foi alterado.',
  'outro-email': 'Essa é outra conta do Google. Entre com o mesmo e-mail que usa no assistente.',
  'sem-token': 'O Google não completou a autorização. Tente de novo.',
  'sem-permissao': 'O Google não deu todas as permissões. Tente de novo e deixe todas marcadas.',
  erro: 'Não foi possível conectar o Gmail. Tente de novo.',
}

const GMAIL_STATE: Record<GmailStatus['status'], { label: string; text: string }> = {
  connected: { label: 'Conectado', text: 'O assistente está recebendo e lendo seus e-mails novos.' },
  disconnected: { label: 'Desconectado', text: 'O assistente não está lendo seus e-mails. Reconecte para voltar a funcionar.' },
  revoked: { label: 'Acesso revogado', text: 'O Google encerrou o acesso do assistente ao seu Gmail. Reconecte para voltar a funcionar.' },
  template: { label: 'Conta de modelo', text: 'Esta conta não lê e-mails.' },
}

function GmailCard() {
  const toast = useToast()
  const [params, setParams] = useSearchParams()
  const status = useQuery({ queryKey: ['gmail-status'], queryFn: api.gmailStatus })
  const connect = useMutation({
    mutationFn: api.connectGmail,
    onSuccess: ({ url }) => navigateTo(url),
    onError: (err) => toast.show(err instanceof ApiError ? err.detail : 'Não foi possível iniciar a conexão.'),
  })

  const result = params.get('gmail')
  useEffect(() => {
    if (!result) return
    toast.show(GMAIL_RESULTS[result] ?? GMAIL_RESULTS.erro!)
    setParams({}, { replace: true })
  }, [result, toast, setParams])

  const data = status.data
  const healthy = data?.status === 'connected'
  const info = data ? GMAIL_STATE[data.status] : null
  return (
    <section className="ia-card" style={{ display: 'grid', gap: 12 }} aria-label="Gmail">
      <div className="spread">
        <h2 className="h-heading">Gmail</h2>
        {info ? (
          healthy ? <Badge tone="accent" icon="check">{info.label}</Badge> : <Badge tone="warning" icon="alert">{info.label}</Badge>
        ) : null}
      </div>
      {status.isError ? <p className="t-small ia-muted" style={{ margin: 0 }}>Não foi possível carregar o estado do Gmail.</p> : null}
      {data && info ? (
        <>
          <p className="t-small" style={{ margin: 0 }}>{data.email_address}</p>
          <p className="t-small ia-muted" style={{ margin: 0 }}>{info.text}</p>
          {data.last_sync_at ? (
            <p className="t-small ia-muted" style={{ margin: 0 }}>{`Última leitura: ${formatDateTime(data.last_sync_at)}`}</p>
          ) : null}
          {!healthy && data.last_error ? <p className="warn-line" style={{ margin: 0 }}>{data.last_error}</p> : null}
        </>
      ) : null}
      <div>
        <button
          type="button"
          className={`ia-btn ia-btn--sm${healthy ? '' : ' ia-btn--primary'}`}
          disabled={connect.isPending || status.isPending}
          onClick={() => connect.mutate()}
        >
          <Icon name="mail" />
          Reconectar Gmail
        </button>
      </div>
    </section>
  )
}

export default function Seguranca() {
  const qc = useQueryClient()
  const toast = useToast()
  const [kind, setKind] = useState('')
  const [now] = useState(() => new Date())

  const me = useMe()
  const admin = me.data?.role === 'admin'
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: api.sessions })
  // IP blocks are platform-wide: only the admin sees them
  const blocks = useQuery({ queryKey: ['blocks'], queryFn: api.securityBlocks, enabled: admin })
  const events = useQuery({ queryKey: ['security-events', kind], queryFn: () => api.securityEvents(kind || undefined) })
  const notifications = useQuery({ queryKey: ['notifications'], queryFn: api.notifications })

  const revoke = useMutation({
    mutationFn: api.revokeSession,
    onSuccess: async () => {
      toast.show('Sessão encerrada.')
      await qc.invalidateQueries({ queryKey: ['sessions'] })
      await qc.invalidateQueries({ queryKey: ['security-events'] })
    },
    onError: (err) => toast.show(err instanceof ApiError ? err.detail : 'Não foi possível encerrar a sessão.'),
  })

  const restricted = blocks.data ? blocks.data.global_lock || blocks.data.ips.length > 0 : false

  return (
    <Page>
      <PageHead
        title={!admin ? 'Segurança' : blocks.isPending ? 'Segurança' : restricted ? 'Atenção: login com restrições' : 'Tudo em ordem'}
        subtitle={
          !admin
            ? 'Os aparelhos conectados e o que aconteceu na sua conta.'
            : blocks.isPending ? undefined : restricted ? 'Veja os bloqueios abaixo.' : 'Login aberto, nenhum IP bloqueado.'
        }
        actions={
          !admin || blocks.isPending ? undefined : restricted ? (
            <Badge tone="warning" icon="alert">Login com restrições</Badge>
          ) : (
            <Badge tone="accent" icon="shield">Login aberto</Badge>
          )
        }
      />

      <div className="split">
        <div className="split__list" style={{ display: 'grid', gap: 'var(--space-4)' }}>
          <GmailCard />

          <section className="ia-card" style={{ display: 'grid', gap: 12 }} aria-label="Sessões ativas">
            <h2 className="h-heading">Sessões ativas</h2>
            {sessions.isSuccess && sessions.data.length === 0 ? (
              <p className="t-small ia-muted" style={{ margin: 0 }}>Nenhuma sessão ativa.</p>
            ) : null}
            {sessions.data?.map((s) => (
              <div key={s.family_id} className="ia-row" style={{ gridTemplateColumns: 'auto 1fr auto', alignItems: 'center', cursor: 'default' }}>
                <Icon name={isPhone(s.user_agent) ? 'smartphone' : 'monitor'} size={18} />
                <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                  <span className="row ia-row__subject" style={{ fontSize: 14 }}>
                    {describeAgent(s.user_agent)}
                    {s.current ? <Badge tone="accent">esta</Badge> : null}
                  </span>
                  <span className="ia-row__from">{`desde ${formatDateTime(s.started_at)}, ${expiresIn(s, now)}`}</span>
                </span>
                {s.current ? (
                  <span />
                ) : (
                  <button
                    type="button"
                    className="ia-btn ia-btn--danger ia-btn--sm"
                    disabled={revoke.isPending}
                    onClick={() => revoke.mutate(s.family_id)}
                  >
                    <Icon name="logout" />
                    Encerrar
                  </button>
                )}
              </div>
            ))}
          </section>

          {admin ? (
          <section className="ia-card" style={{ display: 'grid', gap: 12 }} aria-label="Bloqueios">
            <h2 className="h-heading">Bloqueios</h2>
            {blocks.data?.global_lock ? (
              <p className="warn-line" style={{ margin: 0 }}>
                <span className="ia-ico-warning"><Icon name="lock" /></span>
                Login fechado para todos.
              </p>
            ) : null}
            {blocks.data?.ips.map((b) => (
              <div key={b.ip} className="row t-small">
                <span className="ia-ico-danger"><Icon name="ban" /></span>
                <span>{`${b.ip}, ${b.reason}`}</span>
                <span className="ia-num ia-subtle" style={{ marginLeft: 'auto' }}>{formatDateTime(b.since)}</span>
              </div>
            ))}
            {blocks.isSuccess && !restricted ? (
              <p className="t-small ia-muted" style={{ margin: 0 }}>Nenhum IP bloqueado.</p>
            ) : null}
            <p className="t-small ia-muted" style={{ margin: 0 }}>
              Desbloqueio só pela VPS (<code style={{ fontFamily: 'var(--font-mono)' }}>src.cli.unlock</code>).
            </p>
          </section>
          ) : null}

          <section className="ia-card" style={{ display: 'grid', gap: 12 }} aria-label="Alertas enviados">
            <h2 className="h-heading">Alertas enviados</h2>
            {notifications.isSuccess && notifications.data.length === 0 ? (
              <p className="t-small ia-muted" style={{ margin: 0 }}>Nenhum alerta enviado.</p>
            ) : null}
            {notifications.data?.map((n) => (
              <div key={n.id} className="row t-small">
                <span style={{ color: 'var(--info)' }}><Icon name="mail" /></span>
                <span>{n.subject}</span>
                {n.status === 'failed' ? <Badge tone="warning">falhou</Badge> : null}
                <span className="ia-num ia-subtle" style={{ marginLeft: 'auto' }}>
                  {formatDateTime(n.sent_at ?? n.created_at)}
                </span>
              </div>
            ))}
          </section>
        </div>

        <section className="split__detail ia-card" style={{ display: 'grid', gap: 12 }} aria-label="Eventos">
          <div className="spread">
            <h2 className="h-heading">Eventos</h2>
            <label className="ia-input" style={{ width: 200 }}>
              <select aria-label="Tipo" value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="">Todos os tipos</option>
                {SECURITY_KIND_OPTIONS.map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
              <Icon name="chevrondown" />
            </label>
          </div>
          {events.isSuccess && events.data.length === 0 ? <Empty text="Nenhum evento registrado." /> : null}
          {events.isError ? <Empty text="Não foi possível carregar os eventos." /> : null}
          {events.data && events.data.length > 0 ? (
            <div className="tablebox">
              <table className="table">
                <thead>
                  <tr>
                    <th>Quando</th>
                    <th>Evento</th>
                    <th>IP</th>
                    <th>Detalhe</th>
                  </tr>
                </thead>
                <tbody>
                  {events.data.map((e) => {
                    const info = securityKind(e.kind)
                    return (
                      <tr key={e.id}>
                        <td className="ia-num">{formatDateTime(e.created_at)}</td>
                        <td>
                          <Badge tone={info.tone} icon={info.icon}>{info.label}</Badge>
                        </td>
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
      </div>
    </Page>
  )
}
