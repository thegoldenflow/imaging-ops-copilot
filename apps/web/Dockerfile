# Imaging Ops Copilot web: static build served by nginx, which also proxies /api to the backend.
# Build from the repository root:
#   docker build -f apps/web/Dockerfile .
FROM node:20-alpine AS build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY apps/web/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /web/dist /usr/share/nginx/html
# Backend address, filled into the nginx config at start.
ENV API_UPSTREAM=api:8000
EXPOSE 80
