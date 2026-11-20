import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { approve, categories, emailDetail, listEmails, reclassify, reject } from '../api/endpoints'
import { ApiError } from '../api/errors'
import type { Action, DocumentOut } from '../api/schemas'
import { useDebounced } from '../lib/debounce'
import { DECISION_DELAY_MS, useDeferred } from '../lib/deferred'
import { downloadDocument } from '../lib/download'
import {
  dueLabel,
  formatDate,
  formatDateTime,
  formatMoney,
  formatWhen,
  senderName,
  subjectOf,
} from '../lib/format'
import { useHotkey } from '../lib/hotkeys'
import { actionKind } from '../lib/labels'
import { Badge } from '../ui/Badge'
import { Chip } from '../ui/Chip'
import { Empty } from '../ui/Empty'
import { Icon } from '../ui/Icon'
import { Page, PageHead } from '../ui/Page'
import { EmailRow } from '../ui/Row'
import { useToast } from '../ui/Toast'

const message = (err: unknown, fallback: string): string => (err instanceof ApiError ? err.detail : fallback)

export default function Caixa() {
  const { emailId } = useParams()
  const [category, setCategory] = useState<string | undefined>()
  const [onlyRelevant, setOnlyRelevant] = useState(false)
  const [search, setSearch] = useState('')
  const q = useDebounced(search.trim(), 300)
  const [now] = useState(() => new Date())

  const taxonomy = useQuery({ queryKey: ['categories'], queryFn: categories })
  const list = useInfiniteQuery({
    queryKey: ['emails', 'list', { category, onlyRelevant, q }],
    queryFn: ({ pageParam }) =>
      listEmails({
        category,
        relevant: onlyRelevant ? true : undefined,
        q: q || undefined,
        before: pageParam,
        limit: 50,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_before ?? undefined,
  })

  const items = list.data?.pages.flatMap((p) => p.items) ?? []
  const selectedId = emailId ?? items[0]?.id

  return (
    <Page wide>
      <div className={emailId ? 'caixa caixa--detail' : 'caixa'}>
        <div className="caixa__filters">
          <PageHead
            title={category ?? 'Caixa'}
            actions={
              <label className="ia-input" style={{ width: 300, maxWidth: '100%' }}>
                <Icon name="search" />
                <input
                  type="search"
                  aria-label="Buscar na caixa"
                  placeholder="Buscar na caixa"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </label>
            }
          />
          <div className="chips" role="group" aria-label="Categorias" style={{ marginTop: 'var(--space-6)' }}>
            <Chip pressed={category === undefined} onClick={() => setCategory(undefined)}>
              Todas
            </Chip>
            {taxonomy.data?.categories.map((c) => (
              <Chip key={c.name} pressed={category === c.name} onClick={() => setCategory(c.name)}>
                {c.name}
              </Chip>
            ))}
            <Chip pressed={onlyRelevant} onClick={() => setOnlyRelevant((v) => !v)}>
              Só relevantes
            </Chip>
          </div>
        </div>

        <div className="split" style={{ marginTop: 'var(--space-6)' }}>
          <section className="split__list ia-card" style={{ padding: 6, display: 'grid', gap: 2 }} aria-label="Lista">
            {items.map((e) => (
              <EmailRow
                key={e.id}
                to={`/caixa/${encodeURIComponent(e.id)}`}
                selected={e.id === selectedId}
                from={senderName(e.sender)}
                date={formatWhen(e.received_at, now)}
                subject={subjectOf(e)}
                snippet={e.snippet}
                meta={
                  <>
                    {e.pending_actions > 0 ? (
                      <Badge tone="accent" icon="checkcheck">
                        {e.pending_actions === 1 ? '1 sugestão' : `${e.pending_actions} sugestões`}
                      </Badge>
                    ) : null}
                    {e.attachments > 0 ? (
                      <Badge tone="info" icon="paperclip">
                        {e.attachments === 1 ? '1 anexo' : `${e.attachments} anexos`}
                      </Badge>
                    ) : null}
                  </>
                }
              />
            ))}
            {list.isSuccess && items.length === 0 ? <Empty text="Nenhum e-mail encontrado." /> : null}
            {list.isError ? <Empty text="Não foi possível carregar a caixa." /> : null}
            {list.hasNextPage ? (
              <button
                type="button"
                className="ia-btn ia-btn--ghost"
                style={{ margin: 6 }}
                disabled={list.isFetchingNextPage}
                onClick={() => void list.fetchNextPage()}
              >
                Carregar mais
              </button>
            ) : null}
          </section>

          <article className="split__detail ia-card detail" aria-label="Detalhe">
            {selectedId ? (
              <DetailPane key={selectedId} emailId={selectedId} now={now} />
            ) : (
              <p className="ia-muted" style={{ margin: 0 }}>
                Selecione um e-mail.
              </p>
            )}
          </article>
        </div>
      </div>
    </Page>
  )
}

function DetailPane({ emailId, now }: { emailId: string; now: Date }) {
  const qc = useQueryClient()
  const toast = useToast()
  const deferred = useDeferred(DECISION_DELAY_MS)
  const [target, setTarget] = useState('')
  const detail = useQuery({ queryKey: ['email', emailId], queryFn: () => emailDetail(emailId) })
  const taxonomy = useQuery({ queryKey: ['categories'], queryFn: categories })

  const recat = useMutation({
    mutationFn: (category: string) => reclassify(emailId, category),
    onSuccess: async (_data, category) => {
      toast.show(`Reclassificado como ${category}. O agente aprende com isso.`)
      setTarget('')
      await qc.invalidateQueries({ queryKey: ['emails'] })
      await qc.invalidateQueries({ queryKey: ['email', emailId] })
    },
    onError: (err) => toast.show(message(err, 'Não foi possível reclassificar.')),
  })

  const pending = detail.data?.actions.filter((a) => a.status === 'pending') ?? []
  const first = pending[0]

  const decide = (verb: 'approve' | 'reject', a: Action) => {
    const send = verb === 'approve' ? approve : reject
    deferred.schedule(verb, async () => {
      try {
        await send([a.id])
        await qc.invalidateQueries({ queryKey: ['actions'] })
        await qc.invalidateQueries({ queryKey: ['email', emailId] })
      } catch (err) {
        toast.show(message(err, 'Não foi possível registrar a decisão.'))
      }
    })
    toast.show(verb === 'approve' ? 'Sugestão aprovada. Executando no Gmail…' : 'Sugestão recusada.', {
      undo: deferred.cancel,
      ms: DECISION_DELAY_MS,
    })
  }

  const canDecide = first !== undefined && deferred.pending === null
  useHotkey('y', () => first && decide('approve', first), canDecide)
  useHotkey('n', () => first && decide('reject', first), canDecide)

  const download = async (doc: DocumentOut) => {
    try {
      await downloadDocument(doc.id)
    } catch (err) {
      toast.show(`Não foi possível baixar: ${message(err, 'erro inesperado')}`)
    }
  }

  if (detail.isPending) return <p className="ia-muted" style={{ margin: 0 }}>Carregando…</p>
  if (detail.isError || !detail.data) return <Empty text="E-mail não encontrado." />

  const { item, reason, body, attachments, runs } = detail.data
  const lastRun = runs.reduce<(typeof runs)[number] | null>((m, r) => (m === null || r.id > m.id ? r : m), null)
  const dl = dueLabel(item.deadline, now)
  const categoryOptions = (taxonomy.data?.categories ?? []).filter((c) => c.name !== item.category)

  return (
    <>
      <div className="spread" style={{ alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
          <Link to="/caixa" className="ia-btn ia-btn--ghost ia-btn--sm caixa__back" style={{ alignSelf: 'flex-start' }}>
            <Icon name="chevronleft" />
            Voltar
          </Link>
          <h2 className="h-title">{subjectOf(item)}</h2>
          <span className="t-small ia-muted">
            {senderName(item.sender)}, <span className="ia-num">{formatDateTime(item.received_at)}</span>
          </span>
        </div>
      </div>

      <div className="ia-card" style={{ background: 'var(--surface)', display: 'grid', gap: 10 }}>
        <div className="spread">
          <span className="ia-label">Classificação</span>
          {lastRun ? (
            <span className="t-small ia-subtle">
              run #{lastRun.id}, <Link to={`/execucoes?run=${lastRun.id}`}>ver passos</Link>
            </span>
          ) : null}
        </div>
        <div className="chips">
          {item.category ? <Badge>{item.category}</Badge> : null}
          {item.is_relevant ? (
            <Badge tone="accent" icon="sparkles">
              Relevante
            </Badge>
          ) : null}
          {dl ? (
            <Badge tone={dl.soon ? 'warning' : 'neutral'} icon="calendar">
              {dl.text}
            </Badge>
          ) : null}
        </div>
        {reason ? (
          <p className="t-small ia-muted" style={{ margin: 0 }}>
            Motivo: {reason}
          </p>
        ) : null}
        <div className="row" style={{ flexWrap: 'wrap' }}>
          <label className="ia-input ia-input--dark" style={{ width: 220 }}>
            <select aria-label="Nova categoria" value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">Reclassificar para…</option>
              {categoryOptions.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                </option>
              ))}
            </select>
            <Icon name="chevrondown" />
          </label>
          <button
            type="button"
            className="ia-btn ia-btn--sm"
            disabled={!target || recat.isPending}
            onClick={() => recat.mutate(target)}
          >
            <Icon name="tag" />
            Reclassificar
          </button>
        </div>
      </div>

      {body ? (
        <p className="t-body" style={{ margin: 0, whiteSpace: 'pre-line' }}>
          {body}
        </p>
      ) : (
        <p className="t-small ia-muted" style={{ margin: 0 }}>
          Sem texto indexado para este e-mail.
        </p>
      )}

      {attachments.map((doc) => (
        <div key={doc.id} className="ia-card doc-card">
          <span className="ia-ico-info doc-card__icon">
            <Icon name="filetext" />
          </span>
          <span className="doc-card__text">
            <span className="h-heading" style={{ fontSize: 14 }}>
              {doc.title ?? doc.filename}
            </span>
            <span className="t-small ia-muted">{docMeta(doc)}</span>
          </span>
          <button type="button" className="ia-btn ia-btn--accent ia-btn--sm" onClick={() => void download(doc)}>
            <Icon name="download" />
            Baixar
          </button>
        </div>
      ))}

      {pending.length > 0 ? (
        <div className="ia-card" style={{ display: 'grid', gap: 12 }}>
          <span className="ia-label">Sugestão do agente</span>
          {pending.map((a, i) => {
            const info = actionKind(a.kind)
            return (
              <div key={a.id} className="spread" style={{ flexWrap: 'wrap' }}>
                <span className="row t-body">
                  <span className={info.danger ? 'ia-ico-danger' : undefined}>
                    <Icon name={info.icon} />
                  </span>
                  {info.label}
                  <span className="t-small ia-muted">— {a.reason}</span>
                </span>
                <span className="toolbar">
                  <button
                    type="button"
                    className="ia-btn ia-btn--primary ia-btn--sm"
                    disabled={deferred.pending !== null}
                    onClick={() => decide('approve', a)}
                  >
                    <Icon name="check" />
                    Aprovar{i === 0 ? <span className="ia-kbd">Y</span> : null}
                  </button>
                  <button
                    type="button"
                    className="ia-btn ia-btn--sm"
                    disabled={deferred.pending !== null}
                    onClick={() => decide('reject', a)}
                  >
                    <Icon name="x" />
                    Recusar{i === 0 ? <span className="ia-kbd">N</span> : null}
                  </button>
                </span>
              </div>
            )
          })}
        </div>
      ) : null}
    </>
  )
}

function docMeta(doc: DocumentOut): string {
  const parts = [
    doc.doc_type,
    doc.amount_cents === null ? null : formatMoney(doc.amount_cents),
    doc.due_on ? `vence ${formatDate(doc.due_on)}` : null,
    `${Math.max(1, Math.round(doc.size / 1024))} KB`,
  ]
  return parts.filter((p): p is string => Boolean(p)).join(', ')
}
