import type {
  CompiledKnowledge,
  PreparedAnswer,
  PreparedMatchCandidate,
  PreparedMatchResult,
  PreparedVariant,
} from './model.ts'

export type PreparedAnswerIndex = {
  knowledge: CompiledKnowledge
  variants: PreparedVariant[]
}

const NEGATIVE_CUES = [
  '不应该', '不需要', '不能', '不会', '不是', '没有', '没用', '不用', '不选', '不采用',
  '缺点', '失败', '反对', '风险', '最差', '避免', '放弃', 'why not', 'didn\'t',
  'cannot', 'can\'t', 'without', 'disadvantage', 'failure', '不', '没',
]
const FOLLOW_UP_CUES = [
  '为什么这么', '为什么这样', '为什么要这么', '为什么要这样', '这么做', '这样做', '具体呢',
  '然后呢', '后来呢', '怎么实现的', '遇到了什么', '如何解决的', '再说说', '它呢',
  'what about that', 'why do that', 'how exactly', 'and then',
]
const INTENT_CUES: Record<string, string[]> = {
  project: ['项目', '经历', '负责', '上线', '成果', '案例', '架构'],
  behavioral: ['团队', '冲突', '压力', '失败', '困难', '协作', '沟通'],
  technical: ['原理', '机制', '底层', '区别', '实现', '算法', '协议', '性能'],
  greeting: ['你好', '自我介绍', '介绍一下自己'],
  reverse_qa: ['有什么问题', '想问我们', '反问'],
}

function normalized(value: string): string {
  return value.toLowerCase().replace(/[\s\p{P}\p{S}]+/gu, '')
}

function features(value: string): Set<string> {
  const raw = value.toLowerCase()
  const compact = normalized(value)
  const output = new Set(raw.match(/[a-z0-9_+#.-]{2,}/g) || [])
  for (let index = 0; index < compact.length - 1; index += 1) output.add(compact.slice(index, index + 2))
  return output
}

function lexical(left: string, right: string): number {
  const a = features(left); const b = features(right)
  if (!a.size || !b.size) return 0
  let overlap = 0
  for (const token of a) if (b.has(token)) overlap += 1
  return overlap / Math.max(1, Math.min(a.size, b.size))
}

function cosine(left: Float32Array, right: Float32Array): number {
  if (!left.length || left.length !== right.length) return -1
  let dot = 0; let a = 0; let b = 0
  for (let index = 0; index < left.length; index += 1) {
    const l = left[index]!; const r = right[index]!
    dot += l * r; a += l * l; b += r * r
  }
  return a && b ? dot / Math.sqrt(a * b) : -1
}

export function questionPolarity(value: string): 'negative' | 'direct' {
  const text = value.toLowerCase()
  return NEGATIVE_CUES.some((cue) => text.includes(cue)) ? 'negative' : 'direct'
}

export function isContextualFollowUp(value: string): boolean {
  const text = value.toLowerCase().replace(/\s+/g, ' ').trim()
  return normalized(text).length <= 40 && FOLLOW_UP_CUES.some((cue) => text.includes(cue))
}

function explicitIntent(value: string): string | undefined {
  const text = value.toLowerCase()
  let best: { intent?: string; hits: number } = { hits: 0 }
  for (const [intent, cues] of Object.entries(INTENT_CUES)) {
    const hits = cues.filter((cue) => text.includes(cue)).length
    if (hits > best.hits) best = { intent, hits }
  }
  return best.intent
}

function answerFor(knowledge: CompiledKnowledge, answerId: string): PreparedAnswer | undefined {
  const answer = knowledge.prepared_answers[answerId]
  return answer?.usable && answer.prepared_answer ? answer : undefined
}

function rounded(value: number): number { return Math.round(value * 10_000) / 10_000 }

export function matchPreparedAnswer(
  index: PreparedAnswerIndex | undefined,
  queryVector: Float32Array | undefined,
  utterance: string,
  lastNodeId?: string | null,
): PreparedMatchResult {
  if (!index?.variants.length) return { matched: false, route: 'miss', score: 0, reason: 'index_missing', candidates: [] }
  if (!queryVector?.length) return { matched: false, route: 'miss', score: 0, reason: 'embedding_unavailable', candidates: [] }
  const followUp = Boolean(lastNodeId && isContextualFollowUp(utterance))
  const polarity = questionPolarity(utterance)
  const intent = explicitIntent(utterance)
  const byAnswer = new Map<string, PreparedMatchCandidate>()
  for (const variant of index.variants) {
    const answer = answerFor(index.knowledge, variant.answer_id)
    if (!answer || variant.embedding.length !== queryVector.length) continue
    const semantic = cosine(queryVector, variant.embedding)
    const lexicalScore = lexical(utterance, variant.question)
    const polarityCompatible = polarity === variant.polarity
    const intentCompatible = !intent || !answer.intent || answer.intent === 'unknown' || answer.intent === intent
    const contextCompatible = !followUp || variant.node_id === lastNodeId
    const compatible = polarityCompatible && intentCompatible && contextCompatible
    const contextAdjustment = followUp ? (variant.node_id === lastNodeId ? 0.08 : -0.08) : 0
    const score = Math.min(1, semantic + lexicalScore * 0.05 + contextAdjustment)
    const candidate: PreparedMatchCandidate = {
      score: rounded(score), semantic_score: rounded(semantic), lexical_score: rounded(lexicalScore),
      node_id: variant.node_id, answer_id: variant.answer_id, matched_question: variant.question, compatible,
    }
    const current = byAnswer.get(variant.answer_id)
    if (!current || candidate.score > current.score) byAnswer.set(variant.answer_id, candidate)
  }
  const candidates = [...byAnswer.values()].sort((left, right) => right.score - left.score).slice(0, 5)
  const best = candidates[0]
  if (!best) return { matched: false, route: 'miss', score: 0, reason: 'embedding_dimension_mismatch', candidates: [] }
  const runnerUp = candidates.find((candidate) => candidate.answer_id !== best.answer_id)
  const margin = runnerUp ? best.score - runnerUp.score : 1
  const answer = answerFor(index.knowledge, best.answer_id)
  const reliable = followUp
    ? best.compatible && best.node_id === lastNodeId && best.semantic_score >= 0.72 && best.score >= 0.8 && margin >= 0.02
    : best.compatible && best.semantic_score >= 0.82 && best.score >= 0.82 && margin >= 0.035 && (best.lexical_score >= 0.06 || best.semantic_score >= 0.9)
  if (reliable && answer) return {
    matched: true, route: 'prepared', score: best.score, semantic_score: best.semantic_score,
    node_id: best.node_id, answer_id: best.answer_id, matched_question: best.matched_question, answer, candidates,
  }
  if (best.compatible && best.semantic_score >= 0.62 && answer) return {
    matched: false, route: 'partial', score: best.score, semantic_score: best.semantic_score,
    reason: margin < 0.035 ? 'ambiguous_candidates' : 'below_reliable_threshold', node_id: best.node_id,
    answer_id: best.answer_id, matched_question: best.matched_question, answer, candidates,
  }
  return {
    matched: false, route: 'miss', score: best.score, semantic_score: best.semantic_score,
    reason: best.compatible ? 'below_threshold' : 'intent_verification_failed', candidates,
  }
}
