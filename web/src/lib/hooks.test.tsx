import { act, renderHook } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { useDeferred } from './deferred'
import { useHotkey } from './hotkeys'

describe('useDeferred', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  test('runs_after_delay', () => {
    const run = vi.fn(() => Promise.resolve())
    const { result } = renderHook(() => useDeferred(5000))
    act(() => result.current.schedule('Aprovar 2', run))
    expect(result.current.pending).toEqual({ label: 'Aprovar 2' })
    act(() => void vi.advanceTimersByTime(4999))
    expect(run).not.toHaveBeenCalled()
    act(() => void vi.advanceTimersByTime(1))
    expect(run).toHaveBeenCalledTimes(1)
    expect(result.current.pending).toBeNull()
  })

  test('cancel_prevents_run', () => {
    const run = vi.fn(() => Promise.resolve())
    const { result } = renderHook(() => useDeferred(5000))
    act(() => result.current.schedule('x', run))
    act(() => result.current.cancel())
    act(() => void vi.advanceTimersByTime(10_000))
    expect(run).not.toHaveBeenCalled()
    expect(result.current.pending).toBeNull()
  })

  test('unmount_flushes_pending_immediately', () => {
    const run = vi.fn(() => Promise.resolve())
    const { result, unmount } = renderHook(() => useDeferred(5000))
    act(() => result.current.schedule('x', run))
    unmount()
    expect(run).toHaveBeenCalledTimes(1)
  })

  test('second_schedule_flushes_the_first', () => {
    const first = vi.fn(() => Promise.resolve())
    const second = vi.fn(() => Promise.resolve())
    const { result } = renderHook(() => useDeferred(5000))
    act(() => result.current.schedule('a', first))
    act(() => result.current.schedule('b', second))
    expect(first).toHaveBeenCalledTimes(1)
    expect(second).not.toHaveBeenCalled()
    act(() => void vi.advanceTimersByTime(5000))
    expect(second).toHaveBeenCalledTimes(1)
  })
})

describe('useHotkey', () => {
  test('fires_on_key_and_ignores_modifiers', async () => {
    const user = userEvent.setup()
    const handler = vi.fn()
    renderHook(() => useHotkey('y', handler))
    await user.keyboard('y')
    await user.keyboard('{Control>}y{/Control}')
    expect(handler).toHaveBeenCalledTimes(1)
  })

  test('does_not_fire_while_typing_in_an_input', async () => {
    const user = userEvent.setup()
    const handler = vi.fn()
    renderHook(() => useHotkey('y', handler))
    const input = document.createElement('input')
    document.body.append(input)
    input.focus()
    await user.keyboard('y')
    input.remove()
    expect(handler).not.toHaveBeenCalled()
  })

  test('disabled_does_not_fire', async () => {
    const user = userEvent.setup()
    const handler = vi.fn()
    renderHook(() => useHotkey('y', handler, false))
    await user.keyboard('y')
    expect(handler).not.toHaveBeenCalled()
  })
})
