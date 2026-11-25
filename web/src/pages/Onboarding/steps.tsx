// The registry of onboarding steps. The server owns the order (ONBOARDING_STEPS in
// src/auth/onboarding.py); a later phase adds its page here under the same name
// and nothing else in this folder changes.
import type { ComponentType } from 'react'
import type { OnboardingState } from '../../api/schemas'
import { GmailStep } from './GmailStep'
import { LlmKeyForm } from './LlmKey'
import { PasswordStep } from './PasswordStep'
import { TotpStep } from './TotpStep'

export interface StepProps {
  state: OnboardingState
  /** Re-read the state from the server (the step just advanced). */
  onAdvance: () => void
}

export interface StepInfo {
  /** Short name in the progress bar. */
  label: string
  Page: ComponentType<StepProps>
}

export const STEP_PAGES: Record<string, StepInfo> = {
  gmail: { label: 'Gmail', Page: GmailStep },
  totp: { label: 'Proteção da conta', Page: TotpStep },
  password_optional: { label: 'Senha (opcional)', Page: PasswordStep },
  llm_key: { label: 'Chave do Gemini', Page: ({ onAdvance }) => <LlmKeyForm onContinue={onAdvance} /> },
}
