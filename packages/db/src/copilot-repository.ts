import { Database } from 'bun:sqlite'
import type { CopilotPrepRecord, CopilotRepository, CopilotSessionState, PreparedVariant } from '@techspar/core'

type PrepRow = Omit<CopilotPrepRecord, 'result' | 'document_ids'> & { result: string | null; document_ids: string | null }
type SessionRow = Omit<CopilotSessionState, 'conversation'> & { conversation: string }
function json<T>(value: string | null | undefined, fallback: T): T { try { return value ? JSON.parse(value) as T : fallback } catch { return fallback } }
function prep(row: PrepRow): CopilotPrepRecord { return { ...row, document_ids: json<string[]>(row.document_ids, []), result: json<Record<string, unknown> | null>(row.result, null) } }
function session(row: SessionRow): CopilotSessionState { return { ...row, conversation: json(row.conversation, []) } }
function toBlob(vector: Float32Array): Uint8Array { const bytes = new Uint8Array(vector.length * 4); const view = new DataView(bytes.buffer); vector.forEach((value, index) => view.setFloat32(index * 4, value, true)); return bytes }
function fromBlob(bytes: Uint8Array): Float32Array { const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength); const values = new Float32Array(bytes.byteLength / 4); for (let index = 0; index < values.length; index += 1) values[index] = view.getFloat32(index * 4, true); return values }

