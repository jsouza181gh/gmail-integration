import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'
import { relevantEmails } from '../api/endpoints'
import { useBackfill, usePendingCount } from '../api/hooks'
import type { EmailListItem } from '../api/schemas'
import { dueLabel, formatPercent, formatWhen, longDate, senderName, subjectOf } from '../lib/format'
import { Badge } from '../ui/Badge'
import { Chip } from '../ui/Chip'
import { Empty } from '../ui/Empty'
import { EmailRow } from '../ui/Row'
import { Icon } from '../ui/Icon'
import { Page, PageHead } from '../ui/Page'
import { Progress } from '../ui/Progress'

type Filter = 'todos' | 'prazo' | 'doc'

const matches = (e: EmailListItem, f: Filter): boolean =>
  f === 'todos' || (f === 'prazo' ? e.deadline !== null : e.has_document === true)

function title(n: number, loading: boolean): string {
  if (loading) return 'Relevantes'
  if (n === 0) return 'Nada pede atenção agora'
  return n === 1 ? '1 coisa pede atenção' : `${n} coisas pedem atenção`
}

function subtitle(due: number, pending: number): string | undefined {
  const parts: string[] = []
  if (due > 0) parts.push(`${due} com prazo próximo`)
  if (pending > 0) parts.push(`${pending} ${pending === 1 ? 'sugestão' : 'sugestões'} do agente esperando você`)
  return parts.length ? `${parts.join(' e ')}.` : undefined
}

export default function Relevantes() {
  const [filter, setFilter] = useState<Filter>('todos')
  const emails = useQuery({ queryKey: ['emails', 'relevant'], queryFn: relevantEmails })
  const backfill = useBackfill()
  const pending = usePendingCount()
  const [now] = useState(() => new Date())

  const items = emails.data ?? []
  const due = items.filter((e) => e.due_soon).length
  const visible = items.filter((e) => matches(e, filter))
  const percent = backfill.data?.percent ?? null

  return (
    <Page>
      <PageHead title={title(items.length, emails.isPending)} kicker={longDate(now)} subtitle={subtitle(due, pending)} />

      <nav className="summary" aria-label="Resumo">
        <Link className="summary__item" to="/aprovacoes">
          <span className="ia-ico-accent">
            <Icon name="checkcheck" size={18} />
          </span>
          <span className="summary__text">
            {pending > 0 ? (
              <>
                <b className="ia-num">{pending}</b> {pending === 1 ? 'sugestão' : 'sugestões'} para aprovar
              </>
            ) : (
              'Nenhuma sugestão para aprovar'
            )}
          </span>
          <Icon name="chevronright" />
        </Link>
        <Link className="summary__item" to="/caixa">
          <span className="ia-ico-warning">
            <Icon name="calendar" size={18} />
          </span>
          <span className="summary__text">
            {due > 0 ? (
              <>
                <b className="ia-num">{due}</b> {due === 1 ? 'prazo próximo' : 'prazos próximos'}
              </>
            ) : (
              'Nenhum prazo próximo'
            )}
          </span>
          <Icon name="chevronright" />
        </Link>
        {backfill.data ? (
          <Link className="summary__item" to="/execucoes">
            <span style={{ color: 'var(--ink-subtle)' }}>
              <Icon name="activity" size={18} />
            </span>
            <span className="summary__text">
              {percent === null ? (
                'Histórico sendo indexado'
              ) : (
                <>
                  Histórico <b className="ia-num">{formatPercent(percent)}</b> indexado
                </>
              )}
            </span>
            <span style={{ width: 64, flex: 'none' }}>
              <Progress value={percent} />
            </span>
          </Link>
        ) : null}
      </nav>

      {items.length > 0 ? (
        <div className="spread" style={{ flexWrap: 'wrap' }}>
          <div className="chips" role="group" aria-label="Filtro">
            <Chip pressed={filter === 'todos'} onClick={() => setFilter('todos')}>{`Todos, ${items.length}`}</Chip>
            <Chip pressed={filter === 'prazo'} onClick={() => setFilter('prazo')}>Com prazo</Chip>
            <Chip pressed={filter === 'doc'} onClick={() => setFilter('doc')}>Com documento</Chip>
          </div>
          <span className="t-small ia-subtle">Ordenado por prazo</span>
        </div>
      ) : null}

      {emails.isError ? (
        <Empty text="Não foi possível carregar os e-mails relevantes." />
      ) : items.length === 0 && emails.isSuccess ? (
        <Empty text="Nenhum e-mail relevante no momento." />
      ) : (
        <section
          className="ia-card"
          style={{ padding: 6, display: 'flex', flexDirection: 'column', gap: 2 }}
          aria-label="E-mails relevantes"
        >
          {visible.map((e) => {
            const dl = dueLabel(e.deadline, now)
            return (
              <EmailRow
                key={e.id}
                to={`/caixa/${encodeURIComponent(e.id)}`}
                from={senderName(e.sender)}
                date={formatWhen(e.received_at, now)}
                subject={subjectOf(e)}
                snippet={e.snippet}
                meta={
                  <>
                    {e.category ? <Badge>{e.category}</Badge> : null}
                    {dl ? (
                      <Badge tone={dl.soon ? 'warning' : 'neutral'} icon="calendar">
                        {dl.text}
                      </Badge>
                    ) : null}
                    {e.has_document ? (
                      <Badge tone="info" icon="filetext">
                        Documento
                      </Badge>
                    ) : null}
                  </>
                }
              />
            )
          })}
          {visible.length === 0 && items.length > 0 ? (
            <p className="empty" style={{ margin: 0 }}>
              Nada aqui com esse filtro.
            </p>
          ) : null}
        </section>
      )}
    </Page>
  )
}
