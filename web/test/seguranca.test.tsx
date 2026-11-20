import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { expect, test } from 'vitest'
import type { BlocksOut, Notification, SecurityEvent, Session } from '../src/api/schemas'
import { blocks, notification, securityEvent, session } from './fixtures'
import { server } from './server'
import { json, noContent, renderApp, signedIn } from './render'

const phone: Session = {
  ...session,
  family_id: 'f-2',
  current: false,
  user_agent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17) Safari',
}

interface Data {
  sessions?: Session[]
  blocks?: BlocksOut
  events?: SecurityEvent[]
  notifications?: Notification[]
}

function load(d: Data = {}) {
  signedIn([])
  const kinds: Array<string | null> = []
  server.use(
    http.get('/api/auth/sessions', () => json(d.sessions ?? [session, phone])),
    http.get('/api/security/blocks', () => json(d.blocks ?? blocks)),
    http.get('/api/security/events', ({ request }) => {
      kinds.push(new URL(request.url).searchParams.get('kind'))
      return json(d.events ?? [securityEvent])
    }),
    http.get('/api/notifications', () => json(d.notifications ?? [notification])),
  )
  return kinds
}

test('all_clear_says_everything_is_in_order', async () => {
  load()
  renderApp('/seguranca')
  expect(await screen.findByRole('heading', { name: 'Tudo em ordem' })).toBeInTheDocument()
  expect(screen.getByText('Login aberto')).toBeInTheDocument()
  expect(screen.getByText('Nenhum IP bloqueado.')).toBeInTheDocument()
})

test('blocked_ip_and_global_lock_turn_the_header_into_a_warning', async () => {
  load({
    blocks: {
      global_lock: true,
      ips: [{ ip: '198.51.100.7', reason: '3 falhas em 24 h', since: '2026-11-19T10:00:00Z' }],
    },
  })
  renderApp('/seguranca')
  expect(await screen.findByRole('heading', { name: 'Atenção: login com restrições' })).toBeInTheDocument()
  expect(screen.getByText('Login fechado para todos.')).toBeInTheDocument()
  expect(screen.getByText(/198\.51\.100\.7/)).toBeInTheDocument()
  expect(screen.getByText(/src\.cli\.unlock/)).toBeInTheDocument()
})

test('other_sessions_can_be_ended_but_the_current_one_cannot', async () => {
  load()
  let deleted = ''
  server.use(
    http.delete('/api/auth/sessions/:id', ({ params }) => {
      deleted = String(params.id)
      return noContent()
    }),
  )
  const user = userEvent.setup()
  renderApp('/seguranca')
  const panel = await screen.findByRole('region', { name: 'Sessões ativas' })
  expect(await within(panel).findByText('Chrome, Windows')).toBeInTheDocument()
  expect(within(panel).getByText('esta')).toBeInTheDocument()
  expect(within(panel).getByText('Safari, iPhone')).toBeInTheDocument()
  const ends = within(panel).getAllByRole('button', { name: 'Encerrar' })
  expect(ends).toHaveLength(1)
  await user.click(ends[0]!)
  await waitFor(() => expect(deleted).toBe('f-2'))
})

test('event_type_filter_refetches_with_kind', async () => {
  const kinds = load()
  const user = userEvent.setup()
  renderApp('/seguranca')
  await user.selectOptions(await screen.findByLabelText('Tipo'), 'download')
  await waitFor(() => expect(kinds).toContain('download'))
})

test('null_ip_and_empty_detail_render_dashes', async () => {
  load({ events: [{ ...securityEvent, ip: null, detail: '' }] })
  renderApp('/seguranca')
  const table = await screen.findByRole('table')
  const cells = within(table).getAllByRole('cell')
  expect(cells.map((c) => c.textContent).slice(-2)).toEqual(['—', '—'])
})

test('unknown_event_kind_falls_back_to_its_raw_name', async () => {
  load({ events: [{ ...securityEvent, kind: 'novo_tipo' }] })
  renderApp('/seguranca')
  expect(await screen.findByText('novo_tipo')).toBeInTheDocument()
})

test('failed_notification_is_marked', async () => {
  load({ notifications: [{ ...notification, status: 'failed', error: 'smtp', sent_at: null }] })
  renderApp('/seguranca')
  const panel = await screen.findByRole('region', { name: 'Alertas enviados' })
  expect(await within(panel).findByText('falhou')).toBeInTheDocument()
})

test('empty_lists_have_messages', async () => {
  load({ events: [], notifications: [], sessions: [] })
  renderApp('/seguranca')
  expect(await screen.findByText('Nenhum evento registrado.')).toBeInTheDocument()
  expect(screen.getByText('Nenhum alerta enviado.')).toBeInTheDocument()
  expect(screen.getByText('Nenhuma sessão ativa.')).toBeInTheDocument()
})
