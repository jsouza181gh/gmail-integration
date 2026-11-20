import { useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { listDocuments } from '../api/endpoints'
import { ApiError } from '../api/errors'
import type { DocumentOut } from '../api/schemas'
import { downloadDocument } from '../lib/download'
import { dueLabel, formatDate, formatMoney } from '../lib/format'
import { useHotkey } from '../lib/hotkeys'
import { Badge } from '../ui/Badge'
import { Chip } from '../ui/Chip'
import { Empty } from '../ui/Empty'
import { Icon } from '../ui/Icon'
import { Page, PageHead } from '../ui/Page'
import { useToast } from '../ui/Toast'

type ClassFilter = 'todos' | 'fiscal' | 'pessoal' | 'trabalho'

const CLASSES: Array<[ClassFilter, string]> = [
  ['todos', 'Todos'],
  ['fiscal', 'Fiscal'],
  ['pessoal', 'Pessoal'],
  ['trabalho', 'Trabalho'],
]

const LIMIT = 500

const titleOf = (d: DocumentOut): string => d.title?.trim() || d.filename

function matchesText(d: DocumentOut, text: string): boolean {
  if (!text) return true
  const hay = [d.title, d.issuer, d.filename].filter(Boolean).join(' ').toLowerCase()
  return hay.includes(text.toLowerCase())
}

export default function Documentos() {
  const toast = useToast()
  const searchRef = useRef<HTMLInputElement>(null)
  const [cls, setCls] = useState<ClassFilter>('todos')
  const [text, setText] = useState('')
  const [now] = useState(() => new Date())

  const done = useQuery({ queryKey: ['documents', 'done'], queryFn: () => listDocuments({ limit: LIMIT }) })
  const skipped = useQuery({
    queryKey: ['documents', 'skipped'],
    queryFn: () => listDocuments({ status: 'skipped', limit: LIMIT }),
  })

  useHotkey('/', () => searchRef.current?.focus())

  const all = done.data ?? []
  const count = (k: ClassFilter) => all.filter((d) => k === 'todos' || d.doc_class === k).length
  const visible = all.filter((d) => (cls === 'todos' || d.doc_class === cls) && matchesText(d, text.trim()))
  const encrypted = (skipped.data ?? []).filter((d) => d.skip_reason === 'encrypted').length

  const copy = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code)
      toast.show('Linha digitável copiada')
    } catch {
      toast.show('Não foi possível copiar. Selecione e copie manualmente.')
    }
  }

  const download = async (d: DocumentOut) => {
    try {
      await downloadDocument(d.id)
    } catch (err) {
      toast.show(`Não foi possível baixar: ${err instanceof ApiError ? err.detail : 'erro inesperado'}`)
    }
  }

  return (
    <Page>
      <PageHead
        title="Documentos"
        subtitle="Anexos lidos pelo agente. O arquivo continua no Gmail e é baixado na hora."
        actions={
          <label className="ia-input" style={{ width: 300, maxWidth: '100%' }}>
            <Icon name="search" />
            <input
              ref={searchRef}
              type="search"
              aria-label="Buscar documentos"
              placeholder="Emissor, título, arquivo…"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            <span className="ia-kbd">/</span>
          </label>
        }
      />

      <div className="spread" style={{ flexWrap: 'wrap' }}>
        <div className="chips" role="group" aria-label="Classe">
          {CLASSES.map(([id, label]) => (
            <Chip key={id} pressed={cls === id} onClick={() => setCls(id)}>
              {`${label} ${count(id)}`}
            </Chip>
          ))}
        </div>
        <span className="t-small ia-subtle">Mais recentes primeiro</span>
      </div>

      {done.isError ? <Empty text="Não foi possível carregar os documentos." /> : null}
      {done.isSuccess && all.length === 0 ? <Empty text="Nenhum documento indexado ainda." /> : null}
      {done.isSuccess && all.length > 0 && visible.length === 0 ? (
        <Empty text="Nenhum documento com esse filtro." />
      ) : null}

      {visible.length > 0 ? (
        <section className="ia-card" style={{ padding: 0, overflow: 'hidden' }} aria-label="Lista de documentos">
          <div className="doc-row doc-head" aria-hidden="true">
            <span />
            <span>Documento</span>
            <span className="doc-col-issuer">Emissor</span>
            <span className="doc-col-amount" style={{ textAlign: 'right' }}>Valor</span>
            <span className="doc-col-due">Vencimento</span>
            <span />
          </div>
          <ul className="ia-list" style={{ listStyle: 'none', margin: 0, padding: 0, borderTop: '1px solid var(--line)' }}>
            {visible.map((d) => {
              const due = dueLabel(d.due_on, now)
              return (
                <li key={d.id} className="doc-row">
                  <span className="ia-ico-info doc-row__icon">
                    <Icon name="filetext" />
                  </span>
                  <span className="doc-row__main">
                    <span className="row" style={{ minWidth: 0 }}>
                      <span className="h-heading doc-row__title">{titleOf(d)}</span>
                      {d.doc_type ? <Badge>{d.doc_type}</Badge> : null}
                    </span>
                    <span className="t-small ia-subtle">
                      Recebido em <span className="ia-num">{formatDate(d.email_received_at)}</span>
                    </span>
                    <span className="t-small ia-muted doc-meta-sm">
                      {d.issuer ?? '—'} {formatMoney(d.amount_cents)}
                    </span>
                  </span>
                  <span className="t-small ia-muted doc-col-issuer doc-row__ellipsis">{d.issuer ?? '—'}</span>
                  <span className="ia-num doc-col-amount" style={{ textAlign: 'right', fontSize: 14 }}>
                    {formatMoney(d.amount_cents)}
                  </span>
                  <span className="doc-col-due row" style={{ gap: 6 }}>
                    <span className="ia-num t-small">{formatDate(d.due_on)}</span>
                    {due?.soon ? (
                      <span className="ia-ico-warning" aria-label="vence em breve" role="img">
                        <Icon name="calendar" />
                      </span>
                    ) : null}
                  </span>
                  <span className="row" style={{ gap: 6 }}>
                    {d.barcode ? (
                      <button type="button" className="ia-btn ia-btn--ghost ia-btn--sm" onClick={() => void copy(d.barcode ?? '')}>
                        Copiar código
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="ia-btn ia-btn--accent ia-btn--sm ia-btn--icon"
                      aria-label={`Baixar ${titleOf(d)}`}
                      onClick={() => void download(d)}
                    >
                      <Icon name="download" />
                    </button>
                  </span>
                </li>
              )
            })}
          </ul>
        </section>
      ) : null}

      {encrypted > 0 ? (
        <p className="warn-line">
          <span style={{ color: 'var(--ink-subtle)' }}>
            <Icon name="lock" />
          </span>
          {encrypted === 1
            ? '1 documento está protegido por senha e não pôde ser lido.'
            : `${encrypted} documentos estão protegidos por senha e não puderam ser lidos.`}
        </p>
      ) : null}
    </Page>
  )
}
