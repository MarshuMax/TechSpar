# TechSpar 部署记录（v0.4.1 + 知识编译器）

## 部署信息
- 域名：https://techspar.marshu.dpdns.org （Cloudflare 橙云 → Caddy → nginx）
- 服务器：Oracle Cloud `161.118.213.14`
- 代码：`/home/ubuntu/repo/TechSpar`，分支 **`deploy/kc-upstream`**
  （= `feature/copilot-knowledge-upstream-integration` @ d0a3151 + 6 个本机补丁）
- 上游基线：`origin/main` @ dac7930（v0.4.1-4）；运行时 **Bun 1.3.14**，不需要 Python
- 切换日期 2026-10-08，**实测停机 13 秒**
- 切换前备份：`~/backups/techspar-switch-20261008-092749/`
- 回滚镜像标签：`techspar-backend:legacy-python-20260812` / `techspar-frontend:legacy-python-20260812`

## 架构
```
CF → Caddy :443 → localhost:8081 → nginx(frontend) → /api /ws → backend:8000 (Hono)
                                     ./data → /app/data（bind mount，禁止改路径）
```

## 本机补丁（6 个，缺一不可）
| # | 补丁 | 为什么必须 |
|---|---|---|
| A | `packages/providers` embedding 显式 `encoding_format='float'` | 本机网关只接受 float，SDK 默认 base64 → 不补则 test-match / 向量匹配 / 知识库与资料检索 / 画像向量 / 变体索引构建全部 400 |
| B | 去掉 3 处 `isDesktopApp()` gate | 上游 v0.4.1 把网页端 Copilot 整页换成「下载桌面端」；本机是网页部署 |
| C | `frontend/Dockerfile` 补 `COPY packages/contracts` | 上游 v0.4.1 自身缺陷：前端运行时 import `@techspar/contracts/events`，Dockerfile 不 COPY packages 源码 → 前端镜像必然构建失败 |
| D | profile 加载期归一化 | v0.4.1 收紧契约后，Python 时代 profile.json（null 值 + 缺 due_reviews）会让 `GET /api/profile` 500 |
| E | compose 只绑回环 + restart；`.dockerignore` 排除 data/.upgrade | 上游 compose 是 0.0.0.0:8000/80；构建上下文含 1.3GB 原型目录 |
| F | 前端 vitest `^4.1.10` + src/lib 三例改 vitest | 原 vitest 3 与 vite 8 不兼容 → `vi.mock` 完全不生效 |

## 环境变量（.env，chmod 600）
```
JWT_SECRET / DEFAULT_EMAIL / DEFAULT_PASSWORD / DEFAULT_NAME / ALLOW_REGISTRATION
VOICEPRINT_ENCRYPTION_KEY=<随机>
TECHSPAR_BASE_DIR=/app      # TS 版路径是 cwd 相对，必须显式钉住
TECHSPAR_DATA_DIR=data
DB_PATH=data/interviews.db
```
可选：`COPILOT_LLM_*`、`COPILOT_DASHSCOPE_*`、`PLATFORM_*`。已被忽略：`MAX_QUESTIONS_PER_PHASE`、`MAX_DRILL_QUESTIONS`。

## 管理命令（无需 sudo）
```bash
cd /home/ubuntu/repo/TechSpar
docker compose ps && docker compose logs -f backend
docker compose build && docker compose up -d --remove-orphans
docker compose restart backend
```
`techspar.service` 的 ExecStart 就是本目录 `docker compose up -d --remove-orphans`，与手工 compose 不冲突。

## 备份 / 回滚
```bash
docker exec techspar-backend-1 tar -C /app -czf /tmp/b.tar.gz data
docker cp -q techspar-backend-1:/tmp/b.tar.gz ~/backups/techspar-$(date +%F).tar.gz
```
回滚：`docker compose down` → `git checkout -f 363dd2f` → `docker tag techspar-{backend,frontend}:legacy-python-20260812 ...:latest` → `docker compose up -d`；
数据还原用切换前整副本（新版已切 WAL，**只还原 interviews.db 会不一致**）。

## 本次自动迁移
- `journal_mode: delete → wal`
- 新表：`copilot_prepared_variants`、`copilot_realtime_sessions`、`resume_interview_state`、`tasks`、`llm_usage`
- 旧 profile.json 缺字段/null 在**加载期**归一化（用户数据一条未动）
- Python 遗留不再读取：`langgraph_checkpoints.sqlite*`、`copilot_emb_cache/`

## 切换后已知状态
- 旧的 3 个 prep 需在 Copilot 详情页点「重新准备」重建变体索引（会走 LLM 编译 + embedding，产生费用）
- 实时 Copilot 走 v0.4.1 双路采集（麦克风=自己 / 系统音频=对方）；浏览器需允许共享音频，建议耳机 + macOS/Windows Chrome
