import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'

// The shell always asks for warnings; tests that care override this.
export const server = setupServer(http.get('/api/warnings', () => HttpResponse.json([])))
