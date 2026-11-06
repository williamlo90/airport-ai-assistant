FROM node:22-bookworm-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d AS builder
ENV NODE_OPTIONS=--max-old-space-size=512 npm_config_jobs=1
WORKDIR /build
RUN npm install --global npm@11.17.0 --ignore-scripts --no-audit --no-fund
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/index.html frontend/tsconfig*.json frontend/vite.config.ts ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm run build

FROM nginx:stable-alpine@sha256:583b7f2fce1cdb8da5ad5c39ebd3c33b98b87d493190e548bdee1fa8c619c311 AS runtime
COPY deploy/containers/nginx.conf /etc/nginx/nginx.conf
COPY deploy/containers/proxy.conf /etc/nginx/westbridge-proxy.conf
COPY --from=builder /build/dist /usr/share/nginx/html
USER 101:101
EXPOSE 8443
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
