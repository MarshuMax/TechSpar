FROM oven/bun:1.3.14-alpine AS build
WORKDIR /app
COPY package.json bun.lock ./
COPY apps/api/package.json apps/api/package.json
COPY apps/desktop/package.json apps/desktop/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY packages/core/package.json packages/core/package.json
COPY packages/db/package.json packages/db/package.json
COPY packages/platform/package.json packages/platform/package.json
COPY packages/providers/package.json packages/providers/package.json
COPY packages/testing/package.json packages/testing/package.json
COPY frontend/package.json frontend/package.json
RUN bun install --frozen-lockfile --filter frontend
# 上游 v0.4.1 的缺陷：只 COPY frontend 源码时，vite 无法解析
# 前端运行时 import 的 @techspar/contracts/events（workspace 包源码不在镜像里），
# 构建必然失败。这里补 COPY 工作区包（node_modules 已被 .dockerignore 排除）。
COPY packages/contracts packages/contracts
COPY frontend frontend
RUN bun run --cwd frontend build

FROM nginx:alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
