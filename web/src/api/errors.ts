export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export const RATE_LIMITED = 'Muitas requisições, tente de novo em instantes'
export const BAD_RESPONSE = 'resposta inesperada do servidor'
export const OFFLINE = 'Sem conexão com o servidor'

const FALLBACK: Record<number, string> = {
  401: 'sessão expirada',
  403: 'requisição recusada',
  404: 'não encontrado',
  423: 'login bloqueado',
}

export function fallbackDetail(status: number): string {
  if (status === 429) return RATE_LIMITED
  return FALLBACK[status] ?? `erro ${status} no servidor`
}
