import { z } from 'zod'

const PreparedAnswerSourceSchema = z.object({
  source_type: z.enum(['personal_document', 'resume', 'profile']),
  document_id: z.string().optional(), filename: z.string().optional(), evidence: z.string(),
}).strict()

// Stable program-owned events reject unknown fields. Only the two model-owned
// updates retain extensions and historical partial payloads; live producers
// validate their required fields before creating these events.
export const CopilotServerEventSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('started'), session_id: z.string(), audio_ready: z.boolean().optional() }).strict(),
  z.object({ type: z.literal('stopped') }).strict(),
  z.object({ type: z.literal('progress'), message: z.string() }).strict(),
  z.object({ type: z.literal('error'), message: z.string() }).strict(),
  z.object({ type: z.literal('asr_interim'), text: z.string(), role: z.enum(['hr', 'candidate']).optional() }).strict(),
  // Older clients/fixtures default a missing role to HR.
  z.object({ type: z.literal('asr_final'), text: z.string(), role: z.enum(['hr', 'candidate']).optional() }).strict(),
  z.object({
    type: z.literal('copilot_update'),
    utterance_id: z.string().optional(),
    intent: z.string(), tree_position: z.string().nullable(), topic: z.string(),
    // Cosine matching can report a negative score, including -1 for no match.
    confidence: z.number(), recommended_points: z.array(z.string()),
    children: z.array(z.object({ topic: z.string(), question: z.string() }).strict()),
    prep_hint: z.object({ safe_talking_points: z.array(z.string()), redirect_suggestion: z.string() }).strict().nullable(),
  }).strict(),
  z.object({ type: z.literal('risk_alert'), utterance_id: z.string().optional(), message: z.string(), node_id: z.string().nullable() }).strict(),
  z.object({ type: z.literal('answer_chunk'), utterance_id: z.string().optional(), text: z.string() }).strict(),
  z.object({
    type: z.literal('answer_meta'), utterance_id: z.string().optional(), first_token_ms: z.number(),
    source: z.enum(['prepared', 'llm_augmented', 'llm_fallback']), confidence: z.number().optional(),
    latency_ms: z.number().optional(), answer_id: z.string().optional(), matched_question: z.string().optional(),
    short_answer: z.string().optional(), sources: z.array(PreparedAnswerSourceSchema).optional(), warnings: z.array(z.string()).optional(),
  }).strict(),
  z.object({ type: z.literal('answer_done'), utterance_id: z.string().optional(), total_ms: z.number(), chunk_count: z.number().int().nonnegative() }).strict(),
  z.object({
    type: z.literal('hr_profile_update'), style: z.string().optional(), focus: z.string().optional(),
    satisfaction_signals: z.string().optional(), advice: z.string().optional(),
  }).passthrough(),
  z.object({
    type: z.literal('monitor_update'), phase: z.string().optional(), last_answer_feedback: z.string().optional(),
    covered_topics: z.array(z.string()).optional(), uncovered_topics: z.array(z.string()).optional(), strategy_tip: z.string().optional(),
  }).passthrough(),
]).meta({ id: 'CopilotServerEvent' })

export const CopilotServerEventTypeSchema = z.enum(CopilotServerEventSchema.options.map((event) => event.shape.type.value))
export type CopilotServerEvent = z.infer<typeof CopilotServerEventSchema>
