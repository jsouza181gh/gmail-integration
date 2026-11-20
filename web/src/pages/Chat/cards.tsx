// One component per card kind (src/chat/store.py CardView). Proposed cards
// carry the buttons; decided ones show their outcome.
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import type { ConfirmOptions } from '../../api/endpoints'
import type { ChatCard } from '../../api/schemas'
import { downloadDocument } from '../../lib/download'
import { POLICY_LABELS, TRIGGER_LABELS, actionKind } from '../../lib/labels'
import { Badge } from '../../ui/Badge'
import { Icon } from '../../ui/Icon'

export interface CardHandlers {
  onConfirm: (cardId: number, opts?: ConfirmOptions) => Promise<void>
  onCancel: (cardId: number) => Promise<void>
  onAdjust: (cardId: number) => Promise<void>
}

const SCOPES: Record<string, string> = {
  classification: 'ao classificar e-mails',
  decision: 'ao sugerir ações',
}

function Outcome({ card }: { card: ChatCard }) {
  if (card.status === 'confirmed') return <Badge tone="accent">Confirmada</Badge>
  if (card.status === 'cancelled') return <Badge>Cancelada</Badge>
  if (card.status === 'failed') {
    return (
      <p className="warn-line" role="alert" style={{ margin: 0 }}>
        <span className="ia-ico-warning">
          <Icon name="alert" />
        </span>
        {`Não deu certo: ${card.error ?? 'erro desconhecido'}`}
      </p>
    )
  }
  return null
}

function Shell({ label, card, children }: { label: string; card: ChatCard; children: ReactNode }) {
  return (
    <section className="ia-card chat-card" aria-label={label}>
      <span className="ia-label">{label}</span>
      {children}
      <Outcome card={card} />
    </section>
  )
}

function useBusy(): [boolean, (run: () => Promise<void>) => void] {
  const [busy, setBusy] = useState(false)
  const run = (fn: () => Promise<void>) => {
    setBusy(true)
    void fn().finally(() => setBusy(false))
  }
  return [busy, run]
}

function DecideButtons({ card, h, opts }: { card: ChatCard; h: CardHandlers; opts?: ConfirmOptions }) {
  const [busy, run] = useBusy()
  if (card.status !== 'proposed') return null
  return (
    <div className="toolbar">
      <button
        type="button"
        className="ia-btn ia-btn--primary ia-btn--sm"
        disabled={busy}
        onClick={() => run(() => h.onConfirm(card.id, opts))}
      >
        <Icon name="check" />
        Confirmar
      </button>
      <button type="button" className="ia-btn ia-btn--sm" disabled={busy} onClick={() => run(() => h.onAdjust(card.id))}>
        Ajustar
      </button>
      <button
        type="button"
        className="ia-btn ia-btn--ghost ia-btn--sm"
        disabled={busy}
        onClick={() => run(() => h.onCancel(card.id))}
      >
        Cancelar
      </button>
    </div>
  )
}

function ActionLabel({ action }: { action: string }) {
  const info = actionKind(action)
  return (
    <dd className="row">
      <span style={info.danger ? { color: 'var(--danger)' } : undefined}>
        <Icon name={info.icon} />
      </span>
      {info.label}
    </dd>
  )
}

function RuleCard({ card, h }: { card: ChatCard; h: CardHandlers }) {
  const [existing, setExisting] = useState(card.apply_existing)
  const r = card.rule
  if (!r) return null
  const when = [
    r.sender_address && ['remetente for', r.sender_address],
    r.sender_domain && ['remetente for', r.sender_domain],
    r.category && ['categoria for', r.category],
    r.subject_contains && ['assunto contiver', `“${r.subject_contains}”`],
  ].filter((x): x is string[] => Array.isArray(x))
  const affected = card.affected ?? 0
  return (
    <Shell label="Nova regra de automação" card={card}>
      <dl className="kv">
        {when.map(([what, value], i) => (
          <div key={i} style={{ display: 'contents' }}>
            <dt>{i === 0 ? 'Quando' : 'E'}</dt>
            <dd>
              {`${what} `}
              <b>{value}</b>
            </dd>
          </div>
        ))}
        {r.unless_document ? (
          <>
            <dt>Exceto</dt>
            <dd>quando tiver documento (nota, boleto…)</dd>
          </>
        ) : null}
        <dt>Ação</dt>
        <ActionLabel action={r.action} />
        <dt>Modo</dt>
        <dd>
          <Badge>Sugerir (você aprova)</Badge>
        </dd>
      </dl>
      <p className="t-small ia-muted" style={{ margin: 0 }}>
        {affected > 0 ? (
          <>
            {'Afeta '}
            <span className="ia-num">{affected}</span>
            {affected === 1 ? ' e-mail que você já tem.' : ' e-mails que você já tem.'}
          </>
        ) : (
          'Não afeta nenhum e-mail que você já tem; vale do próximo em diante.'
        )}
      </p>
      {affected > 0 && card.status === 'proposed' ? (
        <label className="row t-small">
          <input type="checkbox" checked={existing} onChange={(e) => setExisting(e.target.checked)} />
          Gerar sugestões para eles também
        </label>
      ) : null}
      <DecideButtons card={card} h={h} opts={existing ? { apply_existing: true } : {}} />
    </Shell>
  )
}

function PreferenceCard({ card, h }: { card: ChatCard; h: CardHandlers }) {
  const p = card.preference
  if (!p) return null
  return (
    <Shell label="Nova preferência" card={card}>
      <p className="t-body" style={{ margin: 0 }}>
        {p.text}
      </p>
      <p className="t-small ia-muted" style={{ margin: 0 }}>{`O agente vai considerar isso ${SCOPES[p.scope] ?? p.scope}.`}</p>
      <DecideButtons card={card} h={h} />
    </Shell>
  )
}

