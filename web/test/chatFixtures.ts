import type { ChatCard, ChatMessage, Conversation, Turn } from '../src/api/schemas'

export const conversation: Conversation = {
  id: 1,
  title: 'Boleto da Light',
  updated_at: '2026-11-19T12:00:00Z',
  busy: false,
}

const baseCard: ChatCard = {
  id: 1,
  kind: 'rule',
  status: 'proposed',
  channel: 'chat',
  error: null,
  rule: null,
  preference: null,
  promote: null,
  policy: null,
  verdict: null,
  comment: null,
  apply_existing: false,
  affected: null,
  created_rule_id: null,
  kb_entry_id: null,
  items: [],
  batch_query: null,
  batch_total: null,
  batch_no: null,
}

export function card(over: Partial<ChatCard>): ChatCard {
  return { ...baseCard, ...over }
}

export const ruleCard = card({
  id: 11,
  kind: 'rule',
  channel: 'audio',
  affected: 37,
  rule: {
    name: 'Lixo da Loja Vértice',
    action: 'trash',
    sender_address: null,
    sender_domain: 'lojavertice.com.br',
    category: null,
    subject_contains: null,
    unless_document: true,
  },
})

export const preferenceCard = card({
  id: 12,
  kind: 'preference',
  preference: { scope: 'decision', text: 'Vagas de Java não me interessam' },
})

export const promoteCard = card({
  id: 13,
  kind: 'promote',
  promote: {
    rule_id: 4, name: 'Arquivar Vértice', action: 'archive', mode: 'suggest',
    fired: 12, approved: 11, rejected: 1,
  },
})

export const policyCard = card({
  id: 14,
  kind: 'policy',
  policy: { target: 'archive', trigger: 'webhook', policy: 'auto' },
})

export const actionsCard = card({
  id: 15,
  kind: 'actions',
  verdict: 'reject',
  comment: 'não quero isso',
  items: [
    { email_id: null, action_id: 7, attachment_id: null, title: '50% off', subtitle: 'trash · ofertas@loja.com', status: 'pending' },
  ],
})

export const emailsCard = card({
  id: 16,
  kind: 'emails',
  status: 'info',
  items: [
    { email_id: 'e1', action_id: null, attachment_id: null, title: 'Sua conta chegou', subtitle: 'Light <c@light.com>', status: null },
  ],
})

export const documentsCard = card({
  id: 17,
  kind: 'documents',
  status: 'info',
  items: [
    { email_id: null, action_id: null, attachment_id: 3, title: 'Boleto Light novembro/2026', subtitle: 'boleto · 41 KB', status: null },
  ],
})

export function message(over: Partial<ChatMessage>): ChatMessage {
  return {
    id: 1,
    conversation_id: 1,
    role: 'user',
    text: 'Me manda o último boleto da Light.',
    input: 'text',
    status: 'done',
    progress: null,
    error: null,
    audio_ms: null,
    run_id: null,
    created_at: '2026-11-19T12:00:00Z',
    cards: [],
    model: null,
    failures: [],
    ...over,
  }
}

export function turn(assistant: Partial<ChatMessage>, user: Partial<ChatMessage> = {}): Turn {
  return {
    user: message(user),
    assistant: message({ id: 2, role: 'assistant', text: '', status: 'queued', ...assistant }),
  }
}
