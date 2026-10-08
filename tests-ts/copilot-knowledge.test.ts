import { afterEach, describe, expect, test } from 'bun:test'
import { mkdtemp, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import {
  compileKnowledgePackage,
  CopilotRealtimeService,
  matchPreparedAnswer,
  sourceSnapshotState,
  type CompiledKnowledge,
  type CopilotDependencies,
  type PreparedAnswer,
  type PreparedVariant,
  type RequestContext,
} from '@techspar/core'
import { BunCopilotRepository } from '@techspar/db'

const directories: string[] = []
async function databasePath(): Promise<string> { const directory = await mkdtemp(join(tmpdir(), 'techspar-knowledge-')); directories.push(directory); return join(directory, 'techspar.db') }
afterEach(async () => { while (directories.length) await rm(directories.pop()!, { recursive: true, force: true }) })
const context: RequestContext = { requestId: 'knowledge-test', userId: 'user-a', signal: new AbortController().signal }

function answer(input: Partial<PreparedAnswer> & Pick<PreparedAnswer, 'answer_id' | 'node_id'>): PreparedAnswer {
  return {
    topic: input.node_id, intent: 'technical', question_variants: ['如何实现事件循环'],
    prepared_answer: '这是预编译答案', short_answer: '预编译短答', key_points: ['结论'], source_refs: [],
    confidence: 0.9, usable: true, warnings: [], ...input,
  }
}
function knowledge(...answers: PreparedAnswer[]): CompiledKnowledge {
  return {
    version: 2, document_ids: [], source_snapshot: [], source_fingerprint: 'empty',
    prepared_answers: Object.fromEntries(answers.map((item) => [item.answer_id, item])), uncompiled_nodes: [],
    compile_stats: { strategy_nodes: answers.length, compiled_answers: answers.length, question_variants: answers.reduce((total, item) => total + item.question_variants.length, 0), source_documents: 0 },
    index_status: 'ready',
  }
}
function variant(input: Partial<PreparedVariant> & Pick<PreparedVariant, 'variant_id' | 'node_id' | 'answer_id' | 'question' | 'embedding'>): PreparedVariant {
  return { prep_id: 'prep-1', intent: 'technical', polarity: 'direct', ...input }
}

describe('Prepared answer matcher reliability', () => {
  test('accepts an unambiguous direct match and rejects reversed intent without an LLM verifier', () => {
    const prepared = answer({ answer_id: 'a', node_id: 'node', question_variants: ['为什么使用共享库'] })
    const index = { knowledge: knowledge(prepared), variants: [variant({ variant_id: 'v', node_id: 'node', answer_id: 'a', question: '为什么使用共享库', embedding: Float32Array.from([1, 0]) })] }
    expect(matchPreparedAnswer(index, Float32Array.from([1, 0]), '为什么使用共享库')).toMatchObject({ matched: true, route: 'prepared', answer_id: 'a' })
    expect(matchPreparedAnswer(index, Float32Array.from([1, 0]), '为什么不使用共享库')).toMatchObject({ matched: false, route: 'miss', reason: 'intent_verification_failed' })
  })

  test('falls back when two same-topic answers are indistinguishable', () => {
    const first = answer({ answer_id: 'a', node_id: 'a-node', question_variants: ['解释方案甲'] })
    const second = answer({ answer_id: 'b', node_id: 'b-node', question_variants: ['解释方案乙'] })
    const result = matchPreparedAnswer({ knowledge: knowledge(first, second), variants: [
      variant({ variant_id: 'a-v', node_id: 'a-node', answer_id: 'a', question: '解释方案甲', embedding: Float32Array.from([1, 0]) }),
      variant({ variant_id: 'b-v', node_id: 'b-node', answer_id: 'b', question: '解释方案乙', embedding: Float32Array.from([1, 0]) }),
    ] }, Float32Array.from([1, 0]), '请详细说明')
    expect(result).toMatchObject({ matched: false, route: 'partial', reason: 'ambiguous_candidates' })
  })

  test('uses the previous node only for a contextual follow-up', () => {
    const previous = answer({ answer_id: 'previous', node_id: 'previous' })
    const other = answer({ answer_id: 'other', node_id: 'other' })
    const result = matchPreparedAnswer({ knowledge: knowledge(previous, other), variants: [
      variant({ variant_id: 'previous-v', node_id: 'previous', answer_id: 'previous', question: '项目为什么采用队列', embedding: Float32Array.from([0.75, Math.sqrt(1 - 0.75 ** 2)]) }),
      variant({ variant_id: 'other-v', node_id: 'other', answer_id: 'other', question: '其他技术问题', embedding: Float32Array.from([0.82, Math.sqrt(1 - 0.82 ** 2)]) }),
    ] }, Float32Array.from([1, 0]), '为什么要这么做？', 'previous')
    expect(result).toMatchObject({ matched: true, route: 'prepared', node_id: 'previous' })
  })
})

describe('Prepared answer persistence and source isolation', () => {
  test('persists by user and prep and deletes the index with its Prep', async () => {
    const repository = new BunCopilotRepository(await databasePath()); repository.initialize()
    try {
      await repository.createPrep({ prepId: 'prep-1', userId: 'user-a', company: '', position: '', jdText: 'JD' })
      const row = variant({ variant_id: 'v', node_id: 'node', answer_id: 'a', question: '问题', embedding: Float32Array.from([1, 0]) })
      await repository.replacePreparedVariants({ prepId: 'prep-1', userId: 'user-a', variants: [row] })
      expect(await repository.loadPreparedVariants('prep-1', 'user-a')).toHaveLength(1)
      expect(await repository.loadPreparedVariants('prep-1', 'user-b')).toHaveLength(0)
      await repository.deletePrep('prep-1', 'user-a')
      expect(await repository.loadPreparedVariants('prep-1', 'user-a')).toHaveLength(0)
    } finally { repository.close() }
  })

  test('marks a snapshot stale after the selected document changes', async () => {
    const state = await sourceSnapshotState({ materials: {
      async getDocument() { return { document_id: 'doc', filename: 'notes.md', extension: '.md', size_bytes: 1, status: 'ready', chunk_count: 1, created_at: 't1', updated_at: 't2' } },
    } as unknown as CopilotDependencies['materials'] }, 'user-a', [{ document_id: 'doc', filename: 'notes.md', updated_at: 't1' }])
    expect(state).toBe('stale')
  })
})

describe('Knowledge compiler degradation', () => {
  test('keeps successful nodes and drops references outside the selected set', async () => {
    const deps = {
      embeddings: { async embed(_context: RequestContext, texts: readonly string[]) { return texts.map(() => Float32Array.from([1, 0])) } },
      materials: { async searchDocuments() { return [{ document_id: 'selected', source: 'selected.md', content: '项目证据', score: 1 }] } },
      ai: { async complete(_context: RequestContext, messages: Array<{ content: string }>) {
        const prompt = messages.at(-1)?.content || ''
        if (prompt.includes('Broken')) throw new Error('node failed')
        return JSON.stringify({ question_variants: ['怎么实现的'], prepared_answer: '基于所选资料回答', short_answer: '简答', key_points: ['证据'], source_refs: [
          { source_type: 'personal_document', document_id: 'selected', evidence: '项目证据' },
          { source_type: 'personal_document', document_id: 'foreign', evidence: '越界资料' },
        ], confidence: 0.9, warnings: [] })
      } },
    } as unknown as CopilotDependencies
    const result = await compileKnowledgePackage({
      deps, context, prepId: 'prep-1', jdText: 'JD', fitReport: {}, prepHints: [], resumeContext: '简历', profileSummary: '画像',
      strategyTree: { nodes: {
        good: { topic: 'Project', intent: 'project', sample_questions: ['介绍项目'] },
        broken: { topic: 'Broken', intent: 'technical', sample_questions: ['解释失败节点'] },
      } },
      documents: [{ document_id: 'selected', filename: 'selected.md', extension: '.md', size_bytes: 1, status: 'ready', chunk_count: 1, created_at: 't', updated_at: 't' }],
    })
    expect(result.compile_stats.compiled_answers).toBe(1)
    expect(result.uncompiled_nodes).toEqual([{ node_id: 'broken', error: 'node failed' }])
    const compiled = Object.values(result.prepared_answers)[0]!
    expect(compiled.source_refs.map((source) => source.document_id)).toEqual(['selected'])
    expect(compiled.warnings.some((warning) => warning.includes('未选择'))).toBeTrue()
  })
})

describe('Prepared Answer First realtime route', () => {
  async function fixture() {
    const repository = new BunCopilotRepository(await databasePath()); repository.initialize()
    const prepared = answer({ answer_id: 'a', node_id: 'tech', topic: '事件循环', question_variants: ['如何实现事件循环'] })
    const compiled = knowledge(prepared)
    await repository.createPrep({ prepId: 'ready', userId: 'user-a', company: '', position: '', jdText: 'JD' })
    await repository.completePrep('ready', 'user-a', { compiled_knowledge: compiled, question_strategy_tree: { nodes: { tech: { topic: '事件循环', intent: 'technical', sample_questions: ['如何实现事件循环'], recommended_points: [], children: [], risk_level: 'safe' } } } })
    await repository.replacePreparedVariants({ prepId: 'ready', userId: 'user-a', variants: [variant({ variant_id: 'v', node_id: 'tech', answer_id: 'a', question: '如何实现事件循环', embedding: Float32Array.from([1, 0]) })] })
    let streams = 0
    const deps = {
      repository,
      embeddings: { async embed(_context: RequestContext, texts: readonly string[]) { return texts.map((text) => text.includes('unrelated') ? Float32Array.from([0, 1]) : Float32Array.from([1, 0])) } },
      materials: { async getDocument() { return undefined }, async listDocuments() { return [] }, async searchDocuments() { return [] } },
      settings: { async loadProvider() { return { services: { dashscope_api_key: '', tavily_api_key: '', oss_access_key_id: '', oss_access_key_secret: '', oss_bucket: '', oss_endpoint: '' } } } },
      asr: { create() { return { async start() {}, sendAudio() { return true }, async stop() {} } } },
      ai: { async complete() { return '{}' }, async *stream() { streams += 1; yield 'LLM fallback' } },
    } as unknown as CopilotDependencies
    return { repository, deps, streams: () => streams }
  }

  test('returns a reliable prepared answer without calling generation', async () => {
    const f = await fixture(); const events: Array<Record<string, unknown>> = []
    const connection = new CopilotRealtimeService(f.deps).connect(context, 'live-prepared', async (event) => { events.push(event) })
    try {
      await connection.handle({ type: 'start', prep_id: 'ready' })
      await connection.handle({ type: 'manual', text: '如何实现事件循环' })
      expect(f.streams()).toBe(0)
      expect(events).toContainEqual(expect.objectContaining({ type: 'answer_meta', source: 'prepared', answer_id: 'a' }))
      expect(events).toContainEqual(expect.objectContaining({ type: 'answer_chunk', text: '这是预编译答案' }))
    } finally { await connection.close(); f.repository.close() }
  })

  test('uses the original Answer Coach when there is no reliable match', async () => {
    const f = await fixture(); const events: Array<Record<string, unknown>> = []
    const connection = new CopilotRealtimeService(f.deps).connect(context, 'live-fallback', async (event) => { events.push(event) })
    try {
      await connection.handle({ type: 'start', prep_id: 'ready' })
      await connection.handle({ type: 'manual', text: 'unrelated topic' })
      expect(f.streams()).toBe(1)
      expect(events).toContainEqual(expect.objectContaining({ type: 'answer_meta', source: 'llm_fallback' }))
      expect(events).toContainEqual(expect.objectContaining({ type: 'answer_chunk', text: 'LLM fallback' }))
    } finally { await connection.close(); f.repository.close() }
  })
})
