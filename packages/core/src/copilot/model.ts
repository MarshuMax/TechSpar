export type CopilotPrepStatus = 'running' | 'done' | 'error'

export type CopilotPrepRecord = {
  prep_id: string
  user_id: string
  company: string
  position: string
  jd_text: string
  document_ids: string[]
  status: CopilotPrepStatus
  progress: string
  error: string
  result?: Record<string, unknown> | null
  created_at: string
}

export type CopilotSourceSnapshot = {
  document_id: string
  filename: string
  updated_at: string
}

export type PreparedAnswerSource = {
  source_type: 'personal_document' | 'resume' | 'profile'
  document_id?: string
  filename?: string
  evidence: string
}

export type PreparedAnswer = {
  answer_id: string
  node_id: string
  topic: string
  intent: string
  question_variants: string[]
  prepared_answer: string
  short_answer: string
  key_points: string[]
  source_refs: PreparedAnswerSource[]
  confidence: number
  usable: boolean
  warnings: string[]
}

export type CompiledKnowledge = {
  version: number
  document_ids: string[]
  source_snapshot: CopilotSourceSnapshot[]
  source_fingerprint: string
  prepared_answers: Record<string, PreparedAnswer>
  uncompiled_nodes: Array<{ node_id: string; error: string }>
  compile_stats: {
    strategy_nodes: number
    compiled_answers: number
    question_variants: number
    source_documents: number
  }
  index_status: 'pending' | 'ready' | 'error'
  index_error?: string
}

export type PreparedVariant = {
  variant_id: string
  prep_id: string
  node_id: string
  answer_id: string
  question: string
  intent: string
  polarity: 'negative' | 'direct'
  embedding: Float32Array
}

export type PreparedMatchCandidate = {
  score: number
  semantic_score: number
  lexical_score: number
  node_id: string
  answer_id: string
  matched_question: string
  compatible: boolean
}

export type PreparedMatchResult = {
  matched: boolean
  route: 'prepared' | 'partial' | 'miss'
  score: number
  semantic_score?: number
  reason?: string
  node_id?: string
  answer_id?: string
  matched_question?: string
  answer?: PreparedAnswer
  candidates: PreparedMatchCandidate[]
}

export type CopilotConversationTurn = { role: 'hr' | 'candidate'; text: string; at: string }

export type CopilotSessionState = {
  session_id: string
  user_id: string
  prep_id: string
  conversation: CopilotConversationTurn[]
  last_node_id?: string | null
  turn_count: number
  status: 'active' | 'stopped'
  created_at: string
  updated_at: string
}

export type CopilotClientMessage =
  | { type: 'start'; prep_id?: string; audio_mode?: 'dual' }
  | { type: 'manual'; text?: string }
  | { type: 'candidate_response'; text: string }
  | { type: 'stop' }

export type CopilotServerEvent =
  | { type: 'started'; session_id: string; audio_ready?: boolean }
  | { type: 'stopped' }
  | { type: 'progress'; message: string }
  | { type: 'error'; message: string }
  | { type: 'asr_interim'; text: string; role?: 'hr' | 'candidate' }
  | { type: 'asr_final'; text: string; role?: 'hr' | 'candidate' }
  | { type: 'copilot_update'; utterance_id?: string; intent: string; tree_position: string | null; topic: string; confidence: number; recommended_points: string[]; children: Array<{ topic: string; question: string }>; prep_hint: { safe_talking_points: string[]; redirect_suggestion: string } | null }
  | { type: 'risk_alert'; utterance_id?: string; message: string; node_id: string | null }
  | { type: 'answer_chunk'; utterance_id?: string; text: string }
  | { type: 'answer_meta'; utterance_id?: string; first_token_ms: number; source: 'prepared' | 'llm_augmented' | 'llm_fallback'; confidence?: number; latency_ms?: number; answer_id?: string; matched_question?: string; short_answer?: string; sources?: PreparedAnswerSource[]; warnings?: string[] }
  | { type: 'answer_done'; utterance_id?: string; total_ms: number; chunk_count: number }
  // Transport compatibility permits partial historical events. Live producers
  // validate all required model fields before creating these events.
  | { type: 'hr_profile_update'; style?: string; focus?: string; satisfaction_signals?: string; advice?: string; [key: string]: unknown }
  | { type: 'monitor_update'; phase?: string; last_answer_feedback?: string; covered_topics?: string[]; uncovered_topics?: string[]; strategy_tip?: string; [key: string]: unknown }
