import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { Badge } from './Badge'
import { Chip } from './Chip'
import { Empty } from './Empty'
import { EmailRow } from './Row'
import { Progress } from './Progress'
import { Switch } from './Switch'
import { ToastProvider, useToast } from './Toast'

function Harness({ undo, ms }: { undo?: () => void; ms?: number }) {
  const toast = useToast()
  return (
    <button type="button" onClick={() => toast.show('3 sugestões aprovadas.', { undo, ms })}>
      mostrar
    </button>
  )
}

describe('Chip', () => {
  test('exposes_pressed_state', () => {
    render(<Chip pressed onClick={() => {}}>Todos</Chip>)
    expect(screen.getByRole('button', { name: 'Todos' })).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('Badge', () => {
  test('tone_maps_to_design_system_class', () => {
    render(<Badge tone="warning">Vence hoje</Badge>)
    expect(screen.getByText('Vence hoje')).toHaveClass('ia-badge', 'ia-badge--warning')
  })
})

describe('Switch', () => {
  function Wrapper() {
    const [on, setOn] = useState(false)
    return <Switch checked={on} onChange={setOn} label="Modo fast" />
  }

  test('toggles_with_click_and_space', async () => {
    const user = userEvent.setup()
    render(<Wrapper />)
    const sw = screen.getByRole('switch', { name: /Modo fast/ })
    expect(sw).toHaveAttribute('aria-checked', 'false')
    await user.click(sw)
    expect(sw).toHaveAttribute('aria-checked', 'true')
    sw.focus()
    await user.keyboard(' ')
    expect(sw).toHaveAttribute('aria-checked', 'false')
  })
})

describe('Progress', () => {
  test('null_value_renders_empty_bar_without_invalid_width', () => {
    const { container } = render(<Progress value={null} />)
    const bar = container.querySelector<HTMLElement>('.ia-progress__bar')
    expect(bar?.style.width).toBe('0%')
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow')
  })

  test('value_is_clamped_and_paused_is_striped', () => {
    const { container } = render(<Progress value={140} paused />)
    expect(container.querySelector<HTMLElement>('.ia-progress__bar')?.style.width).toBe('100%')
    expect(container.querySelector('.ia-progress')).toHaveClass('ia-progress--paused')
  })
})

describe('Empty', () => {
  test('renders_text_and_action', () => {
    render(<Empty text="Nada aqui." action={<button type="button">Ir para a Caixa</button>} />)
    expect(screen.getByText('Nada aqui.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ir para a Caixa' })).toBeInTheDocument()
  })
})

describe('EmailRow', () => {
  test('button_variant_reports_selection', async () => {
    const user = userEvent.setup()
    const onClick = vi.fn()
    render(
      <EmailRow from="Light" date="ontem" subject="Conta" snippet="R$ 189,90" selected onClick={onClick} />,
    )
    const row = screen.getByRole('button')
    expect(row).toHaveAttribute('aria-selected', 'true')
    await user.click(row)
    expect(onClick).toHaveBeenCalledTimes(1)
  })
})

describe('Toast', () => {
  afterEach(() => vi.useRealTimers())

  test('undo_button_calls_undo_and_hides_the_toast', async () => {
    const user = userEvent.setup()
    const undo = vi.fn()
    render(
      <ToastProvider>
        <Harness undo={undo} />
      </ToastProvider>,
    )
    await user.click(screen.getByRole('button', { name: 'mostrar' }))
    expect(screen.getByRole('status')).toHaveTextContent('3 sugestões aprovadas.')
    await user.click(screen.getByRole('button', { name: /Desfazer/ }))
    expect(undo).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('3 sugestões aprovadas.')).not.toBeInTheDocument()
  })

  test('z_key_undoes', async () => {
    const user = userEvent.setup()
    const undo = vi.fn()
    render(
      <ToastProvider>
        <Harness undo={undo} />
      </ToastProvider>,
    )
    await user.click(screen.getByRole('button', { name: 'mostrar' }))
    await user.keyboard('z')
    expect(undo).toHaveBeenCalledTimes(1)
  })

  test('no_undo_button_without_undo_and_auto_hides', () => {
    vi.useFakeTimers()
    render(
      <ToastProvider>
        <Harness ms={3000} />
      </ToastProvider>,
    )
    act(() => screen.getByRole('button', { name: 'mostrar' }).click())
    expect(screen.queryByRole('button', { name: /Desfazer/ })).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toBeInTheDocument()
    act(() => void vi.advanceTimersByTime(3000))
    expect(screen.queryByText('3 sugestões aprovadas.')).not.toBeInTheDocument()
  })
})
