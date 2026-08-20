import { API_BASE, authFetch, type ApiResponse } from "./client";

/** 列出所有 Prep 会话 */
export async function listCopilotPreps(): Promise<
  ApiResponse<"/api/copilot/preps", "get">
> {
  const res = await authFetch(`${API_BASE}/copilot/preps`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/** 删除 Prep 会话 */
export async function deleteCopilotPrep(
  prepId: string
): Promise<ApiResponse<"/api/copilot/prep/{prep_id}", "delete">> {
  const res = await authFetch(`${API_BASE}/copilot/prep/${prepId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

interface StartCopilotPrepOptions {
  jdText: string;
  company?: string;
  position?: string;
  documentIds?: string[];
  materials?: Array<{ document_id: string; role: "authoritative_script" | "supporting_material" }>;
}

export interface PreparedKnowledge {
  version?: number | null;
  document_ids?: string[];
  source_snapshot?: Array<Record<string, unknown>>;
  prepared_answers: Record<string, Record<string, unknown>>;
  uncompiled_nodes: Array<Record<string, unknown>>;
  compile_stats: Record<string, number>;
  index_status?: string;
}

export interface PreparedMatchResult {
  matched: boolean;
  route: "prepared" | "miss";
  score: number;
  latency_ms: number;
  [key: string]: unknown;
}

/** 启动 Copilot Prep Phase */
export async function startCopilotPrep({
  jdText,
  company,
  position,
  documentIds = [],
  materials = [],
}: StartCopilotPrepOptions): Promise<ApiResponse<"/api/copilot/prep", "post">> {
  const form = new FormData();
  form.append("jd_text", jdText);
  if (company) form.append("company", company);
  if (position) form.append("position", position);
  form.append("document_ids", JSON.stringify(documentIds));
  form.append("materials", JSON.stringify(materials));

  const res = await authFetch(`${API_BASE}/copilot/prep`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/** 查询 Prep 进度 */
export async function getCopilotPrepStatus(
  prepId: string
): Promise<ApiResponse<"/api/copilot/prep/{prep_id}", "get">> {
  const res = await authFetch(`${API_BASE}/copilot/prep/${prepId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/** 获取策略树 */
export async function getCopilotStrategyTree(
  prepId: string
): Promise<ApiResponse<"/api/copilot/prep/{prep_id}/tree", "get">> {
  const res = await authFetch(`${API_BASE}/copilot/prep/${prepId}/tree`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function recompileCopilotPrep(
  prepId: string,
  materials: Array<{ document_id: string; role: "authoritative_script" | "supporting_material" }>,
): Promise<{ prep_id: string; knowledge_version: number }> {
  const res = await authFetch(`${API_BASE}/copilot/prep/${prepId}/recompile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_ids: materials.map((item) => item.document_id), materials }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateCopilotPreparedAnswer(
  prepId: string,
  answerId: string,
  changes: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const res = await authFetch(`${API_BASE}/copilot/prep/${prepId}/prepared-answers/${answerId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listCopilotSessions(prepId: string): Promise<Array<Record<string, unknown>>> {
  const res = await authFetch(`${API_BASE}/copilot/prep/${prepId}/sessions`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getCopilotSession(sessionId: string): Promise<Record<string, unknown>> {
  const res = await authFetch(`${API_BASE}/copilot/sessions/${sessionId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/** 获取只读的预编译问答包 */
export async function getCopilotPreparedAnswers(prepId: string): Promise<PreparedKnowledge> {
  const res = await authFetch(`${API_BASE}/copilot/prep/${prepId}/prepared-answers`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/** 在接入实时语音前手工验证 prepared matcher */
export async function testCopilotPreparedMatch(prepId: string, question: string): Promise<PreparedMatchResult> {
  const res = await authFetch(`${API_BASE}/copilot/prep/${prepId}/test-match`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
