# frontend/Dockerfile —— 多阶段：Node 构建静态资源 → nginx 托管并发反代 /api
FROM node:22-alpine AS build
WORKDIR /app
# NPM_REGISTRY 可选换源（云服务器在国内时指向 npmmirror 提速）；默认官方源
ARG NPM_REGISTRY=https://registry.npmjs.org
RUN npm config set registry "${NPM_REGISTRY}"
# 先装依赖（独立层：改源码不必重装 node_modules）
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build          # tsc --noEmit && vite build（类型检查不过就不出包）

FROM nginx:stable-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
