import { QueryClient, useQuery } from '@tanstack/react-query'
import { backfill, listActions, me, warnings } from './endpoints'
import { ApiError } from './errors'

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // 4xx will not fix itself; only retry flaky transport once.
        retry: (count, error) =>
          !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 1,
        refetchOnWindowFocus: false,
        staleTime: 10_000,
      },
    },
  })
}

export const qk = {
  me: ['me'] as const,
  pending: ['actions', 'pending'] as const,
  backfill: ['backfill'] as const,
  warnings: ['warnings'] as const,
}

export function useMe() {
  return useQuery({ queryKey: qk.me, queryFn: me, retry: false, staleTime: 60_000 })
}

export function usePendingActions() {
  return useQuery({ queryKey: qk.pending, queryFn: () => listActions({ status: 'pending', limit: 1000 }) })
}

export function usePendingCount(): number {
  return usePendingActions().data?.length ?? 0
}

export function useBackfill(opts: { enabled?: boolean; poll?: boolean } = {}) {
  return useQuery({
    queryKey: qk.backfill,
    queryFn: backfill,
    enabled: opts.enabled ?? true,
    refetchInterval: opts.poll ? 15_000 : false,
  })
}

export function useWarnings() {
  return useQuery({ queryKey: qk.warnings, queryFn: warnings, retry: false })
}