function PromoteCard({ card, h }: { card: ChatCard; h: CardHandlers }) {
  const p = card.promote
  if (!p) return null
  return (
    <Shell label="Tornar regra automática" card={card}>
      <dl className="kv">
        <dt>Regra</dt>
        <dd>{p.name}</dd>
        <dt>Ação</dt>
        <ActionLabel action={p.action} />
        <dt>Histórico</dt>
        <dd className="ia-num">{`${p.fired} disparos, ${p.approved} aprovadas, ${p.rejected} recusadas`}</dd>
      </dl>
      <p className="t-small ia-muted" style={{ margin: 0 }}>
        Passa a agir sozinha, sem pedir sua aprovação.
      </p>
      <DecideButtons card={card} h={h} />
    </Shell>
  )
}

function PolicyCard({ card, h }: { card: ChatCard; h: CardHandlers }) {
  const p = card.policy
  if (!p) return null
  return (
    <Shell label="Mudar política" card={card}>
      <dl className="kv">
        <dt>Ação</dt>
        {p.target === 'apply_labels' ? <dd>Rótulos da IA</dd> : <ActionLabel action={p.target} />}
        <dt>Quando</dt>
        <dd>{TRIGGER_LABELS[p.trigger] ?? p.trigger}</dd>
        <dt>Passa a ser</dt>
        <dd>
          <Badge tone={p.policy === 'auto' ? 'accent' : 'neutral'}>{POLICY_LABELS[p.policy] ?? p.policy}</Badge>
        </dd>
      </dl>
      <DecideButtons card={card} h={h} />
    </Shell>
  )
}

function ActionsCard({ card, h }: { card: ChatCard; h: CardHandlers }) {
  const [busy, run] = useBusy()
  const n = card.items.length
  return (
    <Shell label={n === 1 ? 'Sugestão' : `${n} sugestões`} card={card}>
      <div className="chat-card__list">
        {card.items.map((it) => (
          <div key={it.action_id} className="ia-row chat-card__item">
            <span className="chat-card__text">
              <span className="truncate">{it.title}</span>
              <span className="t-small ia-muted truncate">{it.subtitle}</span>
            </span>
            {it.status && it.status !== 'pending' ? <Badge>{it.status}</Badge> : null}
          </div>
        ))}
      </div>
      {card.comment ? <p className="t-small ia-muted" style={{ margin: 0 }}>{`Comentário: ${card.comment}`}</p> : null}
      <div className="toolbar">
        {card.status === 'proposed' && n > 0 ? (
          <>
            <button
              type="button"
              className={card.verdict === 'reject' ? 'ia-btn ia-btn--sm' : 'ia-btn ia-btn--primary ia-btn--sm'}
              disabled={busy}
              onClick={() => run(() => h.onConfirm(card.id, { verdict: 'approve' }))}
            >
              <Icon name="check" />
              {n === 1 ? 'Aprovar' : 'Aprovar todas'}
            </button>
            <button
              type="button"
              className={card.verdict === 'reject' ? 'ia-btn ia-btn--primary ia-btn--sm' : 'ia-btn ia-btn--sm'}
              disabled={busy}
              onClick={() => run(() => h.onConfirm(card.id, { verdict: 'reject' }))}
            >
              <Icon name="x" />
              {n === 1 ? 'Recusar' : 'Recusar todas'}
            </button>
          </>
        ) : null}
        <Link className="ia-btn ia-btn--ghost ia-btn--sm" to="/aprovacoes">
          Abrir em Aprovações
        </Link>
      </div>
    </Shell>
  )
}

function EmailsCard({ card }: { card: ChatCard }) {
  return (
    <section className="ia-card chat-card" aria-label="E-mails">
      {card.items.map((it) =>
        it.email_id ? (
          <Link key={it.email_id} className="ia-row chat-card__item" to={`/caixa/${encodeURIComponent(it.email_id)}`}>
            <Icon name="mail" />
            <span className="chat-card__text">
              <span className="truncate">{it.title}</span>
              <span className="t-small ia-muted truncate">{it.subtitle}</span>
            </span>
            <Icon name="chevronright" />
          </Link>
        ) : null,
      )}
    </section>
  )
}

function DocumentsCard({ card }: { card: ChatCard }) {
  const [busy, run] = useBusy()
  return (
    <>
      {card.items.map((it) =>
        it.attachment_id !== null ? (
          <div key={it.attachment_id} className="ia-card doc-card">
            <span className="doc-card__icon" style={{ color: 'var(--info)' }}>
              <Icon name="filetext" />
            </span>
            <span className="doc-card__text">
              <span className="h-heading truncate" style={{ fontSize: 14 }}>
                {it.title}
              </span>
              <span className="t-small ia-muted">{it.subtitle}</span>
            </span>
            <button
              type="button"
              className="ia-btn ia-btn--accent ia-btn--sm"
              disabled={busy}
              onClick={() => run(() => downloadDocument(it.attachment_id as number))}
            >
              <Icon name="download" />
              Baixar
            </button>
          </div>
        ) : null,
      )}
    </>
  )
}

export function CardView({ card, h }: { card: ChatCard; h: CardHandlers }) {
  switch (card.kind) {
    case 'rule':
      return <RuleCard card={card} h={h} />
    case 'preference':
      return <PreferenceCard card={card} h={h} />
    case 'promote':
      return <PromoteCard card={card} h={h} />
    case 'policy':
      return <PolicyCard card={card} h={h} />
    case 'actions':
      return <ActionsCard card={card} h={h} />
    case 'emails':
      return <EmailsCard card={card} />
    case 'documents':
      return <DocumentsCard card={card} />
  }
}
