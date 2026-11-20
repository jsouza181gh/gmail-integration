import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from '../../api/endpoints'
import { ApiError } from '../../api/errors'
import { actionKind, POLICY_LABELS, TRIGGER_LABELS } from '../../lib/labels'
import { Badge } from '../../ui/Badge'
import { Empty } from '../../ui/Empty'
import { Icon } from '../../ui/Icon'
import { useToast } from '../../ui/Toast'

const TARGETS = ['apply_labels', 'mark_read', 'star', 'archive', 'trash', 'block_sender']
const TRIGGERS = ['webhook', 'chat']
// Mirrors NEVER_AUTO in src/agent/types.py: the server refuses auto for these.
const ALWAYS_SUGGEST = new Set(['block_sender'])

const targetLabel = (t: string): string => (t === 'apply_labels' ? 'Aplicar labels IA/*' : actionKind(t).label)

export function Politicas() {
  const qc = useQueryClient()
  const toast = useToast()
  const policies = useQuery({ queryKey: ['policies'], queryFn: api.policies })

  const change = useMutation({
    mutationFn: (v: { target: string; trigger: string; policy: string }) => api.setPolicy(v.target, v.trigger, v.policy),
    onSuccess: async () => {
      toast.show('Política atualizada.')
      await qc.invalidateQueries({ queryKey: ['policies'] })
      await qc.invalidateQueries({ queryKey: ['audit'] })
    },
    onError: async (err) => {
      toast.show(err instanceof ApiError ? err.detail : 'Não foi possível atualizar a política.')
      await qc.invalidateQueries({ queryKey: ['policies'] })
    },
  })

  const current = (target: string, trigger: string): string =>
    policies.data?.find((p) => p.target === target && p.trigger === trigger)?.policy ?? 'suggest'

  return (
    <section className="ia-card" style={{ display: 'grid', gap: 12 }} aria-label="Políticas">
      <h2 className="h-heading">Políticas por ação</h2>
      {policies.isError ? <Empty text="Não foi possível carregar as políticas." /> : null}
      <div className="tablebox">
        <table className="table">
          <thead>
            <tr>
              <th>Ação</th>
              {TRIGGERS.map((t) => (
                <th key={t}>{TRIGGER_LABELS[t]}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {TARGETS.map((target) => (
              <tr key={target}>
                <td>{targetLabel(target)}</td>
                {TRIGGERS.map((trigger) => (
                  <td key={trigger}>
                    {ALWAYS_SUGGEST.has(target) ? (
                      <Badge icon="alert">Sempre sugerir</Badge>
                    ) : (
                      <label className="ia-input ia-input--dark policy-select">
                        <select
                          aria-label={`${targetLabel(target)}, ${TRIGGER_LABELS[trigger]}`}
                          value={current(target, trigger)}
                          disabled={change.isPending}
                          onChange={(e) => change.mutate({ target, trigger, policy: e.target.value })}
                        >
                          {Object.entries(POLICY_LABELS).map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                        <Icon name="chevrondown" />
                      </label>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="t-small ia-subtle" style={{ margin: 0 }}>
        Marcar como lido, estrela, arquivar e lixeira só viram automáticos quando você decidir.
      </p>
    </section>
  )
}
