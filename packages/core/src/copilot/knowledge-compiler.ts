import { AppError } from '../kernel/errors.ts'
import { parseJsonResponse } from '../kernel/json.ts'
import type { RequestContext } from '../kernel/context.ts'
import type { PersonalDocument } from '../personal-agent/model.ts'
import type { CompiledKnowledge, CopilotSourceSnapshot, PreparedAnswer, PreparedAnswerSource, PreparedVariant } from './model.ts'
import type { CopilotDependencies } from './ports.ts'
import { COPILOT_KNOWLEDGE_PROMPT } from './prompts.ts'
import { questionPolarity } from './prepared-answer-matcher.ts'
import { fill } from '../interview/prompts.ts'
import { STRUCTURED_CHAT_OPTIONS } from '../provider/ports.ts'

const COMPILER_CONCURRENCY = 3

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
function strings(value: unknown, limit = 8): string[] {
  return Array.isArray(value) ? [...new Set(value.map(String).map((item) => item.trim()).filter(Boolean))].slice(0, limit) : []
}
function stableId(prefix: string, value: string): string {
  let hash = 0x811c9dc5
  for (const character of value) { hash ^= character.codePointAt(0) || 0; hash = Math.imul(hash, 0x01000193) }
  return `${prefix}_${(hash >>> 0).toString(16).padStart(8, '0')}`
}
function json(value: unknown): string { return JSON.stringify(value, null, 2) }

export async function selectedDocuments(
  deps: Pick<CopilotDependencies, 'materials'>,
  userId: string,
  rawIds: readonly string[],
): Promise<PersonalDocument[]> {
  const ids = [...new Set(rawIds.map((value) => value.trim()).filter(Boolean))]
  if (ids.length > 50) throw new AppError('一次最多选择 50 份面试资料', 400)
  const values = await Promise.all(ids.map((documentId) => deps.materials.getDocument(documentId, userId)))
  const invalid = ids.filter((_documentId, index) => !values[index] || values[index]!.status !== 'ready')
  if (invalid.length) throw new AppError(`资料不存在、无权访问或尚未完成索引: ${invalid.join(', ')}`, 400)
  return values as PersonalDocument[]
}

export function snapshotOf(documents: readonly PersonalDocument[]): CopilotSourceSnapshot[] {
  return documents.map((document) => ({
    document_id: document.document_id,
    filename: document.filename,
    updated_at: document.updated_at || '',
  }))
}

