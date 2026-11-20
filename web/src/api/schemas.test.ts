import { describe, expect, test } from 'vitest'
import * as fx from '../../test/fixtures'
import * as s from './schemas'

interface Parser {
  safeParse: (v: unknown) => { success: boolean }
}

describe('schemas', () => {
  const cases: Array<[string, Parser, unknown]> = [
    ['EmailListItem', s.EmailListItem, fx.emailItem],
    ['EmailListItem (nulls)', s.EmailListItem, fx.emailItemNulls],
    ['EmailPage', s.EmailPage, fx.emailPage],
    ['EmailDetail', s.EmailDetail, fx.emailDetail],
    ['DocumentOut', s.DocumentOut, fx.document],
    ['DocumentOut (nulls)', s.DocumentOut, fx.documentNulls],
    ['Action', s.Action, fx.action],
    ['RunDetail', s.RunDetail, fx.runDetail],
    ['BackfillStatus', s.BackfillStatus, fx.backfillStatus],
    ['BackfillStatus (nulls)', s.BackfillStatus, fx.backfillStatusNulls],
    ['JobsOut', s.JobsOut, fx.jobsOut],
    ['UsageDay', s.UsageDay, fx.usageDay],
    ['Rule', s.Rule, fx.rule],
    ['Policy', s.Policy, fx.policy],
    ['KbEntry', s.KbEntry, fx.kbEntry],
    ['ConfigsOut', s.ConfigsOut, fx.configs],
    ['Audit', s.Audit, fx.audit],
    ['Session', s.Session, fx.session],
    ['BlocksOut', s.BlocksOut, fx.blocks],
    ['SecurityEvent', s.SecurityEvent, fx.securityEvent],
    ['Notification', s.Notification, fx.notification],
    ['Taxonomy', s.Taxonomy, fx.taxonomy],
  ]

  test.each(cases)('accepts the %s fixture', (_name, schema, fixture) => {
    expect(schema.safeParse(fixture).success).toBe(true)
  })

  test('rejects an email without id', () => {
    const { id: _id, ...rest } = fx.emailItem
    expect(s.EmailListItem.safeParse(rest).success).toBe(false)
  })

  test('llm_mode accepts null, free and fast only', () => {
    expect(s.AgentSettings.safeParse({ llm_mode: null }).success).toBe(true)
    expect(s.AgentSettings.safeParse({ llm_mode: 'fast' }).success).toBe(true)
    expect(s.AgentSettings.safeParse({ llm_mode: 'turbo' }).success).toBe(false)
  })
})
