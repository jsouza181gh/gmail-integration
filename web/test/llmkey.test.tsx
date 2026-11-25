import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { expect, test } from 'vitest'
import { server } from './server'
import { json, renderApp, signedIn } from './render'

const status = {
  configured: true,
  has_backfill_key: false,
  validated_at: '2026-11-24T12:00:00Z',
  last_error: null,
  next_step: null,
}

test('explains_where_to_get_the_key_and_what_it_costs', async () => {
  signedIn([])
  renderApp('/onboarding/chave')
  expect(await screen.findByRole('heading', { name: 'Chave do Gemini' })).toBeInTheDocument()
  const link = screen.getByRole('link', { name: /aistudio\.google\.com\/apikey/ })
  expect(link).toHaveAttribute('href', 'https://aistudio.google.com/apikey')
  expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
  expect(screen.getByText(/Quanto custa/)).toBeInTheDocument()
})

test('a_working_key_is_confirmed_and_never_kept_in_the_field', async () => {
  signedIn([])
  const sent: unknown[] = []
  server.use(
    http.post('/api/onboarding/llm-key', async ({ request }) => {
      sent.push(await request.json())
      return json(status)
    }),
  )
  const user = userEvent.setup()
  renderApp('/onboarding/chave')
  await user.type(await screen.findByLabelText('Sua chave'), '  AIza-abc ')
  await user.click(screen.getByRole('button', { name: 'Testar e salvar' }))

  expect(await screen.findByText(/Chave funcionando/)).toBeInTheDocument()
  expect(sent).toEqual([{ app_key: 'AIza-abc' }])
  expect(screen.getByLabelText('Sua chave')).toHaveValue('')
  expect(screen.getByRole('button', { name: 'Continuar' })).toBeInTheDocument()
})

test('a_refused_key_shows_the_reason_in_plain_words', async () => {
  signedIn([])
  server.use(
    http.post('/api/onboarding/llm-key', () =>
      json({ detail: 'Essa chave não existe ou foi copiada incompleta.' }, 422),
    ),
  )
  const user = userEvent.setup()
  renderApp('/onboarding/chave')
  await user.type(await screen.findByLabelText('Sua chave'), 'ruim')
  await user.click(screen.getByRole('button', { name: 'Testar e salvar' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('não existe ou foi copiada incompleta')
  await waitFor(() => expect(screen.queryByText(/Chave funcionando/)).not.toBeInTheDocument())
})

test('the_second_key_is_optional_and_sent_when_filled', async () => {
  signedIn([])
  const sent: unknown[] = []
  server.use(
    http.post('/api/onboarding/llm-key', async ({ request }) => {
      sent.push(await request.json())
      return json({ ...status, has_backfill_key: true })
    }),
  )
  const user = userEvent.setup()
  renderApp('/onboarding/chave')
  await user.type(await screen.findByLabelText('Sua chave'), 'AIza-1')
  await user.click(screen.getByText(/Avançado/))
  await user.type(screen.getByLabelText('Segunda chave'), 'AIza-2')
  await user.click(screen.getByRole('button', { name: 'Testar e salvar' }))
  await screen.findByText(/Chave funcionando/)
  expect(sent).toEqual([{ app_key: 'AIza-1', backfill_key: 'AIza-2' }])
})
