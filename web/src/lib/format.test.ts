import { describe, expect, test } from 'vitest'
import {
  dueLabel,
  formatDate,
  formatDateTime,
  formatDuration,
  formatEta,
  formatInt,
  formatMoney,
  formatTime,
  formatPercent,
  formatWhen,
  longDate,
  senderAddress,
  senderName,
  subjectOf,
} from './format'

// Thursday, local time.
const now = new Date(2026, 10, 19, 12, 0, 0)

describe('formatMoney', () => {
  test('null_is_dash', () => expect(formatMoney(null)).toBe('—'))
  test('cents_to_brl', () => expect(formatMoney(18990)).toBe('R$ 189,90'))
  test('zero_is_not_dash', () => expect(formatMoney(0)).toBe('R$ 0,00'))
  test('thousands_separator', () => expect(formatMoney(123456)).toBe('R$ 1.234,56'))
})

describe('formatDate', () => {
  test('plain_date_is_not_shifted_by_timezone', () => expect(formatDate('2026-11-28')).toBe('28/11/2026'))
  test('null_and_garbage_are_dash', () => {
    expect(formatDate(null)).toBe('—')
    expect(formatDate('nonsense')).toBe('—')
  })
})

describe('formatWhen', () => {
  const at = (d: number, h = 10, m = 42) => new Date(2026, 10, d, h, m).toISOString()
  test('today_shows_time', () => expect(formatWhen(at(19), now)).toBe('10:42'))
  test('yesterday', () => expect(formatWhen(at(18), now)).toBe('ontem'))
  test('last_week_shows_weekday', () => expect(formatWhen(at(16), now)).toBe('seg'))
  test('older_shows_day_month', () => expect(formatWhen(at(5), now)).toBe('05/11'))
  test('null_is_dash', () => expect(formatWhen(null, now)).toBe('—'))
})

describe('dueLabel', () => {
  test('null_has_no_label', () => expect(dueLabel(null, now)).toBeNull())
  test('overdue', () => expect(dueLabel('2026-11-16', now)).toEqual({ text: 'Venceu há 3 dias', soon: true }))
  test('today', () => expect(dueLabel('2026-11-19', now)).toEqual({ text: 'Vence hoje', soon: true }))
  test('tomorrow', () => expect(dueLabel('2026-11-20', now)).toEqual({ text: 'Vence amanhã', soon: true }))
  test('in_two_days', () => expect(dueLabel('2026-11-21', now)).toEqual({ text: 'Vence em 2 dias', soon: true }))
  test('far_is_a_date_and_not_soon', () => expect(dueLabel('2026-11-29', now)).toEqual({ text: 'Vence 29/11', soon: false }))
})

describe('sender and subject', () => {
  test('empty_subject_has_placeholder', () => {
    expect(subjectOf({ subject: '' })).toBe('(sem assunto)')
    expect(subjectOf({ subject: null })).toBe('(sem assunto)')
    expect(subjectOf({ subject: 'Oi' })).toBe('Oi')
  })
  test('sender_name_prefers_display_name', () => {
    expect(senderName('Light <no-reply@light.com.br>')).toBe('Light')
    expect(senderName('"Light S.A." <a@b.com>')).toBe('Light S.A.')
    expect(senderName('a@b.com')).toBe('a@b.com')
    expect(senderName(null)).toBe('(sem remetente)')
  })
  test('sender_address', () => {
    expect(senderAddress('Light <No-Reply@Light.com.br>')).toBe('no-reply@light.com.br')
    expect(senderAddress(null)).toBeNull()
  })
})

describe('formatInt, formatDuration and formatTime', () => {
  test('int_uses_dot_thousands', () => expect(formatInt(45038)).toBe('45.038'))
  test('duration_in_ms_or_seconds', () => {
    expect(formatDuration(310)).toBe('310 ms')
    expect(formatDuration(1900)).toBe('1,9 s')
    expect(formatDuration(2000)).toBe('2,0 s')
  })
  test('time_is_local_hh_mm', () => {
    expect(formatTime(new Date(2026, 10, 19, 21, 5).toISOString())).toBe('21:05')
    expect(formatTime(null)).toBe('—')
  })
  test('eta_in_hours_then_days', () => {
    expect(formatEta(19)).toBe('≈ 19 h')
    expect(formatEta(72)).toBe('≈ 3 dias')
    expect(formatEta(null)).toBe('—')
  })
})

describe('formatDateTime', () => {
  test('date_and_time', () =>
    expect(formatDateTime(new Date(2026, 10, 18, 8, 12).toISOString())).toBe('18/11/2026 08:12'))
  test('null_is_dash', () => expect(formatDateTime(null)).toBe('—'))
})

describe('formatPercent and longDate', () => {
  test('percent_uses_comma_and_one_decimal', () => {
    expect(formatPercent(1.6)).toBe('1,6%')
    expect(formatPercent(23.4)).toBe('23,4%')
    expect(formatPercent(100)).toBe('100%')
    expect(formatPercent(null)).toBe('—')
  })
  test('long_date_in_portuguese', () => expect(longDate(now)).toBe('Quinta, 19 de novembro'))
})
