import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import * as api from '../api/endpoints'
import { ApiError } from '../api/errors'
import { qk } from '../api/hooks'
import type { KeysStatus, NewTotp } from '../api/schemas'
import { formatDateTime } from '../lib/format'
import { navigateTo } from '../lib/nav'
import { Badge } from '../ui/Badge'
import { Icon } from '../ui/Icon'
import { Page, PageHead } from '../ui/Page'
import { useToast } from '../ui/Toast'

const message = (err: unknown, fallback: string): string => (err instanceof ApiError ? err.detail : fallback)

function Field({
  label,
  type = 'text',
  value,
  onChange,
  autoComplete,
  placeholder,
}: {
  label: string
  type?: string
  value: string
  onChange: (v: string) => void
  autoComplete?: string
  placeholder?: string
}) {
  return (
    <label className="ia-field">
      <span className="ia-label">{label}</span>
      <span className="ia-input">
        <input
          type={type}
          value={value}
          autoComplete={autoComplete}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      </span>
    </label>
  )
}

function ErrorLine({ text }: { text: string }) {
  return (
    <p className="warn-line" role="alert">
      <span className="ia-ico-warning">
        <Icon name="alert" />
      </span>
      {text}
    </p>
  )
}

const GMAIL_STATE: Record<string, { label: string; tone: 'accent' | 'warning' | 'danger' }> = {
  connected: { label: 'Conectado', tone: 'accent' },
  disconnected: { label: 'Desconectado', tone: 'warning' },
  revoked: { label: 'Precisa reconectar', tone: 'danger' },
}

function GmailCard() {
  const status = useQuery({ queryKey: ['gmail-status'], queryFn: api.gmailStatus })
  const reconnect = useMutation({ mutationFn: api.connectGmail, onSuccess: (out) => navigateTo(out.url) })
  const state = GMAIL_STATE[status.data?.status ?? ''] ?? { label: status.data?.status ?? '', tone: 'warning' as const }
  const healthy = status.data?.status === 'connected'

  return (
    <section className="ia-card stack" aria-label="Gmail">
      <div className="spread">
        <h2 className="h-heading">Gmail</h2>
        {status.data ? <Badge tone={state.tone}>{state.label}</Badge> : null}
      </div>
      {status.isError ? <p className="t-small ia-muted" style={{ margin: 0 }}>Não foi possível ver o estado do Gmail agora.</p> : null}
      {status.data ? (
        <>
          <p className="t-body" style={{ margin: 0 }}>{status.data.email_address ?? 'Nenhum Gmail conectado'}</p>
          <p className="t-small ia-muted" style={{ margin: 0 }}>
            {status.data.last_sync_at
              ? `Último e-mail recebido em ${formatDateTime(status.data.last_sync_at)}.`
              : 'Ainda não chegou nenhum e-mail.'}
          </p>
          {!healthy ? (
            <p className="t-small ia-muted" style={{ margin: 0 }}>
              Sem a conexão, e-mails novos não aparecem aqui. Reconectar leva um clique e não apaga nada.
            </p>
          ) : null}
          <div className="toolbar">
            <button
              type="button"
              className={healthy ? 'ia-btn ia-btn--sm' : 'ia-btn ia-btn--primary ia-btn--sm'}
              disabled={reconnect.isPending}
              onClick={() => reconnect.mutate()}
            >
              <Icon name="refresh" />
              Reconectar Gmail
            </button>
          </div>
          {reconnect.isError ? <ErrorLine text={message(reconnect.error, 'Não deu para abrir o Google agora.')} /> : null}
        </>
      ) : null}
    </section>
  )
}

function keysSummary(k: KeysStatus): { label: string; tone: 'accent' | 'warning' | 'danger'; text: string } {
  if (!k.configured) return { label: 'Falta cadastrar', tone: 'warning', text: 'Sem a chave o assistente não lê nem responde seus e-mails.' }
  if (k.last_error) {
    return {
      label: 'Parou de funcionar',
      tone: 'danger',
      text: 'O Google recusou a chave. Cole uma chave nova abaixo para o assistente voltar a trabalhar.',
    }
  }
  return {
    label: 'Funcionando',
    tone: 'accent',
    text: k.validated_at ? `Conferida em ${formatDateTime(k.validated_at)}.` : 'Chave cadastrada.',
  }
}

