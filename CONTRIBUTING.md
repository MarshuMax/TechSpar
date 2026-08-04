# 参与贡献

感谢你愿意花时间让 TechSpar 变得更好。无论是修复 Bug、完善文档、改进交互、补充测试，还是增加新的模型与服务商适配，我们都欢迎。

这份指南说明如何提出问题、搭建开发环境、提交代码以及准备 Pull Request。开始开发前请先通读相关章节，避免在实现完成后才发现方向或验证方式不符合项目要求。

## 目录

- [开始之前](#开始之前)
- [开发环境](#开发环境)
- [项目结构](#项目结构)
- [开发流程](#开发流程)
- [代码约定](#代码约定)
- [测试与 CI](#测试与-ci)
- [提交与 Pull Request](#提交与-pull-request)
- [数据、安全与隐私](#数据安全与隐私)
- [文档贡献](#文档贡献)
- [AI 辅助贡献](#ai-辅助贡献)
- [License](#license)

## 开始之前

### 先确认问题是否已经存在

提交 Issue 或 PR 前，请先搜索现有的 [Issues](https://github.com/AnnaSuSu/TechSpar/issues) 和 [Pull Requests](https://github.com/AnnaSuSu/TechSpar/pulls)，避免重复工作。

报告 Bug 时，请尽量提供：

- 实际行为与预期行为；
- 可以稳定复现的最小步骤；
- 操作系统、Python、Node.js、浏览器及部署方式；
- 必要的日志或截图；
- 问题是否只在特定模型、Embedding 服务或可选服务中出现。

请先移除日志、截图和配置中的 API Key、Token、密码、真实简历、面试录音及其他个人数据。

### 什么情况可以直接提 PR

以下改动通常可以直接提交 PR：

- 修复范围明确的 Bug；
- 补充回归测试；
- 修正文档、错别字或过期命令；
- 不改变外部行为的小型重构；
- 局部、低风险的体验改进。

以下改动建议先开 Issue 对齐方案：

- 新增完整功能或新的训练模式；
- 大规模重构或跨前后端架构调整；
- 修改认证、用户隔离、数据格式或迁移逻辑；
- 引入新的外部服务、基础设施或重要依赖；
- 可能影响现有部署、兼容性或用户数据的变更。

Issue 中请说明要解决的问题、预期用户价值、初步方案和可能影响的模块。维护者确认方向后再开始大改动，可以减少返工。

## 开发环境

完整的使用说明见 [README 快速开始](README.md#快速开始)。开发时可以使用 Docker，也可以分别启动前后端。

### 环境要求

| 工具 | 推荐版本 | 用途 |
| --- | --- | --- |
| Git | 当前稳定版 | 版本管理 |
| Docker / Docker Compose | 当前稳定版，可选 | 一致化运行环境 |
| Python | 3.11 | 与后端 Docker 镜像和 CI 一致 |
| Node.js | 22 | 与前端 Docker 镜像和 CI 一致 |
| npm | 随 Node.js 安装 | 前端依赖与脚本 |

### Fork 与克隆

先在 GitHub 上 Fork 仓库，再克隆自己的 Fork：

```bash
git clone https://github.com/<你的用户名>/TechSpar.git
cd TechSpar
git remote add upstream https://github.com/AnnaSuSu/TechSpar.git
```

确认远端配置：

```bash
git remote -v
```

### 配置启动项

```bash
cp .env.example .env
```

`.env` 只保存管理员账号、`JWT_SECRET`、是否开放注册等启动引导项。LLM、Embedding、DashScope、Tavily、阿里云 OSS、腾讯云 VPR 等服务密钥由每个用户登录后在「设置」中配置，不要把这些密钥写入代码、测试或提交记录。

### 方式一：Docker

Docker 适合快速验证完整前后端环境：

```bash
docker compose up --build
```

启动后访问 <http://localhost>。停止服务：

```bash
docker compose down
```

### 方式二：本地开发

后端：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

Windows 激活虚拟环境时使用：

```powershell
.venv\Scripts\Activate.ps1
```

只有使用本地 Embedding 时才需要额外安装：

```bash
python -m pip install -r requirements.local-embedding.txt
```

前端：

```bash
cd frontend
npm ci
npm run dev
```

前端默认访问地址为 <http://localhost:5173>，本地后端默认运行在 <http://localhost:8000>。

## 项目结构

```text
backend/
  main.py              FastAPI 应用入口
  routers/             按业务域拆分的 API 路由
  graphs/              面试、训练、备面与复盘流程
  copilot/             实时 Copilot 策略、语音流与辅助逻辑
  storage/             SQLite 等持久化逻辑
  prompts/             集中维护的提示词
frontend/src/
  pages/               页面与页面级模块
  components/          可复用组件
  api/                 API 封装与生成的接口类型
  contexts/            全局上下文
  hooks/               可复用 Hooks
  resume/              简历编辑器模块，适用单独的许可证说明
tests/                  后端回归测试
scripts/                数据导入、导出等维护脚本
data/                   本地运行数据，不提交用户数据
.github/workflows/      GitHub Actions CI
```

## 开发流程

### 1. 同步主分支

```bash
git fetch upstream
git switch main
git pull --ff-only upstream main
```

### 2. 创建工作分支

不要直接在 `main` 上开发或强推。根据改动类型创建清晰的分支名：

```bash
git switch -c fix/profile-atomic-write
git switch -c feat/new-interview-mode
git switch -c docs/update-contributing
```

推荐前缀：`fix/`、`feat/`、`docs/`、`refactor/`、`test/`、`chore/`。

### 3. 保持改动聚焦

- 一个 PR 解决一个清晰问题；
- 不要在功能修复中混入无关格式化或大范围重命名；
- 不要为了“顺手整理”扩大评审范围；
- 如果实现过程中发现独立问题，请另开 Issue 或 PR。

### 4. 在提交前完成验证

根据改动范围运行本指南的 [测试与 CI](#测试与-ci) 命令，并记录实际执行结果。

## 代码约定

### 后端

- Python 代码使用 4 空格缩进，并遵循现有文件的命名、类型标注和组织方式；
- 标准库、第三方依赖和项目内模块分组导入；
- API 路由按业务域放在 `backend/routers/`，统一使用 `/api` 前缀；
- 需要登录态的接口统一通过 `Depends(get_current_user)` 获取用户身份；
- 所有用户数据读写必须带 `user_id`，不得跨用户读取或覆盖；
- LLM 调用统一通过 `backend.llm_provider.get_langchain_llm(user_id)`；
- 需要 LLM 返回 JSON 时，复用项目现有的解析与重试逻辑，不要在各模块重复实现；
- 文件写入、导入导出和上传逻辑需要考虑路径穿越、原子写入、异常清理与并发访问；
- 不要把真实外部 API 调用放进默认测试。

### 前端

- 项目允许 JS 与 TS 共存，但新业务代码优先使用 TypeScript；
- 修改旧 `.jsx` 文件时，可在范围可控的情况下迁移为 TypeScript，但不要让迁移掩盖实际改动；
- 优先复用 `components/`、`hooks/`、`contexts/` 和现有 UI 模式；
- 不要在组件中散落重复请求逻辑，API 调用集中到 `frontend/src/api/`；
- 改动后端接口后，先启动本地后端，再重新生成接口类型：

  ```bash
  cd frontend
  npm run gen:api
  ```

- 新增或升级前端依赖时，必须同步提交 `frontend/package-lock.json`；
- 有可见界面变化时，PR 中应附截图或短视频。

### 通用原则

- 不绑定特定 LLM、Embedding、语音或搜索服务商，优先使用兼容接口和用户级配置；
- 注释重点说明“为什么”，不要逐行复述代码；
- 错误信息应帮助定位问题，但不得包含密钥或个人数据；
- 新依赖需要说明必要性，能用标准库或现有依赖解决时不要重复引入；
- 不要提交构建产物、缓存、本地数据库、用户文件或编辑器临时文件。

## 测试与 CI

### 后端

运行全部后端回归测试：

```bash
python -m unittest discover -s tests
```

验证应用可以完整导入并装配路由：

```bash
python -c "from backend.app import create_app; create_app(); print('OK app builds')"
```

修复 Bug 时，必须补充能够复现旧问题的回归测试。测试应满足：

- 修复前失败，修复后通过；
- 同时覆盖成功路径和关键失败路径；
- 使用临时目录、Mock 或内存数据隔离本地状态；
- 不依赖真实 API Key、外部网络或个人数据；
- 测试结束后不留下文件、数据库或后台任务。

### 前端

```bash
cd frontend
npm run typecheck
npm run lint
npm run build
```

如果改动涉及页面交互，请同时手动验证相关流程，并在 PR 中写明浏览器和验证步骤。

### 按改动范围选择检查

| 改动范围 | 最低验证要求 |
| --- | --- |
| 仅文档 | 检查链接、命令、标题层级和渲染效果 |
| 后端 | 应用构建检查 + 后端回归测试 |
| 前端 | typecheck + lint + build + 相关页面手动验证 |
| 前后端接口 | 后端检查 + 重新生成 API 类型 + 前端全部检查 |
| 数据迁移或持久化 | 正常、失败、重复执行和旧数据兼容测试 |

每个 PR 都会触发 GitHub Actions。合并到 `main` 前，以下必需检查必须通过：

- `backend-smoke`：安装依赖、构建后端应用并运行回归测试；
- `frontend-build`：使用锁文件安装依赖并构建前端。

外部贡献者首次提交 PR 时，Actions 可能需要维护者批准运行。这表示工作流尚未获准执行，不等于测试失败。

## 提交与 Pull Request

### Commit 信息

格式：

```text
类型(范围): 简短描述
```

常用类型：

- `feat`：新功能；
- `fix`：Bug 修复；
- `docs`：文档；
- `test`：测试；
- `refactor`：不改变外部行为的重构；
- `chore`：构建、依赖或维护工作。

示例：

```text
fix(profile): 使用原子写入保存用户画像
test(memory): 覆盖画像写入失败路径
docs(contributing): 完善贡献流程和测试要求
```

每个提交只做一件事，并尽量保证单个提交可以构建和验证。不要提交无意义的 `update`、`fix`、`changes` 等信息。

### 提交 PR

```bash
git push -u origin <分支名>
```

PR 描述至少应包含：

- **背景/问题**：为什么需要这次改动；
- **解决方案**：改了什么，为什么这样设计；
- **影响范围**：涉及哪些模块，是否影响兼容性或数据；
- **验证结果**：实际运行的命令及结果；
- **关联 Issue**：如有，使用 `Closes #123` 或提供链接；
- **界面证据**：有 UI 变化时附截图或短视频；
- **迁移说明**：有配置、数据库或部署变化时写出升级和回滚步骤。

提交前自查：

- [ ] PR 只解决一个明确问题；
- [ ] 已阅读并理解自己提交的全部代码；
- [ ] 已添加或更新必要测试；
- [ ] 已运行与改动范围对应的检查；
- [ ] 未提交密钥、个人数据或本地生成文件；
- [ ] 文档、配置示例和接口类型已同步；
- [ ] PR 描述能够让不了解上下文的人完成评审。

维护者可能要求缩小范围、补充测试、解释设计取舍或更新文档。请直接在原分支继续提交修复，不需要为同一问题新开 PR。除非维护者明确要求，不要通过 force push 重写已经进入评审的提交历史。

## 数据、安全与隐私

TechSpar 会处理简历、面试记录、语音、用户画像和第三方服务凭据。贡献时请特别注意：

- 不提交 `.env`、API Key、Token、密码或生产配置；
- 不提交 `data/users/`、SQLite 数据库、简历、录音、转写文本或用户画像；
- 示例和测试只使用虚构、最小化的数据；
- 日志中不要输出完整凭据、Authorization Header 或敏感正文；
- 导入导出必须保持用户隔离，并验证路径和归属；
- 新增外部服务时，应允许用户自行配置，说明数据会发送到哪里，并提供未配置时的安全行为；
- 怀疑存在可利用的安全问题时，不要在公开 Issue 中粘贴真实凭据、个人数据或完整攻击脚本，请先联系[仓库维护者](https://github.com/AnnaSuSu)。

## 文档贡献

- 中文 README 是主要使用文档；涉及公共功能时同步检查 `README.en.md` 是否需要更新；
- 命令必须在仓库当前版本中可执行，不要复制未经验证的历史命令；
- 链接优先使用仓库内相对路径，避免分支名变化导致失效；
- 示例配置必须使用占位值，不能包含可用密钥；
- 文档应区分默认行为、可选能力和部署相关差异。

## AI 辅助贡献

可以使用 AI 工具帮助分析、编码、测试或编写文档，但提交者必须：

- 阅读并理解所有提交内容；
- 对正确性、安全性、许可证和维护成本负责；
- 验证工具生成的命令、API、依赖和链接确实存在；
- 运行与改动范围匹配的测试，而不是只依赖生成结果；
- 建议在 PR 中简要说明使用了什么工具、用于什么环节，以及人工完成了哪些验证。

不要提交自己无法解释或无法维护的生成代码。

## License

项目整体使用 [CC BY-NC 4.0](LICENSE)。提交贡献即表示你同意自己的贡献按相同条款发布。

`frontend/src/resume/` 中的简历编辑与模板渲染代码移植自 [Magic Resume](https://github.com/JOYCEQL/magic-resume)，保留其原始许可证（Apache 2.0 + 附加商业限制）。修改该目录前，请阅读其中的 [README](frontend/src/resume/README.md) 和 [LICENSE](frontend/src/resume/LICENSE)。

引入第三方代码、素材或模型时，必须确认其许可证与本项目兼容，并在必要时保留版权声明和来源。不要复制来源不明或无权再分发的内容。

再次感谢你的贡献。清晰的问题描述、聚焦的改动和可复现的验证，会让每一次评审都更高效。
