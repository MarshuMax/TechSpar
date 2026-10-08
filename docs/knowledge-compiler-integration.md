# Interview Knowledge Compiler 集成说明

本分支以原功能分支 `agent/interview-copilot-knowledge-compiler` 为第一父提交，合并公开上游
`AnnaSuSu/TechSpar@dac79307ae129d54b9f74825a3570cc42a5aa775`，再把原 Python Knowledge
Compiler 的业务语义迁移到 TypeScript / Bun / Hono / Electron 架构。旧 Python 运行时不再参与执行。

## 功能与模块映射

| 原 Python 能力 | TypeScript 实现 | 迁移结果 |
| --- | --- | --- |
| `material_context.py` | `MaterialSelector.jsx`、`PersonalDocumentRepository` 的受限检索 | Prep 创建前验证所属用户和 `ready` 状态；检索只接受本次选中的文档 ID |
| `knowledge_compiler.py` | `packages/core/src/copilot/knowledge-compiler.ts` | 按策略节点并发编译完整/简短答案、问题变体、要点、来源、置信度和告警；单节点失败降级 |
| `prepared_answer_index.py` | `copilot_prepared_variants` SQLite 表、`buildPreparedVariants` | 用户 + Prep 双重隔离，向量持久化、重启加载、Prep 删除级联清理 |
| `prepared_answer_matcher.py` | `packages/core/src/copilot/prepared-answer-matcher.ts` | 语义分、词法重排、极性、意图、候选间隔和追问上下文联合判定 |
| `answer_advisor.py` 的路由 | `packages/core/src/copilot/realtime-service.ts` | `Prepared Answer -> LLM Augmented -> LLM Fallback`；可靠命中不调用 LLM |
| Prepared Answers UI | `PreparedAnswersView.jsx`、`CopilotPanel.jsx`、`RealtimePhase.jsx` | 展示答案、变体、依据、未编译节点、来源、分数及延迟 |

## Prep 数据流

创建 Prep 时前端把资料 ID 作为 `document_ids` 提交。服务端逐个以当前用户查询，并拒绝不存在、
不属于当前用户或尚未完成索引的资料。策略树和 Knowledge Compiler 只能通过该 ID 集合检索资料，
模型返回的未选资料引用会被移除并写入 warning。

编译器以并发度 3 处理带示例问题的策略节点。一个节点生成或解析失败时只进入
`uncompiled_nodes`，其他答案与 Prep 结果仍然可用。问题变体在编译完成后批量生成 Embedding；
索引失败会把 `index_status` 标为 `error`，不会抹掉已生成的知识包。

知识包记录资料的 ID、文件名和 `updated_at` 快照，并计算 SHA-256 指纹。资料被更新、删除或变为
非 ready 后，读取知识包会返回 `source_state: stale`；实时阶段不会加载过期索引，而是安全回退到
原有 Answer Coach。删除 Prep 在同一 SQLite 事务中删除实时会话和问题变体索引。

## 实时匹配与取消语义

每个最终 HR 转写只生成一个新的 `utterance_id`。新问题会中止上一题的 `AbortController`；所有
Embedding、流式 token 和完成事件在发送前都检查同一取消信号，前端也按 `utterance_id` 忽略过期
事件，避免旧答案覆盖新答案。

匹配只计算一次问题 Embedding，并按答案聚合候选：

1. 可靠命中：普通问题要求语义分至少 `0.82`、综合分至少 `0.82`、候选间隔至少 `0.035`，且满足
   极性和显式意图校验；词法证据不足时语义分须达到 `0.90`。直接返回 Prepared Answer，不调用 LLM。
2. 上下文追问：只允许命中上一个策略节点，要求语义分至少 `0.72`、综合分至少 `0.80` 和候选间隔
   至少 `0.02`。
3. 部分匹配：兼容候选的语义分达到 `0.62`，把预编译内容作为有边界的参考交给 LLM 改写。
4. 无可靠匹配、资料过期、索引缺失或 Embedding 不可用：使用原有 LLM Answer Coach。

否定/反向表达与正向问题的极性必须一致；同主题下两个候选过近时不会走快速直出路径。路由结果通过
`answer_meta.source` 标为 `prepared`、`llm_augmented` 或 `llm_fallback`，并可携带置信度、命中问题、
来源和延迟。

## API

- `POST /api/copilot/prep`：表单字段 `document_ids` 为 JSON 字符串数组；省略时保持无个人资料的兼容流程。
- `GET /api/copilot/prep/{prep_id}/prepared-answers`：读取知识包、索引状态和资料新鲜度。
- `POST /api/copilot/prep/{prep_id}/test-match`：只运行匹配诊断，不生成答案。
- `DELETE /api/copilot/prep/{prep_id}`：删除 Prep、实时状态和派生索引。

所有路径都从鉴权上下文取得用户 ID，不接受客户端提供所有者 ID。

## 配置与启动

需要 Bun `1.3.14`。复制 `.env.example` 后执行：

```bash
bun install --frozen-lockfile
cp .env.example .env
bun run dev:desktop
```

也可分别运行 `bun run dev:api` 与 `bun run dev:web`。登录后在设置页配置：

- OpenAI-compatible LLM：Prep、Knowledge Compiler、部分匹配和 Fallback；
- Embedding API 或本地 Transformers.js/ONNX：问题变体索引和实时匹配；
- DashScope API Key：Qwen 双路流式 ASR；
- Tavily API Key：可选的公司情报搜索。

部署方也可在私有 `.env` 使用 `COPILOT_LLM_*`、`COPILOT_DASHSCOPE_*` 以及
`PLATFORM_EMBEDDING_*` 兜底。密钥只在 API/本地 sidecar 解析，不写入 Renderer、安装包静态资源或
普通日志。

## 验证边界

自动化测试覆盖节点级降级、可靠命中不调用 LLM、低分回退、否定问题、候选歧义、连续追问、
用户隔离、SQLite 持久化、资料过期、Prep 删除和回答取消。Qwen Provider、双路协议、权限错误及
资源清理有测试替身和状态机测试，但自动化通过不等同于真实会议软件、真实 DashScope 账号或各操作
系统音频权限已经完成端到端验证。

- macOS：具备 Electron 构建和系统音频/麦克风权限实现；仍需在真实设备验证并完成发布签名。
- Windows：具备 Electron NSIS 构建配置和双路采集实现；仍需在真实设备验证。
- Linux：Web/API 可构建，Electron 配置含 AppImage/DEB；桌面系统音频受 PipeWire/桌面门户影响，
  当前不声明已支持或已验证。
