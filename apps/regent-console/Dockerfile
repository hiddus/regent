# Build stage
FROM node:20-alpine AS builder
WORKDIR /app
COPY apps/regent-console/package.json apps/regent-console/package-lock.json* ./
RUN npm ci --production=false
COPY apps/regent-console/ .
RUN npm run build

# Serve stage
FROM nginx:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY apps/regent-console/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
FROM nginx:1.27-alpine
COPY apps/regent-console/index.html /usr/share/nginx/html/index.html
EXPOSE 80
