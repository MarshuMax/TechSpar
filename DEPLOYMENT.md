# TechSpar 部署记录

## 部署信息
- 时间: 2026-06-21
- 当前代码源: https://github.com/MarshuMax/TechSpar
- 分支: agent/interview-copilot-knowledge-compiler
- 提交: 39c2318 (+ 本地部署补丁)
- 上游原仓: https://github.com/AnnaSuSu/TechSpar（未改）
- 服务器: Oracle Cloud (161.118.213.14)
- 域名: https://techspar.marshu.dpdns.org

## 架构
```
Cloudflare DNS (techspar.marshu.dpdns.org → 161.118.213.14, proxied)
  └─ Caddy reverse proxy (443 → localhost:8081)
       └─ Docker: frontend (nginx, 8081:80)
            └─ /api/* → Docker: backend (FastAPI, 8001:8000)
            └─ /ws/*  → Docker: backend (WebSocket)
```

## 配置文件
- 项目路径: /home/ubuntu/repo/TechSpar/
- .env: JWT_SECRET + admin账号配置
- docker-compose.yml: 端口 127.0.0.1:8081(frontend) + 127.0.0.1:8001(backend)
- Caddy: /etc/caddy/Caddyfile (techspar.marshu.dpdns.org block)
- systemd: /etc/systemd/system/techspar.service
- 数据卷: ./data → /app/data（用户、知识库、DB 全部在此，升级禁止动路径）

## 管理命令
- 启动: sudo systemctl start techspar
- 停止: sudo systemctl stop techspar
- 重启: sudo systemctl restart techspar
- 状态: sudo systemctl status techspar
- 日志: sudo journalctl -u techspar -f
- 重建: cd /home/ubuntu/repo/TechSpar && docker compose build && docker compose up -d --remove-orphans

## 默认管理员
- 邮箱: admin@techspar.local
- 密码: admin123
- 登录后可在设置里修改，以及配置自己的 LLM / Embedding API Key

## 已知问题修复
- 2026-06-23: Copilot 实时模式无法使用 — 原因：nginx 缺少 /ws/ WebSocket 代理。已在 frontend/nginx.conf 添加 /ws/ location block，支持 WebSocket Upgrade。
- 2026-08-09: Copilot 实时会话"正在预计算策略树 embedding..."无限循环。根因：async 里同步 embedding 阻塞事件循环。修复见 strategy_tree.py 线程池并发 + 落盘缓存。

## 升级记录
- 2026-08-09: 升级至上游 3cca462（266 commits）。远程 main 曾 force push，采用 reset --hard 对齐。新版上游 nginx.conf 已自带 /ws/ 代理，本地定制仅保留 docker-compose.yml（127.0.0.1 端口绑定 + restart）。数据目录 ./data 未动（interviews.db、langgraph_checkpoints.sqlite、users/ 全部保留），登录与画像接口验证通过。
- 2026-08-09: Copilot 实时会话修复 backend/copilot/strategy_tree.py：线程池并发（8 路）+ data/copilot_emb_cache/ 落盘缓存 + .dockerignore 排除 data/。
- 2026-08-12: 切换到个人 fork MarshuMax/TechSpar 分支 agent/interview-copilot-knowledge-compiler @ 39c2318（Interview Copilot knowledge compiler）。
  - 切换前备份: ~/backups/techspar-pre-fork-20260812-074908/
  - 数据迁移方式: 保留同一 ./data bind mount，不重建卷、不导入导出；DB 与 users/ 原样挂载
  - 本地补丁保留: docker-compose.yml 127.0.0.1 端口绑定 + restart；.dockerignore 整行排除 data；strategy_tree.py 兼容模式 fallback（to_thread+缓存+load_embeddings）
  - 新能力: prep 阶段 compile_strategy_tree + prepared_answer_index；实时会话优先加载预编译知识包，旧 prep 无 index 时走兼容模式
  - 验证: 登录 200；profile target_role=DevOps工程师；topics 含 rchain-interview/mje-ci/devops-basics；知识库文件 8+6+6；copilot_preps 2 条；sessions 14；memory_vectors 108；域名 Caddy 200；WS 代理穿透到后端
