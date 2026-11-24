// One schema per backend response model (src/api/queries.py, src/api/routes/*.py,
// src/db/rows.py). Dates stay ISO strings; formatting lives in lib/format.ts.
import { z } from 'zod'

const str = z.string()
const strN = z.string().nullable()
const int = z.number().int()

export const EmailListItem = z.object({
  id: str,
  sender: strN,
  subject: strN,
  snippet: strN,
  received_at: strN,
  category: strN,
  is_relevant: z.boolean().nullable(),
  deadline: strN,
  has_document: z.boolean().nullable(),
  ephemeral: z.boolean().nullable(),
  pending_actions: int,
  attachments: int,
  due_soon: z.boolean().default(false),
})
export type EmailListItem = z.infer<typeof EmailListItem>

export const EmailPage = z.object({ items: z.array(EmailListItem), next_before: strN })
export type EmailPage = z.infer<typeof EmailPage>

export const DocumentOut = z.object({
  id: int,
  email_id: str,
  filename: str,
  mime_type: str,
  size: int,
  status: str,
  skip_reason: strN,
  doc_class: strN,
  doc_type: strN,
  title: strN,
  summary: strN,
  issuer: strN,
  amount_cents: int.nullable(),
  issued_on: strN,
  due_on: strN,
  barcode: strN,
  email_subject: strN.default(null),
  email_received_at: strN.default(null),
})
export type DocumentOut = z.infer<typeof DocumentOut>

export const Action = z.object({
  id: int,
  email_id: str,
  sender: strN,
  subject: strN,
  kind: str,
  reason: str,
  status: str,
  origin: str,
  rule_id: int.nullable(),
  run_id: int.nullable(),
  created_at: str,
  has_important_attachment: z.boolean(),
})
export type Action = z.infer<typeof Action>

export const Run = z.object({
  id: int,
  trigger: str,
  email_id: strN,
  config_id: int,
  status: str,
  summary: strN,
  error: strN,
  started_at: str,
  finished_at: strN,
})
export type Run = z.infer<typeof Run>

export const EmailDetail = z.object({
  item: EmailListItem,
  reason: strN,
  body: strN,
  attachments: z.array(DocumentOut),
  actions: z.array(Action),
  runs: z.array(Run),
})
export type EmailDetail = z.infer<typeof EmailDetail>

export const AgentStep = z.object({
  id: int,
  run_id: int,
  position: int,
  kind: str,
  name: str,
  source: str,
  status: str,
  detail: strN,
  started_at: str,
  finished_at: strN,
})
export type AgentStep = z.infer<typeof AgentStep>

export const LlmCall = z.object({
  id: int,
  created_at: str,
  purpose: str,
  model: str,
  attempt: int,
  status: str,
  error_kind: strN,
  error: strN,
  latency_ms: int,
  input_tokens: int,
  output_tokens: int,
  total_tokens: int,
})
export type LlmCall = z.infer<typeof LlmCall>

export const RunDetail = z.object({ run: Run, steps: z.array(AgentStep), llm_calls: z.array(LlmCall) })
export type RunDetail = z.infer<typeof RunDetail>

export const JobCounts = z.object({
  queued: int.default(0),
  running: int.default(0),
  done: int.default(0),
  dead: int.default(0),
})
export type JobCounts = z.infer<typeof JobCounts>

export const DeadJob = z.object({
  id: int,
  kind: str,
  attempts: int,
  last_error: strN,
  finished_at: strN,
})
export type DeadJob = z.infer<typeof DeadJob>

export const JobsOut = z.object({ counts: z.record(str, JobCounts), dead: z.array(DeadJob) })
export type JobsOut = z.infer<typeof JobsOut>

export const BackfillProgress = z.object({
  run_id: int,
  pages: int,
  fetch_done: z.boolean(),
  emails: int,
  classified: int,
  indexed: int,
  embedded: int,
  attachments: z.record(str, int),
  jobs: z.record(str, JobCounts),
})
export type BackfillProgress = z.infer<typeof BackfillProgress>

export const BackfillStatus = z.object({
  progress: BackfillProgress,
  mailbox_total: int.nullable(),
  percent: z.number().nullable(),
  pages_last_hour: int,
  pages_last_day: int,
  eta_hours: z.number().nullable(),
  deferred_until: strN,
  paused: z.boolean(),
  turbo: z.boolean(),
  turbo_available: z.boolean(),
})
export type BackfillStatus = z.infer<typeof BackfillStatus>

export const UsageDay = z.object({
  day: str,
  purpose: str,
  calls: int,
  errors: int,
  input_tokens: int,
  output_tokens: int,
})
export type UsageDay = z.infer<typeof UsageDay>

export const Rule = z.object({
  id: int,
  name: str,
  sender_address: strN,
  sender_domain: strN,
  category: strN,
  subject_contains: strN,
  unless_document: z.boolean(),
  action: str,
  mode: str,
  active: z.boolean(),
  fired_count: int,
  approved_count: int,
  rejected_count: int,
})
export type Rule = z.infer<typeof Rule>

export const Policy = z.object({ target: str, trigger: str, policy: str })
export type Policy = z.infer<typeof Policy>

export const KbEntry = z.object({
  id: int,
  kind: str,
  scope: str,
  text: str,
  source: str,
  active: z.boolean(),
  created_at: str,
})
export type KbEntry = z.infer<typeof KbEntry>

export const AgentConfig = z.object({
  id: int,
  version: int,
  active: z.boolean(),
  decide_prompt: str,
  chat_prompt: str,
  max_turns: int,
  created_at: str,
})
export type AgentConfig = z.infer<typeof AgentConfig>

