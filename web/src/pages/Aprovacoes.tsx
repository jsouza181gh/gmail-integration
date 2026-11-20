import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'
import { approve, reject, rules } from '../api/endpoints'
import { ApiError } from '../api/errors'
import { usePendingActions } from '../api/hooks'
import type { Action } from '../api/schemas'
import { DECISION_DELAY_MS, useDeferred } from '../lib/deferred'
import { formatWhen, senderName, subjectOf } from '../lib/format'
import { groupActions, type ActionGroup, type GroupBy } from '../lib/group'
import { useHotkey } from '../lib/hotkeys'
import { actionKind } from '../lib/labels'
import { Badge } from '../ui/Badge'
import { Check } from '../ui/Check'
import { Chip } from '../ui/Chip'
import { Empty } from '../ui/Empty'
import { Icon } from '../ui/Icon'
import { Page, PageHead } from '../ui/Page'
import { useToast } from '../ui/Toast'

type Verb = 'approve' | 'reject'

const COMMENT_MAX = 500

const plural = (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`

function headline(n: number, loading: boolean): string {
  if (loading) return 'Aprovações'
  if (n === 0) return 'Nenhuma sugestão esperando você'
  return `${plural(n, 'sugestão', 'sugestões')} esperando você`
}

function discussDraft(a: Action): string {
  return `Sobre a sugestão #${a.id} (${actionKind(a.kind).label.toLowerCase()} em ${senderName(a.sender)}): `
}

function decisionError(err: unknown): string {
  if (err instanceof ApiError && err.status === 403) return 'Requisição recusada. Recarregue a página.'
  return err instanceof ApiError ? err.detail : 'Não foi possível registrar a decisão.'
}

function subtitle(g: ActionGroup, by: GroupBy): string {
  const n = plural(g.items.length, 'sugestão', 'sugestões')
  let text: string
  if (g.ruleStats) {
    text = `regra, ${n}, ${g.ruleStats.approved} aprovadas, ${g.ruleStats.rejected} recusadas até hoje`
  } else if (by === 'rule') {
    text = `agente, ${n}`
  } else {
    text = n
  }
  return g.kind === 'block_sender' ? `${text}, nunca roda sozinha` : text
}

export default function Aprovacoes() {
  const qc = useQueryClient()
  const toast = useToast()
  const deferred = useDeferred(DECISION_DELAY_MS)
  const actions = usePendingActions()
  const ruleList = useQuery({ queryKey: ['rules'], queryFn: rules })
  const [by, setBy] = useState<GroupBy>('rule')
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set())
  const [comment, setComment] = useState('')
  const [now] = useState(() => new Date())

  const items = actions.data ?? []
  const groups = groupActions(items, by, ruleList.data ?? [])
  const chosen = items.filter((a) => selected.has(a.id)).map((a) => a.id)

  const toggle = (ids: number[], on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev)
      for (const id of ids) {
        if (on) next.add(id)
        else next.delete(id)
      }
      return next
    })

  const decide = (verb: Verb, ids: number[]) => {
    if (ids.length === 0) return
    const send = verb === 'approve' ? approve : reject
    const text = comment.trim() || undefined
    const done = verb === 'approve' ? 'aprovadas' : 'recusadas'
    deferred.schedule(verb, async () => {
      try {
        const { decided } = await send(ids, text)
        if (decided < ids.length) {
          toast.show(`${decided} de ${ids.length} sugestões ${done}. As outras já tinham sido decididas.`)
        }
        toggle(ids, false)
        setComment('')
        await qc.invalidateQueries({ queryKey: ['actions'] })
      } catch (err) {
        toast.show(decisionError(err))
      }
    })
    toast.show(
      verb === 'approve'
        ? `${plural(ids.length, 'sugestão aprovada', 'sugestões aprovadas')}. Executando no Gmail…`
        : `${plural(ids.length, 'sugestão recusada', 'sugestões recusadas')}.`,
      { undo: deferred.cancel, ms: DECISION_DELAY_MS },
    )
  }

  const canAct = chosen.length > 0 && deferred.pending === null
  useHotkey('y', () => decide('approve', chosen), canAct)
  useHotkey('n', () => decide('reject', chosen), canAct)

  return (
    <Page>
      <PageHead
        title={headline(items.length, actions.isPending)}
        subtitle="Nada que esconda e-mail roda sem a sua aprovação. Recusar com um comentário ensina o agente."
        actions={
          <div className="toolbar" role="group" aria-label="Agrupar por">
            <Chip pressed={by === 'rule'} onClick={() => setBy('rule')}>Por regra</Chip>
            <Chip pressed={by === 'sender'} onClick={() => setBy('sender')}>Por remetente</Chip>
            <Chip pressed={by === 'kind'} onClick={() => setBy('kind')}>Por ação</Chip>
          </div>
        }
      />

      {actions.isSuccess && items.length === 0 ? (
        <Empty
          text="Tudo decidido por aqui."
          action={
            <Link className="ia-btn" to="/caixa">
              Ir para a Caixa
            </Link>
          }
        />
      ) : null}
      {actions.isError ? <Empty text="Não foi possível carregar as sugestões." /> : null}

      {groups.map((g) => (
        <GroupCard
          key={g.key}
          group={g}
          by={by}
          selected={selected}
          now={now}
          busy={deferred.pending !== null}
          onToggle={toggle}
          onDecide={decide}
        />
      ))}

      <div className="ia-card batch-bar" role="region" aria-label="Decisão em lote">
        <span className="h-heading" style={{ flex: '1 1 200px' }}>
          {`${chosen.length} ${chosen.length === 1 ? 'selecionada' : 'selecionadas'}`}
        </span>
        <label className="ia-input ia-input--dark" style={{ flex: '2 1 280px' }}>
          <Icon name="message" />
          <input
            aria-label="Comentário"
            placeholder="Comentário para o agente (opcional) — vira conhecimento"
            maxLength={COMMENT_MAX}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
        </label>
        <button type="button" className="ia-btn" disabled={!canAct} onClick={() => decide('reject', chosen)}>
          <Icon name="x" />
          Recusar<span className="ia-kbd">N</span>
        </button>
        <button
          type="button"
          className="ia-btn ia-btn--primary"
          disabled={!canAct}
          onClick={() => decide('approve', chosen)}
        >
          <Icon name="check" />
          Aprovar {chosen.length}
          <span className="ia-kbd">Y</span>
        </button>
      </div>
    </Page>
  )
}