export async function fingerprint(snapshot: readonly CopilotSourceSnapshot[]): Promise<string> {
  const value = [...snapshot]
    .sort((left, right) => left.document_id.localeCompare(right.document_id))
    .map((item) => `${item.document_id}\0${item.filename}\0${item.updated_at}`)
    .join('\n')
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

export async function sourceSnapshotState(
  deps: Pick<CopilotDependencies, 'materials'>,
  userId: string,
  snapshot: readonly CopilotSourceSnapshot[],
): Promise<'current' | 'stale'> {
  if (!snapshot.length) return 'current'
  const current = await Promise.all(snapshot.map((item) => deps.materials.getDocument(item.document_id, userId)))
  return current.every((document, index) => document?.status === 'ready' && document.updated_at === snapshot[index]!.updated_at)
    ? 'current'
    : 'stale'
}

function normalizedAnswer(
  raw: Record<string, unknown>,
  nodeId: string,
  node: Record<string, unknown>,
  documents: ReadonlyMap<string, PersonalDocument>,
): PreparedAnswer {
  const warnings = strings(raw.warnings, 12)
  const sourceRefs: PreparedAnswerSource[] = []
  for (const value of Array.isArray(raw.source_refs) ? raw.source_refs : []) {
    const source = object(value)
    const sourceType = String(source.source_type || '')
    if (sourceType === 'personal_document') {
      const document = documents.get(String(source.document_id || ''))
      if (!document) { warnings.push('模型返回了未选择的资料引用，已移除'); continue }
      sourceRefs.push({ source_type: 'personal_document', document_id: document.document_id, filename: document.filename, evidence: String(source.evidence || '').slice(0, 500) })
    } else if (sourceType === 'resume' || sourceType === 'profile') {
      sourceRefs.push({ source_type: sourceType, evidence: String(source.evidence || '').slice(0, 500) })
    }
  }
  const variants = [...new Set([...strings(node.sample_questions), ...strings(raw.question_variants)].map((value) => value.trim()).filter(Boolean))].slice(0, 8)
  const preparedAnswer = String(raw.prepared_answer || '').trim()
  const shortAnswer = String(raw.short_answer || '').trim()
  const numericConfidence = Number(raw.confidence || 0)
  return {
    answer_id: stableId('pa', nodeId), node_id: nodeId, topic: String(node.topic || ''), intent: String(node.intent || 'unknown'),
    question_variants: variants, prepared_answer: preparedAnswer, short_answer: shortAnswer,
    key_points: strings(raw.key_points), source_refs: sourceRefs,
    confidence: Number.isFinite(numericConfidence) ? Math.max(0, Math.min(1, numericConfidence)) : 0,
    usable: Boolean(preparedAnswer && variants.length), warnings: [...new Set(warnings)],
  }
}

async function mapConcurrent<T, R>(values: readonly T[], limit: number, worker: (value: T, index: number) => Promise<R>): Promise<R[]> {
  const output = new Array<R>(values.length)
  let cursor = 0
  await Promise.all(Array.from({ length: Math.min(limit, values.length) }, async () => {
    while (cursor < values.length) {
      const index = cursor; cursor += 1
      output[index] = await worker(values[index]!, index)
    }
  }))
  return output
}

export async function compileKnowledgePackage(input: {
  deps: CopilotDependencies
  context: RequestContext
  prepId: string
  jdText: string
  strategyTree: Record<string, unknown>
  fitReport: Record<string, unknown>
  prepHints: Array<Record<string, unknown>>
  resumeContext: string
  profileSummary: string
  documents: PersonalDocument[]
  onProgress?: (completed: number, total: number) => Promise<void>
}): Promise<CompiledKnowledge> {
  const nodes = Object.entries(object(input.strategyTree.nodes)).flatMap(([nodeId, value]) => {
    const node = object(value)
    return strings(node.sample_questions).length ? [{ nodeId, node }] : []
  })
  const documents = new Map(input.documents.map((document) => [document.document_id, document]))
  const hints = new Map(input.prepHints.map((hint) => [String(hint.node_id || ''), hint]))
  let completed = 0
  const results = await mapConcurrent(nodes, COMPILER_CONCURRENCY, async ({ nodeId, node }) => {
    try {
      const query = [String(node.topic || ''), ...strings(node.sample_questions), ...strings(node.recommended_points)].join('；')
      let materialContext = '本节点没有命中所选个人资料'
      if (input.documents.length) {
        const [queryVector] = await input.deps.embeddings.embed(input.context, [query])
        const hits = queryVector ? await input.deps.materials.searchDocuments(input.context.userId!, queryVector, 6, input.documents.map((document) => document.document_id)) : []
        materialContext = hits.map((hit) => `[document_id=${hit.document_id} source=${hit.source}]\n${hit.content}`).join('\n\n---\n\n').slice(0, 9000) || materialContext
      }
      const generated = await input.deps.ai.complete(input.context, [
        { role: 'system', content: '你是面试知识编译器。只返回 JSON。' },
        { role: 'user', content: fill(COPILOT_KNOWLEDGE_PROMPT, {
          jd_text: input.jdText.slice(0, 5000), node: json(node), fit_report: json(input.fitReport).slice(0, 4000),
          risk_hint: json(hints.get(nodeId) || {}).slice(0, 2000), resume_context: input.resumeContext.slice(0, 5000),
          profile_summary: input.profileSummary.slice(0, 3000), material_context: materialContext,
        }) },
      ], STRUCTURED_CHAT_OPTIONS)
      const answer = normalizedAnswer(object(parseJsonResponse(generated)), nodeId, node, documents)
      return answer.usable ? { answer } : { nodeId, error: '编译结果缺少答案或问题变体' }
    } catch (error) {
      return { nodeId, error: error instanceof Error ? error.message.slice(0, 500) : String(error).slice(0, 500) }
    } finally {
      completed += 1
      await input.onProgress?.(completed, nodes.length)
    }
  })
  const answers = results.flatMap((result) => 'answer' in result && result.answer ? [result.answer] : [])
  const snapshot = snapshotOf(input.documents)
  return {
    version: 2,
    document_ids: input.documents.map((document) => document.document_id),
    source_snapshot: snapshot,
    source_fingerprint: await fingerprint(snapshot),
    prepared_answers: Object.fromEntries(answers.map((answer) => [answer.answer_id, answer])),
    uncompiled_nodes: results.flatMap((result) => 'error' in result ? [{ node_id: result.nodeId!, error: result.error! }] : []),
    compile_stats: {
      strategy_nodes: nodes.length, compiled_answers: answers.length,
      question_variants: answers.reduce((total, answer) => total + answer.question_variants.length, 0),
      source_documents: input.documents.length,
    },
    index_status: 'pending',
  }
}

export async function buildPreparedVariants(input: {
  context: RequestContext
  deps: Pick<CopilotDependencies, 'embeddings'>
  prepId: string
  knowledge: CompiledKnowledge
}): Promise<PreparedVariant[]> {
  const values = Object.values(input.knowledge.prepared_answers).flatMap((answer) => answer.usable
    ? answer.question_variants.map((question, index) => ({ answer, question, index }))
    : [])
  if (!values.length) return []
  const vectors = await input.deps.embeddings.embed(input.context, values.map((value) => value.question))
  return values.map((value, index) => ({
    variant_id: stableId('pv', `${input.prepId}\0${value.answer.answer_id}\0${value.index}\0${value.question}`),
    prep_id: input.prepId, node_id: value.answer.node_id, answer_id: value.answer.answer_id,
    question: value.question, intent: value.answer.intent, polarity: questionPolarity(value.question), embedding: vectors[index]!,
  }))
}
