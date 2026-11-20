import type {
  Action,
  Audit,
  BackfillStatus,
  BlocksOut,
  ConfigsOut,
  DocumentOut,
  EmailDetail,
  EmailListItem,
  EmailPage,
  JobsOut,
  KbEntry,
  Notification,
  Policy,
  Rule,
  RunDetail,
  SecurityEvent,
  Session,
  Taxonomy,
  UsageDay,
} from '../src/api/schemas'

export const emailItem: EmailListItem = {
  id: 'e1',
  sender: 'Light <no-reply@light.com.br>',
  subject: 'Sua conta de novembro chegou',
  snippet: 'Valor R$ 189,90, vencimento 28/11.',
  received_at: '2026-11-18T08:12:00Z',
  category: 'Finanças',
  is_relevant: true,
  deadline: '2026-11-28',
  has_document: true,
  ephemeral: false,
  pending_actions: 0,
  attachments: 1,
  due_soon: true,
}

export const emailItemNulls: EmailListItem = {
  id: 'e2',
  sender: null,
  subject: null,
  snippet: null,
  received_at: null,
  category: null,
  is_relevant: null,
  deadline: null,
  has_document: null,
  ephemeral: null,
  pending_actions: 0,
  attachments: 0,
  due_soon: false,
}

export const emailPage: EmailPage = {
  items: [emailItem, emailItemNulls],
  next_before: '2026-11-18T08:12:00Z|e1',
}

export const document: DocumentOut = {
  id: 7,
  email_id: 'e1',
  filename: 'boleto.pdf',
  mime_type: 'application/pdf',
  size: 41000,
  status: 'done',
  skip_reason: null,
  doc_class: 'fiscal',
  doc_type: 'Boleto',
  title: 'Conta de luz de novembro',
  summary: null,
  issuer: 'Light S.A.',
  amount_cents: 18990,
  issued_on: '2026-11-18',
  due_on: '2026-11-28',
  barcode: '34191.79001 01043.510047 91020.150008 1 99990000018990',
  email_subject: 'Sua conta de novembro chegou',
  email_received_at: '2026-11-18T08:12:00Z',
}

export const documentNulls: DocumentOut = {
  ...document,
  id: 8,
  doc_class: null,
  doc_type: null,
  title: null,
  issuer: null,
  amount_cents: null,
  issued_on: null,
  due_on: null,
  barcode: null,
  email_subject: null,
  email_received_at: null,
}

export const action: Action = {
  id: 1,
  email_id: 'e3',
  sender: 'Loja Vértice <promo@lojavertice.com.br>',
  subject: 'Black Friday antecipada',
  kind: 'trash',
  reason: 'promoção recorrente',
  status: 'pending',
  origin: 'rule',
  rule_id: 5,
  run_id: 811,
  created_at: '2026-11-19T10:02:00Z',
  has_important_attachment: false,
}

const run = {
  id: 812,
  trigger: 'webhook',
  email_id: 'e1',
  config_id: 1,
  status: 'done',
  summary: 'nenhuma ação',
  error: null,
  started_at: '2026-11-18T08:12:05Z',
  finished_at: '2026-11-18T08:12:09Z',
}

export const emailDetail: EmailDetail = {
  item: emailItem,
  reason: 'cobrança com vencimento e boleto anexo',
  body: 'Olá,\nsua fatura chegou.',
  attachments: [document],
  actions: [action],
  runs: [run],
}

export const runDetail: RunDetail = {
  run,
  steps: [
    {
      id: 1,
      run_id: 812,
      position: 1,
      kind: 'tool',
      name: 'classify_email',
      source: 'agent',
      status: 'ok',
      detail: 'Finanças, relevante',
      started_at: '2026-11-18T08:12:05Z',
      finished_at: '2026-11-18T08:12:07Z',
    },
  ],
  llm_calls: [
    {
      id: 1,
      created_at: '2026-11-18T08:12:06Z',
      purpose: 'classify',
      model: 'gemini-3.5-flash-lite',
      attempt: 1,
      status: 'ok',
      error_kind: null,
      error: null,
      latency_ms: 1900,
      input_tokens: 800,
      output_tokens: 40,
      total_tokens: 840,
    },
  ],
}