function GroupCard({
  group: g,
  by,
  selected,
  now,
  busy,
  onToggle,
  onDecide,
}: {
  group: ActionGroup
  by: GroupBy
  selected: ReadonlySet<number>
  now: Date
  busy: boolean
  onToggle: (ids: number[], on: boolean) => void
  onDecide: (verb: Verb, ids: number[]) => void
}) {
  const ids = g.items.map((a) => a.id)
  const allOn = ids.every((id) => selected.has(id))
  const info = g.kind ? actionKind(g.kind) : null
  const important = g.items.filter((a: Action) => a.has_important_attachment).length

  return (
    <section className="ia-card group-card" aria-label={g.title}>
      <div className="spread group-card__head">
        <Check checked={allOn} onChange={() => onToggle(ids, !allOn)}>
          <span className="group-card__title">
            <span className="row" style={{ flexWrap: 'wrap' }}>
              <span className="h-heading">{g.title}</span>
              {info ? (
                <Badge tone={info.danger ? 'danger' : 'neutral'} icon={info.icon}>
                  {info.label}
                </Badge>
              ) : null}
            </span>
            <span className="t-small ia-muted">{subtitle(g, by)}</span>
          </span>
        </Check>
        <span className="toolbar">
          <button type="button" className="ia-btn ia-btn--primary ia-btn--sm" disabled={busy} onClick={() => onDecide('approve', ids)}>
            <Icon name="check" />
            Aprovar {ids.length}
          </button>
          <button type="button" className="ia-btn ia-btn--sm" disabled={busy} onClick={() => onDecide('reject', ids)}>
            <Icon name="x" />
            Recusar
          </button>
        </span>
      </div>
      {important > 0 ? (
        <p className="warn-line group-card__warn">
          <span className="ia-ico-warning">
            <Icon name="alert" />
          </span>
          {important === 1
            ? '1 destes e-mails tem um documento importante indexado. Confira antes de aprovar.'
            : `${important} destes e-mails têm um documento importante indexado. Confira antes de aprovar.`}
        </p>
      ) : null}
      <div style={{ padding: 6, display: 'flex', flexDirection: 'column', gap: 2 }}>
        {g.items.map((a) => (
          <div key={a.id} className="ia-row group-card__item">
            <Check checked={selected.has(a.id)} onChange={() => onToggle([a.id], !selected.has(a.id))} label={`Selecionar ${subjectOf(a)}`} />
            <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
              <span className="ia-row__subject">{subjectOf(a)}</span>
              <span className="ia-row__from">
                {senderName(a.sender)}, {a.reason}
              </span>
            </span>
            <span className="ia-num ia-subtle">{formatWhen(a.created_at, now)}</span>
            <Link
              className="ia-btn ia-btn--ghost ia-btn--sm ia-btn--icon"
              to={`/chat?draft=${encodeURIComponent(discussDraft(a))}`}
              aria-label={`Discutir no chat: ${subjectOf(a)}`}
              title="Discutir no chat"
            >
              <Icon name="message" />
            </Link>
          </div>
        ))}
      </div>
    </section>
  )
}
