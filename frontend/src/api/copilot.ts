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
}

export type PreparedKnowledge = ApiResponse<"/api/copilot/prep/{prep_id}/prepared-answers", "get">;
export type PreparedMatchResult = ApiResponse<"/api/copilot/prep/{prep_id}/test-match", "post">;

/** 启动 Copilot Prep Phase */
export async function startCopilotPrep({
  jdText,
  company,
  position,
  documentIds = [],
}: StartCopilotPrepOptions): Promise<ApiResponse<"/api/copilot/prep", "post">> {
  const form = new FormData();
  form.append("jd_text", jdText);
  if (company) form.append("company", company);
  if (position) form.append("position", position);
  form.append("document_ids", JSON.stringify(documentIds));

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
