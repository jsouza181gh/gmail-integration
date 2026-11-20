// Minimal markdown for the agent's answers: paragraphs, line breaks, - / 1.
// lists, **bold** and `code`. Everything becomes React elements; HTML in the
// text stays text (React escapes it), so nothing from an email can inject markup.
import type { ReactNode } from 'react'

const BULLET = /^\s*[-*]\s+/
const NUMBER = /^\s*\d+[.)]\s+/
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`)/g

function inline(text: string, key: string): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    const k = `${key}.${i}`
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={k}>{part.slice(2, -2)}</strong>
    }
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return <code key={k}>{part.slice(1, -1)}</code>
    }
    return part
  })
}

function lines(block: string[], key: string): ReactNode[] {
  return block.flatMap((line, i) => {
    const parts = inline(line, `${key}.${i}`)
    return i === 0 ? parts : [<br key={`${key}.br${i}`} />, ...parts]
  })
}

export function renderMarkdown(text: string): ReactNode {
  const out: ReactNode[] = []
  let para: string[] = []
  let list: { ordered: boolean; items: string[] } | null = null

  const flush = () => {
    const key = `b${out.length}`
    if (para.length) out.push(<p key={key}>{lines(para, key)}</p>)
    else if (list) {
      const items = list.items.map((item, i) => <li key={i}>{inline(item, `${key}.${i}`)}</li>)
      out.push(list.ordered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>)
    }
    para = []
    list = null
  }

  for (const raw of text.split('\n')) {
    const line = raw.trimEnd()
    if (!line.trim()) {
      flush()
      continue
    }
    const ordered = NUMBER.test(line)
    if (ordered || BULLET.test(line)) {
      if (para.length || (list && list.ordered !== ordered)) flush()
      list ??= { ordered, items: [] }
      list.items.push(line.replace(ordered ? NUMBER : BULLET, ''))
      continue
    }
    if (list) flush()
    para.push(line)
  }
  flush()
  return out
}
