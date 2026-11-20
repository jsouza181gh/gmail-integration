import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import * as api from '../../api/endpoints'
import { ApiError } from '../../api/errors'
import type { Rule } from '../../api/schemas'
import { actionKind } from '../../lib/labels'
import { Badge } from '../../ui/Badge'
import { Empty } from '../../ui/Empty'
import { Icon } from '../../ui/Icon'
import { useToast } from '../../ui/Toast'

const ACTIONS = ['mark_read', 'star', 'archive', 'trash', 'block_sender']

function condition(r: Rule): string {
  const parts = [
    r.sender_address && `remetente ${r.sender_address}`,
    r.sender_domain && `domínio ${r.sender_domain}`,
    r.category && `categoria ${r.category}`,
    r.subject_contains && `assunto contém “${r.subject_contains}”`,
  ].filter((p): p is string => Boolean(p))
  const text = parts.join(' e ') || '—'
  return r.unless_document ? `${text}, exceto com documento` : text
}

export function Regras() {
  const qc = useQueryClient()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const rules = useQuery({ queryKey: ['rules'], queryFn: api.rules })

  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: ['rules'] })
    await qc.invalidateQueries({ queryKey: ['audit'] })
  }
  const promote = useMutation({
    mutationFn: api.promoteRule,
    onSuccess: async () => {
      toast.show('Regra agora é automática.')
      await refresh()
    },
    onError: (err) => toast.show(err instanceof ApiError ? err.detail : 'Não foi possível promover a regra.'),
  })
  const disable = useMutation({
    mutationFn: api.disableRule,
    onSuccess: async () => {
      toast.show('Regra desativada.')
      await refresh()
    },
    onError: (err) => toast.show(err instanceof ApiError ? err.detail : 'Não foi possível desativar a regra.'),
  })

  return (
    <section className="ia-card" style={{ display: 'grid', gap: 12 }} aria-label="Regras">
      <div className="spread">
        <h2 className="h-heading">Regras de automação</h2>
        <button type="button" className="ia-btn ia-btn--sm" onClick={() => setOpen((v) => !v)}>
          <Icon name="plus" />
          Nova regra
        </button>
      </div>

      {open ? (
        <NewRuleForm
          onDone={async () => {
            setOpen(false)
            toast.show('Regra criada.')
            await refresh()
          }}
        />
      ) : null}

      {rules.isSuccess && rules.data.length === 0 ? (
        <Empty text="Nenhuma regra ainda. As aprovações repetidas viram regras." />
      ) : null}
      {rules.isError ? <Empty text="Não foi possível carregar as regras." /> : null}

      {rules.data && rules.data.length > 0 ? (
        <div className="tablebox">
          <table className="table">
            <thead>
              <tr>
                <th>Regra</th>
                <th>Condição</th>
                <th>Ação</th>
                <th>Modo</th>
                <th>Disparos</th>
                <th>Aprov. / Recus.</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rules.data.map((r) => {
                const info = actionKind(r.action)
                return (
                  <tr key={r.id}>
                    <td>{r.name}</td>
                    <td className="ia-muted">{condition(r)}</td>
                    <td>
                      <Badge tone={info.danger ? 'danger' : 'neutral'}>{info.label}</Badge>
                    </td>
                    <td>
                      {r.active ? (
                        <Badge tone={r.mode === 'auto' ? 'accent' : 'neutral'}>
                          {r.mode === 'auto' ? 'Automática' : 'Sugerir'}
                        </Badge>
                      ) : (
                        <Badge>Inativa</Badge>
                      )}
                    </td>
                    <td className="ia-num">{r.fired_count}</td>
                    <td className="ia-num">{`${r.approved_count} / ${r.rejected_count}`}</td>
                    <td>
                      {r.active && r.mode === 'suggest' && r.action !== 'block_sender' ? (
                        <button
                          type="button"
                          className="ia-btn ia-btn--sm"
                          disabled={promote.isPending}
                          onClick={() => promote.mutate(r.id)}
                        >
                          <Icon name="zap" />
                          Tornar automática
                        </button>
                      ) : null}
                      {r.active && r.mode === 'auto' ? (
                        <button
                          type="button"
                          className="ia-btn ia-btn--ghost ia-btn--sm"
                          disabled={disable.isPending}
                          onClick={() => disable.mutate(r.id)}
                        >
                          Desativar
                        </button>
                      ) : null}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="t-small ia-subtle" style={{ margin: 0 }}>
        Bloquear remetente, enviar e filtros nunca ficam automáticos.
      </p>
    </section>
  )
}

function NewRuleForm({ onDone }: { onDone: () => Promise<void> }) {
  const taxonomy = useQuery({ queryKey: ['categories'], queryFn: api.categories })
  const [fields, setFields] = useState({
    name: '',
    action: '',
    sender_address: '',
    sender_domain: '',
    category: '',
    subject_contains: '',
  })
  const [unlessDocument, setUnlessDocument] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = (key: keyof typeof fields) => (value: string) => setFields((f) => ({ ...f, [key]: value }))

  const create = useMutation({
    mutationFn: () => {
      const rule: api.NewRule = { name: fields.name.trim(), action: fields.action }
      if (fields.sender_address.trim()) rule.sender_address = fields.sender_address.trim()
      if (fields.sender_domain.trim()) rule.sender_domain = fields.sender_domain.trim()
      if (fields.category) rule.category = fields.category
      if (fields.subject_contains.trim()) rule.subject_contains = fields.subject_contains.trim()
      if (unlessDocument) rule.unless_document = true
      return api.addRule(rule)
    },
    onSuccess: onDone,
    onError: (err) => setError(err instanceof ApiError ? err.detail : 'Não foi possível criar a regra.'),
  })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    create.mutate()
  }

  return (
    <form className="ia-card rule-form" onSubmit={submit} style={{ background: 'var(--surface)' }}>
      <Field label="Nome">
        <input value={fields.name} onChange={(e) => set('name')(e.target.value)} />
      </Field>
      <Field label="Ação">
        <select value={fields.action} onChange={(e) => set('action')(e.target.value)}>
          <option value="">Escolha…</option>
          {ACTIONS.map((a) => (
            <option key={a} value={a}>
              {actionKind(a).label}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Remetente">
        <input
          value={fields.sender_address}
          onChange={(e) => set('sender_address')(e.target.value)}
          placeholder="pessoa@exemplo.com"
        />
      </Field>
      <Field label="Domínio">
        <input
          value={fields.sender_domain}
          onChange={(e) => set('sender_domain')(e.target.value)}
          placeholder="exemplo.com.br"
        />
      </Field>
      <Field label="Categoria">
        <select value={fields.category} onChange={(e) => set('category')(e.target.value)}>
          <option value="">Qualquer</option>
          {taxonomy.data?.categories.map((c) => (
            <option key={c.name} value={c.name}>
              {c.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Assunto contém">
        <input value={fields.subject_contains} onChange={(e) => set('subject_contains')(e.target.value)} />
      </Field>
      <label className="row t-small" style={{ gridColumn: '1 / -1' }}>
        <input type="checkbox" checked={unlessDocument} onChange={(e) => setUnlessDocument(e.target.checked)} />
        Exceto quando tiver documento (nota, boleto…)
      </label>
      {error ? (
        <p className="warn-line" role="alert" style={{ gridColumn: '1 / -1' }}>
          <span className="ia-ico-warning">
            <Icon name="alert" />
          </span>
          {error}
        </p>
      ) : null}
      <div style={{ gridColumn: '1 / -1' }}>
        <button
          type="submit"
          className="ia-btn ia-btn--primary ia-btn--sm"
          disabled={!fields.name.trim() || !fields.action || create.isPending}
        >
          Criar regra
        </button>
      </div>
    </form>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="ia-field">
      <span className="ia-label">{label}</span>
      <span className="ia-input ia-input--dark">{children}</span>
    </label>
  )
}