export const ConfigsOut = z.object({ active: AgentConfig.nullable(), versions: z.array(AgentConfig) })
export type ConfigsOut = z.infer<typeof ConfigsOut>

export const AgentSettings = z.object({ llm_mode: z.enum(['free', 'fast']).nullable() })
export type AgentSettings = z.infer<typeof AgentSettings>

export const Audit = z.object({
  id: int,
  entity: str,
  entity_id: str,
  channel: str,
  before: strN,
  after: strN,
  created_at: str,
})
export type Audit = z.infer<typeof Audit>

export const Session = z.object({
  family_id: str,
  started_at: str,
  last_used_at: str,
  expires_at: str,
  ip: strN,
  user_agent: strN,
  current: z.boolean(),
})
export type Session = z.infer<typeof Session>

export const BlocksOut = z.object({
  global_lock: z.boolean(),
  ips: z.array(z.object({ ip: str, reason: str, since: str })),
})
export type BlocksOut = z.infer<typeof BlocksOut>

export const SecurityEvent = z.object({
  id: int,
  kind: str,
  ip: strN,
  detail: str,
  created_at: str,
})
export type SecurityEvent = z.infer<typeof SecurityEvent>

export const Notification = z.object({
  id: int,
  kind: str,
  subject: str,
  status: str,
  error: strN,
  created_at: str,
  sent_at: strN,
})
export type Notification = z.infer<typeof Notification>

export const Taxonomy = z.object({
  version: int,
  categories: z.array(z.object({ name: str, definition: str, color: strN })),
  flags: z.array(z.object({ name: str, definition: str })),
})
export type Taxonomy = z.infer<typeof Taxonomy>

export const GmailStatus = z.object({
  email_address: str,
  status: z.enum(['connected', 'disconnected', 'revoked', 'template']),
  last_sync_at: strN,
  last_error: strN,
})
export type GmailStatus = z.infer<typeof GmailStatus>

// onboarding_done is true for users that predate the invites (no step stored).
export const Me = z.object({
  username: str,
  role: str.default('member'),
  onboarding_step: strN.default(null),
  onboarding_done: z.boolean().default(true),
})
export type Me = z.infer<typeof Me>

// --- entry and onboarding (src/api/routes/entry.py, onboarding.py) -------------
export const InviteInfo = z.object({ email: str })
export type InviteInfo = z.infer<typeof InviteInfo>

export const GoogleUrl = z.object({ url: str })
export type GoogleUrl = z.infer<typeof GoogleUrl>

export const OnboardingState = z.object({
  step: strN,
  steps: z.array(str),
  done: z.boolean(),
  email: strN,
  has_password: z.boolean(),
  totp_confirmed: z.boolean(),
})
export type OnboardingState = z.infer<typeof OnboardingState>

export const TotpStart = z.object({ secret: str, uri: str })
export type TotpStart = z.infer<typeof TotpStart>

export const RecoveryCodes = z.object({ codes: z.array(str) })
export type RecoveryCodes = z.infer<typeof RecoveryCodes>

export const Decision = z.object({ decided: int })
export type Decision = z.infer<typeof Decision>

export const DocumentLink = z.object({ url: str, expires_in: int })
export type DocumentLink = z.infer<typeof DocumentLink>

// --- chat (src/chat/store.py views) ------------------------------------------
export const Conversation = z.object({ id: int, title: strN, updated_at: str, busy: z.boolean() })
export type Conversation = z.infer<typeof Conversation>

export const CardItem = z.object({
  email_id: strN,
  action_id: int.nullable(),
  attachment_id: int.nullable(),
  title: str,
  subtitle: str,
  status: strN,
})
export type CardItem = z.infer<typeof CardItem>

export const ChatCard = z.object({
  id: int,
  kind: z.enum(['rule', 'preference', 'promote', 'policy', 'actions', 'emails', 'documents']),
  status: z.enum(['proposed', 'confirmed', 'cancelled', 'failed', 'info']),
  channel: str,
  error: strN,
  rule: z
    .object({
      name: str,
      action: str,
      sender_address: strN,
      sender_domain: strN,
      category: strN,
      subject_contains: strN,
      unless_document: z.boolean(),
    })
    .nullable(),
  preference: z.object({ scope: str, text: str }).nullable(),
  promote: z
    .object({ rule_id: int, name: str, action: str, mode: str, fired: int, approved: int, rejected: int })
    .nullable(),
  policy: z.object({ target: str, trigger: str, policy: str }).nullable(),
  verdict: strN,
  comment: strN,
  apply_existing: z.boolean(),
  batch_query: strN.default(null),
  batch_total: int.nullable().default(null),
  batch_no: int.nullable().default(null),
  affected: int.nullable(),
  created_rule_id: int.nullable(),
  kb_entry_id: int.nullable(),
  items: z.array(CardItem),
})
export type ChatCard = z.infer<typeof ChatCard>

export const CallFailure = z.object({ model: str, reason: str })
export type CallFailure = z.infer<typeof CallFailure>

export const ChatModels = z.object({ models: z.array(str) })

export const ChatMessage = z.object({
  id: int,
  conversation_id: int,
  role: z.enum(['user', 'assistant']),
  text: str,
  input: z.enum(['text', 'audio', 'event']),
  status: z.enum(['queued', 'running', 'done', 'failed']),
  progress: strN,
  error: strN,
  audio_ms: int.nullable(),
  run_id: int.nullable(),
  created_at: str,
  cards: z.array(ChatCard),
  model: strN.default(null),
  failures: z.array(CallFailure).default([]),
})
export type ChatMessage = z.infer<typeof ChatMessage>

export const Turn = z.object({ user: ChatMessage, assistant: ChatMessage })
export type Turn = z.infer<typeof Turn>
