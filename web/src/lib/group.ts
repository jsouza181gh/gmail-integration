import type { Action, Rule } from '../api/schemas'
import { senderAddress, senderName } from './format'
import { actionKind } from './labels'

export type GroupBy = 'rule' | 'sender' | 'kind'

export interface ActionGroup {
  key: string
  title: string
  kind: string | null
  items: Action[]
  ruleStats: { approved: number; rejected: number } | null
  hasImportantAttachment: boolean
}

function keyAndTitle(a: Action, by: GroupBy, rules: Rule[]): { key: string; title: string; rule: Rule | null } {
  const kindLabel = actionKind(a.kind).label
  if (by === 'kind') return { key: `kind:${a.kind}`, title: kindLabel, rule: null }
  if (by === 'sender') {
    return { key: `sender:${senderAddress(a.sender) ?? ''}`, title: senderName(a.sender), rule: null }
  }
  const rule = a.rule_id === null ? null : (rules.find((r) => r.id === a.rule_id) ?? null)
  if (rule) return { key: `rule:${rule.id}`, title: rule.name, rule }
  return { key: `agent:${a.kind}`, title: `Agente: ${kindLabel}`, rule: null }
}

export function groupActions(actions: Action[], by: GroupBy, rules: Rule[]): ActionGroup[] {
  const groups = new Map<string, ActionGroup>()
  for (const a of actions) {
    const { key, title, rule } = keyAndTitle(a, by, rules)
    const group = groups.get(key) ?? {
      key,
      title,
      kind: a.kind,
      items: [],
      ruleStats: rule ? { approved: rule.approved_count, rejected: rule.rejected_count } : null,
      hasImportantAttachment: false,
    }
    group.items.push(a)
    if (group.kind !== a.kind) group.kind = null
    if (a.has_important_attachment) group.hasImportantAttachment = true
    groups.set(key, group)
  }
  return [...groups.values()].sort((x, y) => y.items.length - x.items.length)
}
