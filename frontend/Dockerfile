# investigators-ui: build with Node from package-lock.json, serve static files with unprivileged nginx.
# Build context: ./frontend only. Base images are pinned by digest via docker-compose.yml build args.
ARG NODE_IMAGE=node:20-bookworm-slim
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:stable-alpine

FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG INVESTIGATORS_BUILD_SOURCE_SHA256=unset
ARG INVESTIGATORS_BUILD_TIME=unset
ENV INVESTIGATORS_BUILD_SOURCE_SHA256=${INVESTIGATORS_BUILD_SOURCE_SHA256} \
    INVESTIGATORS_BUILD_TIME=${INVESTIGATORS_BUILD_TIME}
RUN npm run build

FROM ${NGINX_IMAGE}
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
