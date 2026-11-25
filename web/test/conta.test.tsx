import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { expect, test } from 'vitest'
import type { AccountInfo } from '../src/api/schemas'
import { server } from './server'
import { json, noContent, renderApp, signedIn } from './render'

const info: AccountInfo = { email: 'ana@example.com', status: 'connected', can_delete: true, backup_copies: 7 }

function load(account: AccountInfo = info) {
  signedIn([])
  server.use(http.get('/api/account', () => json(account)))
}

test('states_backup_retention_honestly', async () => {
  load()
  renderApp('/conta')
  expect(await screen.findByText(/as 7 cópias mais recentes/)).toBeInTheDocument()
  expect(screen.getByText(/depois de 7 atualizações do sistema/)).toBeInTheDocument()
})

test('disconnect_asks_before_calling', async () => {
  load()
  let called = 0
  server.use(
    http.post('/api/account/disconnect', () => {
      called += 1
      return noContent()
    }),
  )
  renderApp('/conta')
  await userEvent.click(await screen.findByRole('button', { name: 'Desconectar o Gmail' }))
  expect(called).toBe(0)
  await userEvent.click(screen.getByRole('button', { name: 'Sim, desconectar' }))
  await waitFor(() => expect(called).toBe(1))
})

test('delete_needs_the_typed_email_and_a_secret', async () => {
  load()
  let body: unknown
  server.use(
    http.post('/api/account/delete', async ({ request }) => {
      body = await request.json()
      return noContent()
    }),
  )
  renderApp('/conta')
  await userEvent.click(await screen.findByRole('button', { name: 'Excluir minha conta' }))
  const go = screen.getByRole('button', { name: 'Excluir tudo para sempre' })
  expect(go).toBeDisabled()
  await userEvent.type(screen.getByLabelText(/Digite ana@example.com/), 'ana@example.com')
  await userEvent.type(screen.getByLabelText('Sua senha'), 'segredo')
  expect(go).toBeEnabled()
  await userEvent.click(go)
  await waitFor(() => expect(body).toEqual({ email: 'ana@example.com', password: 'segredo' }))
})

test('delete_can_use_the_authenticator_code', async () => {
  load()
  let body: unknown
  server.use(
    http.post('/api/account/delete', async ({ request }) => {
      body = await request.json()
      return noContent()
    }),
  )
  renderApp('/conta')
  await userEvent.click(await screen.findByRole('button', { name: 'Excluir minha conta' }))
  await userEvent.click(screen.getByRole('button', { name: 'Usar código do app autenticador' }))
  await userEvent.type(screen.getByLabelText(/Digite ana@example.com/), 'ana@example.com')
  await userEvent.type(screen.getByLabelText(/Código de 6 dígitos/), '123456')
  await userEvent.click(screen.getByRole('button', { name: 'Excluir tudo para sempre' }))
  await waitFor(() => expect(body).toEqual({ email: 'ana@example.com', code: '123456' }))
})

test('wrong_secret_shows_the_server_message', async () => {
  load()
  server.use(
    http.post('/api/account/delete', () => json({ detail: 'senha ou código inválido' }, 403)),
  )
  renderApp('/conta')
  await userEvent.click(await screen.findByRole('button', { name: 'Excluir minha conta' }))
  await userEvent.type(screen.getByLabelText(/Digite ana@example.com/), 'ana@example.com')
  await userEvent.type(screen.getByLabelText('Sua senha'), 'x')
  await userEvent.click(screen.getByRole('button', { name: 'Excluir tudo para sempre' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('senha ou código inválido')
})

test('admin_sees_why_it_cannot_delete', async () => {
  load({ ...info, can_delete: false })
  renderApp('/conta')
  expect(await screen.findByText(/conta de administração/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Excluir minha conta' })).not.toBeInTheDocument()
})
