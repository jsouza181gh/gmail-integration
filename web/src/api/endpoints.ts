// The only module that knows URLs. One function per backend route.
import { z } from 'zod'
import { request } from './client'
import * as s from './schemas'

const enc = encodeURIComponent

// --- auth ---------------------------------------------------------------
export const login = (username: string, password: string) =>
  request('POST', '/api/auth/login', { body: { username, password } })
export const secondFactor = (code: string) => request('POST', '/api/auth/2fa', { body: { code } })
export const logout = () => request('POST', '/api/auth/logout')
export const me = () => request('GET', '/api/auth/me', { schema: s.Me })
export const sessions = () => request('GET', '/api/auth/sessions', { schema: z.array(s.Session) })
export const revokeSession = (familyId: string) =>
  request('DELETE', `/api/auth/sessions/${enc(familyId)}`)

// --- mail ---------------------------------------------------------------
export interface EmailFilters {
  category?: string
  relevant?: boolean
  q?: string
  before?: string
  limit?: number
}
export const listEmails = (p: EmailFilters = {}) =>
  request('GET', '/api/emails', { query: { ...p }, schema: s.EmailPage })
export const relevantEmails = () =>
  request('GET', '/api/emails/relevant', { schema: z.array(s.EmailListItem) })
export const emailDetail = (id: string) =>
  request('GET', `/api/emails/${enc(id)}`, { schema: s.EmailDetail })
export const reclassify = (id: string, category: string, comment?: string) =>
  request('POST', `/api/emails/${enc(id)}/reclassify`, {
    body: { category, ...(comment ? { comment } : {}) },
  })
export const categories = () => request('GET', '/api/categories', { schema: s.Taxonomy })

// --- approvals ----------------------------------------------------------
export interface ActionFilters {
  status?: string
  kind?: string
  sender?: string
  rule_id?: number
  limit?: number
}
export const listActions = (p: ActionFilters = {}) =>
  request('GET', '/api/actions', { query: { ...p }, schema: z.array(s.Action) })
const decide = (verb: 'approve' | 'reject', ids: number[], comment?: string) =>
  request('POST', `/api/actions/${verb}`, {
    body: { ids, ...(comment ? { comment } : {}) },
    schema: s.Decision,
  })
export const approve = (ids: number[], comment?: string) => decide('approve', ids, comment)
export const reject = (ids: number[], comment?: string) => decide('reject', ids, comment)

// --- documents ----------------------------------------------------------
export interface DocumentFilters {
  class?: string
  type?: string
  q?: string
  status?: string
  before_id?: number
  limit?: number
}
export const listDocuments = (p: DocumentFilters = {}) =>
  request('GET', '/api/documents', { query: { ...p }, schema: z.array(s.DocumentOut) })
export const documentLink = (id: number) =>
  request('POST', `/api/documents/${id}/link`, { schema: s.DocumentLink })

// --- ops ----------------------------------------------------------------
export const backfill = () => request('GET', '/api/backfill', { schema: s.BackfillStatus })
export const backfillStart = () => request('POST', '/api/backfill/start', { schema: s.BackfillStatus })
export const backfillPause = () => request('POST', '/api/backfill/pause', { schema: s.BackfillStatus })
export const backfillResume = () => request('POST', '/api/backfill/resume', { schema: s.BackfillStatus })
export const runs = (p: { trigger?: string; status?: string; limit?: number } = {}) =>
  request('GET', '/api/runs', { query: { ...p }, schema: z.array(s.Run) })
export const runDetail = (id: number) => request('GET', `/api/runs/${id}`, { schema: s.RunDetail })
export const jobs = (limit = 50) => request('GET', '/api/jobs', { query: { limit }, schema: s.JobsOut })
export const requeueJob = (id: number) => request('POST', `/api/jobs/${id}/requeue`)
export const usage = (days = 7) =>
  request('GET', '/api/usage', { query: { days }, schema: z.array(s.UsageDay) })