function KeysCard() {
  const qc = useQueryClient()
  const toast = useToast()
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings })
  const [appKey, setAppKey] = useState('')
  const [backfillKey, setBackfillKey] = useState('')
  const save = useMutation({
    mutationFn: api.replaceLlmKeys,
    onSuccess: async () => {
      setAppKey('')
      setBackfillKey('')
      toast.show('Chave conferida e salva.')
      await qc.invalidateQueries({ queryKey: ['settings'] })
      await qc.invalidateQueries({ queryKey: qk.warnings })
    },
  })
  const keys = settings.data?.llm_keys
  const summary = keys ? keysSummary(keys) : null

  const submit = (e: FormEvent) => {
    e.preventDefault()
    save.mutate({
      ...(appKey.trim() ? { app_key: appKey.trim() } : {}),
      ...(backfillKey.trim() ? { backfill_key: backfillKey.trim() } : {}),
    })
  }

  return (
    <section className="ia-card stack" aria-label="Chaves do Gemini">
      <div className="spread">
        <h2 className="h-heading">Chave do Gemini</h2>
        {summary ? <Badge tone={summary.tone}>{summary.label}</Badge> : null}
      </div>
      {summary ? <p className="t-small ia-muted" style={{ margin: 0 }}>{summary.text}</p> : null}
      <form className="stack" onSubmit={submit}>
        <Field
          label={keys?.configured ? 'Trocar a chave' : 'Sua chave'}
          type="password"
          value={appKey}
          onChange={setAppKey}
          autoComplete="off"
          placeholder="AIza…"
        />
        <details>
          <summary className="t-small ia-muted" style={{ cursor: 'pointer' }}>
            Avançado: segunda chave só para os e-mails antigos
          </summary>
          <div className="stack" style={{ marginTop: 12 }}>
            <p className="t-small ia-muted" style={{ margin: 0 }}>
              Opcional. Com uma segunda chave, ler o histórico não gasta a cota da primeira.
              {keys?.has_backfill_key ? ' Você já tem uma segunda chave salva.' : ''}
            </p>
            <Field label="Segunda chave" type="password" value={backfillKey} onChange={setBackfillKey} autoComplete="off" placeholder="AIza…" />
            {keys?.has_backfill_key ? (
              <button
                type="button"
                className="ia-btn ia-btn--danger ia-btn--sm"
                disabled={save.isPending}
                onClick={() => save.mutate({ drop_backfill: true })}
              >
                Remover a segunda chave
              </button>
            ) : null}
          </div>
        </details>
        {save.isError ? <ErrorLine text={message(save.error, 'Não foi possível salvar a chave.')} /> : null}
        <div className="toolbar">
          <button
            type="submit"
            className="ia-btn ia-btn--primary ia-btn--sm"
            disabled={save.isPending || (!appKey.trim() && !backfillKey.trim())}
          >
            <Icon name="keyround" />
            Conferir e salvar
          </button>
        </div>
      </form>
    </section>
  )
}

function PasswordCard({ hasPassword }: { hasPassword: boolean }) {
  const toast = useToast()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: () => {
      setCurrent('')
      setNext('')
      toast.show('Senha trocada. Os outros aparelhos foram desconectados.')
    },
  })
  return (
    <section className="ia-card stack" aria-label="Senha">
      <h2 className="h-heading">Senha</h2>
      <p className="t-small ia-muted" style={{ margin: 0 }}>
        {hasPassword
          ? 'A senha é opcional: você também pode entrar pelo Google. Use pelo menos 12 caracteres.'
          : 'Você entra pelo Google. Criar uma senha é opcional e serve de segunda opção.'}
      </p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          change.mutate()
        }}
      >
        {hasPassword ? (
          <Field label="Senha atual" type="password" value={current} onChange={setCurrent} autoComplete="current-password" />
        ) : null}
        <Field label="Senha nova" type="password" value={next} onChange={setNext} autoComplete="new-password" />
        {change.isError ? <ErrorLine text={message(change.error, 'Não foi possível trocar a senha.')} /> : null}
        <div className="toolbar">
          <button type="submit" className="ia-btn ia-btn--sm" disabled={change.isPending || !next}>
            {hasPassword ? 'Trocar a senha' : 'Criar a senha'}
          </button>
        </div>
      </form>
    </section>
  )
}

