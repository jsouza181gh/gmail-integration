import { render } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import App from '../src/App'
import { server } from './server'

export function renderApp(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

type JsonBody = Parameters<typeof HttpResponse.json>[0]
export const json = (body: JsonBody, status = 200) => HttpResponse.json(body, { status })
export const noContent = () => new HttpResponse(null, { status: 204 })
export const unauthorized = () => new HttpResponse(null, { status: 401 })

/** Session is valid and `n` actions are pending. */
export function signedIn(pending: JsonBody = []) {
  server.use(
    http.get('/api/auth/me', () => json({ username: 'joao' })),
    http.get('/api/actions', () => json(pending)),
  )
}
