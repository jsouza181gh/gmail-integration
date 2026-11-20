import { render } from '@testing-library/react'
import { expect, test } from 'vitest'
import { renderMarkdown } from './markdown'

function html(text: string): HTMLElement {
  return render(<div data-testid="md">{renderMarkdown(text)}</div>).getByTestId('md')
}

test('paragraphs_bold_and_code', () => {
  const el = html('O de **novembro**: `R$ 189,90`.\n\nVence em 28/11.')
  const ps = el.querySelectorAll('p')
  expect(ps).toHaveLength(2)
  expect(el.querySelector('strong')?.textContent).toBe('novembro')
  expect(el.querySelector('code')?.textContent).toBe('R$ 189,90')
})

test('bullet_and_numbered_lists', () => {
  const el = html('Achei:\n- boleto **Light**\n* fatura Nubank\n\n1. primeiro\n2. segundo')
  expect(el.querySelector('ul')?.querySelectorAll('li')).toHaveLength(2)
  expect(el.querySelector('ol')?.querySelectorAll('li')).toHaveLength(2)
  expect(el.querySelector('li strong')?.textContent).toBe('Light')
})

test('html_is_shown_as_text', () => {
  const el = html('<img src=x onerror=alert(1)> **ok**')
  expect(el.querySelector('img')).toBeNull()
  expect(el.textContent).toContain('<img src=x onerror=alert(1)>')
})

test('single_newlines_become_line_breaks', () => {
  const el = html('linha 1\nlinha 2')
  expect(el.querySelectorAll('br')).toHaveLength(1)
})