function TotpCard({ hasPassword }: { hasPassword: boolean }) {
  const toast = useToast()
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [fresh, setFresh] = useState<NewTotp | null>(null)
  const rotate = useMutation({
    mutationFn: () => api.changeTotp(password, code.trim()),
    onSuccess: (out) => {
      setPassword('')
      setCode('')
      setFresh(out)
      toast.show('Autenticador trocado.')
    },
  })
  return (
    <section className="ia-card stack" aria-label="Autenticador">
      <h2 className="h-heading">Autenticador (2FA)</h2>
      {fresh ? (
        <div className="stack">
          <p className="t-small" style={{ margin: 0 }}>
            Pronto. Cadastre este código no app autenticador e guarde os códigos de recuperação: eles não aparecem de novo.
          </p>
          <label className="ia-field">
            <span className="ia-label">Chave para digitar no app</span>
            <span className="ia-input">
              <input readOnly value={fresh.secret} onFocus={(e) => e.currentTarget.select()} />
            </span>
          </label>
          <a className="ia-btn ia-btn--sm" href={fresh.otpauth_uri}>
            Abrir no app autenticador
          </a>
          <div className="ia-card" aria-label="Códigos de recuperação">
            <code className="ia-code" style={{ display: 'grid', gap: 4 }}>
              {fresh.recovery_codes.map((c) => (
                <span key={c}>{c}</span>
              ))}
            </code>
          </div>
          <div className="toolbar">
            <button type="button" className="ia-btn ia-btn--primary ia-btn--sm" onClick={() => setFresh(null)}>
              Guardei os códigos
            </button>
          </div>
        </div>
      ) : (
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault()
            rotate.mutate()
          }}
        >
          <p className="t-small ia-muted" style={{ margin: 0 }}>
            Trocou de celular? Gere um autenticador novo. O antigo e os códigos de recuperação antigos deixam de valer.
          </p>
          {hasPassword ? (
            <Field label="Senha" type="password" value={password} onChange={setPassword} autoComplete="current-password" />
          ) : null}
          <Field label="Código atual do app (6 dígitos)" value={code} onChange={setCode} autoComplete="one-time-code" placeholder="000000" />
          {rotate.isError ? <ErrorLine text={message(rotate.error, 'Não foi possível trocar o autenticador.')} /> : null}
          <div className="toolbar">
            <button type="submit" className="ia-btn ia-btn--sm" disabled={rotate.isPending || !code.trim()}>
              Gerar autenticador novo
            </button>
          </div>
        </form>
      )}
    </section>
  )
}

function SessionsCard() {
  const qc = useQueryClient()
  const toast = useToast()
  const out = useMutation({
    mutationFn: api.signOutOthers,
    onSuccess: async (r) => {
      toast.show(
        r.ended === 0
          ? 'Não havia outros aparelhos conectados.'
          : `${r.ended} ${r.ended === 1 ? 'aparelho desconectado' : 'aparelhos desconectados'}.`,
      )
      await qc.invalidateQueries({ queryKey: ['sessions'] })
    },
    onError: (err) => toast.show(message(err, 'Não foi possível desconectar os outros aparelhos.')),
  })
  return (
    <section className="ia-card stack" aria-label="Sessões">
      <h2 className="h-heading">Aparelhos conectados</h2>
      <p className="t-small ia-muted" style={{ margin: 0 }}>
        Se você entrou num computador que não é seu, desconecte os outros e continue só neste.
      </p>
      <div className="toolbar">
        <button type="button" className="ia-btn ia-btn--danger ia-btn--sm" disabled={out.isPending} onClick={() => out.mutate()}>
          <Icon name="logout" />
          Sair dos outros aparelhos
        </button>
        <Link to="/seguranca" className="ia-btn ia-btn--ghost ia-btn--sm">
          Ver a lista
        </Link>
      </div>
    </section>
  )
}

export default function Configuracoes() {
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings })
  const s = settings.data
  return (
    <Page>
      <PageHead title="Configurações" subtitle="Sua conta, seu Gmail e suas chaves." />
      <div className="cols-2">
        <div className="stack">
          <section className="ia-card stack" aria-label="Conta">
            <h2 className="h-heading">Conta</h2>
            <p className="t-body" style={{ margin: 0 }}>{s?.gmail_email ?? s?.email ?? s?.username ?? ' '}</p>
            {s ? <p className="t-small ia-muted" style={{ margin: 0 }}>{`Usuário: ${s.username}`}</p> : null}
          </section>
          <GmailCard />
          <KeysCard />
        </div>
        <div className="stack">
          {s ? <PasswordCard hasPassword={s.has_password} /> : null}
          {s ? <TotpCard hasPassword={s.has_password} /> : null}
          <SessionsCard />
        </div>
      </div>
    </Page>
  )
}
