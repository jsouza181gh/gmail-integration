import { QueryClient, useQuery } from '@tanstack/react-query'
import { ApiError } from './errors'
import { listActions } from './endpoints'

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // 4xx will not fix itself; only retry flaky transport once.
        retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 1,
        refetchOnWindowFocus: false,
        staleTime: 10_000,
      },
    },
  })
}

export const qk = {
  me: ['me'] as const,
  pending: ['actions', 'pending'] as const,
}

export function usePendingActions() {
  return useQuery({ queryKey: qk.pending, queryFn: () => listActions({ status: 'pending', limit: 1000 }) })
}

export function usePendingCount(): number {
  return usePendingActions().data?.length ?? 0
}
