FROM node:24-bookworm-slim AS build

WORKDIR /app

# 国内机房访问 registry.npmjs.org 极慢；构建期切到 npmmirror
RUN npm config set registry https://registry.npmmirror.com

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# 生产构建默认同域相对路径（空字符串）；可由 compose build-arg 覆盖
ARG VITE_API_BASE=
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