export class BunCopilotRepository implements CopilotRepository {
  private readonly sqlite: Database
  constructor(path: string) { this.sqlite = new Database(path, { create: true }); this.sqlite.exec('PRAGMA journal_mode = WAL'); this.sqlite.exec('PRAGMA busy_timeout = 5000') }
  initialize(): void {
    this.sqlite.exec(`
      CREATE TABLE IF NOT EXISTS copilot_preps (
        prep_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, company TEXT DEFAULT '', position TEXT DEFAULT '', jd_text TEXT DEFAULT '',
        document_ids TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'running', progress TEXT DEFAULT '', error TEXT DEFAULT '', result TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
      );
      CREATE INDEX IF NOT EXISTS idx_copilot_preps_user ON copilot_preps(user_id);
      CREATE TABLE IF NOT EXISTS copilot_realtime_sessions (
        session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, prep_id TEXT NOT NULL, conversation TEXT NOT NULL DEFAULT '[]',
        last_node_id TEXT, turn_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE INDEX IF NOT EXISTS idx_copilot_realtime_user ON copilot_realtime_sessions(user_id, updated_at DESC);
      CREATE TABLE IF NOT EXISTS copilot_prepared_variants (
        variant_id TEXT PRIMARY KEY, prep_id TEXT NOT NULL, user_id TEXT NOT NULL, node_id TEXT NOT NULL, answer_id TEXT NOT NULL,
        question TEXT NOT NULL, intent TEXT NOT NULL DEFAULT 'unknown', polarity TEXT NOT NULL DEFAULT 'direct', embedding BLOB NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE INDEX IF NOT EXISTS idx_copilot_variants_scope ON copilot_prepared_variants(user_id, prep_id);
    `)
    const prepColumns = new Set(this.sqlite.query<{ name: string }, []>('PRAGMA table_info(copilot_preps)').all().map((row) => row.name))
    if (!prepColumns.has('document_ids')) this.sqlite.exec("ALTER TABLE copilot_preps ADD COLUMN document_ids TEXT NOT NULL DEFAULT '[]'")
  }
  async createPrep(input: { prepId: string; userId: string; company: string; position: string; jdText: string; documentIds?: string[] }): Promise<void> {
    this.sqlite.query("INSERT INTO copilot_preps (prep_id, user_id, company, position, jd_text, document_ids, status, progress, error) VALUES ($id, $userId, $company, $position, $jd, $documentIds, 'running', '初始化中...', '')").run({ $id: input.prepId, $userId: input.userId, $company: input.company, $position: input.position, $jd: input.jdText, $documentIds: JSON.stringify(input.documentIds || []) })
  }
  async getPrep(prepId: string, userId: string): Promise<CopilotPrepRecord | undefined> { const row = this.sqlite.query<PrepRow, { $id: string; $userId: string }>('SELECT * FROM copilot_preps WHERE prep_id = $id AND user_id = $userId').get({ $id: prepId, $userId: userId }); return row ? prep(row) : undefined }
  async listPreps(userId: string): Promise<CopilotPrepRecord[]> { return this.sqlite.query<PrepRow, { $userId: string }>('SELECT * FROM copilot_preps WHERE user_id = $userId ORDER BY created_at DESC, rowid DESC').all({ $userId: userId }).map(prep) }
  async updatePrepProgress(prepId: string, userId: string, progress: string): Promise<void> { this.sqlite.query("UPDATE copilot_preps SET status = 'running', progress = $progress, error = '' WHERE prep_id = $id AND user_id = $userId").run({ $progress: progress, $id: prepId, $userId: userId }) }
  async completePrep(prepId: string, userId: string, result: Record<string, unknown>): Promise<void> { this.sqlite.query("UPDATE copilot_preps SET status = 'done', progress = '准备完成', error = '', result = $result WHERE prep_id = $id AND user_id = $userId").run({ $result: JSON.stringify(result), $id: prepId, $userId: userId }) }
  async failPrep(prepId: string, userId: string, error: string): Promise<void> { this.sqlite.query("UPDATE copilot_preps SET status = 'error', error = $error WHERE prep_id = $id AND user_id = $userId").run({ $error: error, $id: prepId, $userId: userId }) }
  async deletePrep(prepId: string, userId: string): Promise<boolean> { return this.sqlite.transaction(() => { this.sqlite.query('DELETE FROM copilot_realtime_sessions WHERE prep_id = $id AND user_id = $userId').run({ $id: prepId, $userId: userId }); this.sqlite.query('DELETE FROM copilot_prepared_variants WHERE prep_id = $id AND user_id = $userId').run({ $id: prepId, $userId: userId }); return this.sqlite.query('DELETE FROM copilot_preps WHERE prep_id = $id AND user_id = $userId').run({ $id: prepId, $userId: userId }).changes > 0 })() }
  async replacePreparedVariants(input: { prepId: string; userId: string; variants: PreparedVariant[] }): Promise<void> {
    this.sqlite.transaction(() => {
      this.sqlite.query('DELETE FROM copilot_prepared_variants WHERE prep_id = $prepId AND user_id = $userId').run({ $prepId: input.prepId, $userId: input.userId })
      const statement = this.sqlite.query(`INSERT INTO copilot_prepared_variants (variant_id, prep_id, user_id, node_id, answer_id, question, intent, polarity, embedding, created_at)
        VALUES ($variantId, $prepId, $userId, $nodeId, $answerId, $question, $intent, $polarity, $embedding, $createdAt)`)
      const createdAt = new Date().toISOString()
      for (const variant of input.variants) statement.run({ $variantId: variant.variant_id, $prepId: input.prepId, $userId: input.userId, $nodeId: variant.node_id, $answerId: variant.answer_id, $question: variant.question, $intent: variant.intent, $polarity: variant.polarity, $embedding: toBlob(variant.embedding), $createdAt: createdAt })
    })()
  }
  async loadPreparedVariants(prepId: string, userId: string): Promise<PreparedVariant[]> {
    const rows = this.sqlite.query<{ variant_id: string; prep_id: string; node_id: string; answer_id: string; question: string; intent: string; polarity: 'negative' | 'direct'; embedding: Uint8Array }, { $prepId: string; $userId: string }>('SELECT variant_id, prep_id, node_id, answer_id, question, intent, polarity, embedding FROM copilot_prepared_variants WHERE prep_id = $prepId AND user_id = $userId ORDER BY rowid').all({ $prepId: prepId, $userId: userId })
    return rows.map((row) => ({ ...row, embedding: fromBlob(row.embedding) }))
  }
  async loadSession(sessionId: string, userId: string): Promise<CopilotSessionState | undefined> { const row = this.sqlite.query<SessionRow, { $id: string; $userId: string }>('SELECT * FROM copilot_realtime_sessions WHERE session_id = $id AND user_id = $userId').get({ $id: sessionId, $userId: userId }); return row ? session(row) : undefined }
  async saveSession(state: CopilotSessionState): Promise<void> {
    const owner = this.sqlite.query<{ user_id: string }, { $id: string }>('SELECT user_id FROM copilot_realtime_sessions WHERE session_id = $id').get({ $id: state.session_id })
    if (owner && owner.user_id !== state.user_id) throw new Error('Copilot session belongs to another user')
    this.sqlite.query(`INSERT INTO copilot_realtime_sessions (session_id, user_id, prep_id, conversation, last_node_id, turn_count, status, created_at, updated_at)
      VALUES ($id, $userId, $prepId, $conversation, $lastNodeId, $turnCount, $status, $createdAt, $updatedAt)
      ON CONFLICT(session_id) DO UPDATE SET prep_id = excluded.prep_id, conversation = excluded.conversation, last_node_id = excluded.last_node_id, turn_count = excluded.turn_count, status = excluded.status, updated_at = excluded.updated_at
      WHERE copilot_realtime_sessions.user_id = excluded.user_id`).run({ $id: state.session_id, $userId: state.user_id, $prepId: state.prep_id, $conversation: JSON.stringify(state.conversation), $lastNodeId: state.last_node_id || null, $turnCount: state.turn_count, $status: state.status, $createdAt: state.created_at, $updatedAt: state.updated_at })
  }
  close(): void { this.sqlite.close() }
}