// --- agent --------------------------------------------------------------
export const agentConfig = () => request('GET', '/api/agent/config', { schema: s.ConfigsOut })
export const newConfig = (decide_prompt: string, max_turns: number, chat_prompt?: string) =>
  request('POST', '/api/agent/config', {
    body: { decide_prompt, max_turns, ...(chat_prompt ? { chat_prompt } : {}) },
    schema: s.AgentConfig,
  })
export const activateConfig = (id: number) => request('POST', `/api/agent/config/${id}/activate`)
export const policies = () => request('GET', '/api/agent/policies', { schema: z.array(s.Policy) })
export const setPolicy = (target: string, trigger: string, policy: string) =>
  request('PUT', '/api/agent/policies', { body: { target, trigger, policy } })

export interface NewRule {
  name: string
  action: string
  sender_address?: string
  sender_domain?: string
  category?: string
  subject_contains?: string
  unless_document?: boolean
}
export const rules = () => request('GET', '/api/agent/rules', { schema: z.array(s.Rule) })
export const addRule = (r: NewRule) => request('POST', '/api/agent/rules', { body: r, schema: s.Rule })
export const promoteRule = (id: number) =>
  request('POST', `/api/agent/rules/${id}/promote`, { schema: s.Rule })
export const disableRule = (id: number) =>
  request('POST', `/api/agent/rules/${id}/disable`, { schema: s.Rule })

export type KbScope = 'classification' | 'decision'
export const kb = (scope?: KbScope) =>
  request('GET', '/api/agent/kb', { query: { scope }, schema: z.array(s.KbEntry) })
export const addKb = (scope: KbScope, text: string) =>
  request('POST', '/api/agent/kb', { body: { scope, text }, schema: s.KbEntry })
export const deactivateKb = (id: number) => request('DELETE', `/api/agent/kb/${id}`)
export const agentSettings = () => request('GET', '/api/agent/settings', { schema: s.AgentSettings })
export const setAgentSettings = (llm_mode: 'free' | 'fast' | null) =>
  request('PUT', '/api/agent/settings', { body: { llm_mode } })
export const audit = (limit = 200) =>
  request('GET', '/api/agent/audit', { query: { limit }, schema: z.array(s.Audit) })

// --- security -----------------------------------------------------------
export const securityEvents = (kind?: string) =>
  request('GET', '/api/security/events', { query: { kind }, schema: z.array(s.SecurityEvent) })
export const securityBlocks = () => request('GET', '/api/security/blocks', { schema: s.BlocksOut })
export const notifications = () =>
  request('GET', '/api/notifications', { schema: z.array(s.Notification) })

// --- chat ---------------------------------------------------------------
export const conversations = (limit = 30) =>
  request('GET', '/api/chat/conversations', { query: { limit }, schema: z.array(s.Conversation) })
export const newConversation = () =>
  request('POST', '/api/chat/conversations', { schema: s.Conversation })
export const chatMessages = (conversationId: number) =>
  request('GET', `/api/chat/conversations/${conversationId}/messages`, {
    schema: z.array(s.ChatMessage),
  })
export const sendText = (conversationId: number, text: string) =>
  request('POST', `/api/chat/conversations/${conversationId}/messages`, {
    body: { text },
    schema: s.Turn,
  })
export const sendAudio = (conversationId: number, wav: Blob) =>
  request('POST', `/api/chat/conversations/${conversationId}/audio`, {
    raw: wav,
    contentType: 'audio/wav',
    schema: s.Turn,
  })
export const retryTurn = (messageId: number) =>
  request('POST', `/api/chat/messages/${messageId}/retry`, { schema: s.Turn })
export interface ConfirmOptions {
  apply_existing?: boolean
  verdict?: 'approve' | 'reject'
}
export const confirmCard = (cardId: number, opts: ConfirmOptions = {}) =>
  request('POST', `/api/chat/cards/${cardId}/confirm`, { body: opts, schema: s.ChatCard })
export const cancelCard = (cardId: number) =>
  request('POST', `/api/chat/cards/${cardId}/cancel`, { schema: s.ChatCard })
export const messageAudioUrl = (messageId: number) => `/api/chat/messages/${messageId}/audio`