export const backfillStatus: BackfillStatus = {
  progress: {
    run_id: 3,
    pages: 29,
    fetch_done: false,
    emails: 725,
    classified: 700,
    indexed: 700,
    embedded: 700,
    attachments: { done: 3, skipped: 4 },
    jobs: { backfill_page: { queued: 1, running: 0, done: 28, dead: 0 } },
  },
  mailbox_total: 45038,
  percent: 1.6,
  pages_last_hour: 96,
  pages_last_day: 400,
  eta_hours: 19,
  deferred_until: null,
}

export const backfillStatusNulls: BackfillStatus = {
  ...backfillStatus,
  mailbox_total: null,
  percent: null,
  eta_hours: null,
}

export const jobsOut: JobsOut = {
  counts: { backfill_page: { queued: 1, running: 0, done: 28, dead: 0 } },
  dead: [
    { id: 9, kind: 'index_attachment', attempts: 5, last_error: 'timeout', finished_at: '2026-11-19T09:00:00Z' },
  ],
}

export const usageDay: UsageDay = {
  day: '2026-11-19',
  purpose: 'classify',
  calls: 96,
  errors: 2,
  input_tokens: 150000,
  output_tokens: 34000,
}

export const rule: Rule = {
  id: 5,
  name: 'lojas → lixeira',
  sender_address: null,
  sender_domain: 'lojavertice.com.br',
  category: null,
  subject_contains: null,
  unless_document: false,
  action: 'trash',
  mode: 'suggest',
  active: true,
  fired_count: 31,
  approved_count: 23,
  rejected_count: 1,
}

export const policy: Policy = { target: 'archive', trigger: 'webhook', policy: 'suggest' }

export const kbEntry: KbEntry = {
  id: 1,
  kind: 'preference',
  scope: 'classification',
  text: 'E-mails de pedidos@lojavertice.com.br são Compras.',
  source: 'ui',
  active: true,
  created_at: '2026-11-19T10:00:00Z',
}

const config3 = {
  id: 3,
  version: 3,
  active: true,
  decide_prompt: 'Você decide…',
  chat_prompt: 'Você é o assistente do Gmail…',
  max_turns: 4,
  created_at: '2026-11-18T10:00:00Z',
}

export const configs: ConfigsOut = {
  active: config3,
  versions: [
    config3,
    { id: 2, version: 2, active: false, decide_prompt: 'Antigo', chat_prompt: 'Chat antigo', max_turns: 4, created_at: '2026-11-17T10:00:00Z' },
  ],
}

export const audit: Audit = {
  id: 1,
  entity: 'rule',
  entity_id: '5',
  channel: 'ui',
  before: null,
  after: 'criada',
  created_at: '2026-11-19T20:41:00Z',
}

export const session: Session = {
  family_id: 'f-1',
  started_at: '2026-11-19T20:35:00Z',
  last_used_at: '2026-11-19T20:50:00Z',
  expires_at: '2026-12-03T20:35:00Z',
  ip: '203.0.113.9',
  user_agent: 'Mozilla/5.0 Chrome Windows',
  current: true,
}

export const blocks: BlocksOut = { global_lock: false, ips: [] }

export const securityEvent: SecurityEvent = {
  id: 1,
  kind: 'login',
  ip: '203.0.113.9',
  detail: 'Chrome, Windows',
  created_at: '2026-11-19T20:35:00Z',
}

export const notification: Notification = {
  id: 1,
  kind: 'new_ip',
  subject: 'login de IP novo',
  status: 'sent',
  error: null,
  created_at: '2026-11-19T20:35:00Z',
  sent_at: '2026-11-19T20:35:02Z',
}

export const taxonomy: Taxonomy = {
  version: 1,
  categories: [
    { name: 'Finanças', definition: 'banco, fatura, cobrança, imposto', color: null },
    { name: 'Compras', definition: 'pedidos e notas fiscais', color: null },
  ],
  flags: [{ name: 'relevante', definition: 'merece atenção' }],
}
