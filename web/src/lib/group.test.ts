import { describe, expect, test } from 'vitest'
import { action, rule } from '../../test/fixtures'
import type { Action } from '../api/schemas'
import { groupActions } from './group'

const a = (over: Partial<Action>): Action => ({ ...action, ...over })

const items: Action[] = [
  a({ id: 1, rule_id: 5, kind: 'trash', sender: 'Loja <x@loja.com>' }),
  a({ id: 2, rule_id: 5, kind: 'trash', sender: 'Loja <x@loja.com>', has_important_attachment: true }),
  a({ id: 3, rule_id: null, kind: 'archive', sender: 'Catho <v@catho.com>' }),
  a({ id: 4, rule_id: null, kind: 'archive', sender: 'Indeed <v@indeed.com>' }),
  a({ id: 5, rule_id: null, kind: 'block_sender', sender: 'premios@exemplo.net' }),
]

describe('groupActions', () => {
  test('by_rule_names_rule_and_agent_groups', () => {
    const groups = groupActions(items, 'rule', [rule])
    expect(groups.map((g) => [g.title, g.items.length])).toEqual([
      ['lojas → lixeira', 2],
      ['Agente: Arquivar', 2],
      ['Agente: Bloquear', 1],
    ])
    expect(groups[0]).toMatchObject({
      kind: 'trash',
      ruleStats: { approved: 23, rejected: 1 },
      hasImportantAttachment: true,
    })
    expect(groups[1]?.ruleStats).toBeNull()
    expect(groups[1]?.hasImportantAttachment).toBe(false)
  })

  test('by_sender_groups_per_address', () => {
    const groups = groupActions(items, 'sender', [rule])
    expect(groups.map((g) => [g.title, g.items.length])[0]).toEqual(['Loja', 2])
    expect(groups).toHaveLength(4)
  })

  test('by_kind_groups_per_action', () => {
    const groups = groupActions(items, 'kind', [rule])
    expect(groups.map((g) => [g.title, g.items.length])).toEqual([
      ['Lixeira', 2],
      ['Arquivar', 2],
      ['Bloquear', 1],
    ])
  })

  test('rule_id_without_known_rule_falls_back_to_agent_title', () => {
    const groups = groupActions([a({ id: 9, rule_id: 99, kind: 'trash' })], 'rule', [])
    expect(groups[0]?.title).toBe('Agente: Lixeira')
  })

  test('unknown_action_kind_uses_raw_kind_as_label', () => {
    const groups = groupActions([a({ id: 9, rule_id: null, kind: 'novo' })], 'kind', [])
    expect(groups[0]?.title).toBe('novo')
  })

  test('empty_list_has_no_groups', () => expect(groupActions([], 'rule', [])).toEqual([]))
})
