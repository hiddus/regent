FROM node:20-alpine AS builder
WORKDIR /app
COPY apps/novel-web/package.json apps/novel-web/package-lock.json* ./
RUN npm ci --production=false
COPY apps/novel-web/ .
RUN npm run build
FROM nginx:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY apps/novel-web/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
