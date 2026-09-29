# Admin + User panels (static builds) behind nginx, which also proxies /api to the backend.
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
COPY packages/web-shared/package.json packages/web-shared/
COPY admin-web/package.json admin-web/
COPY user-web/package.json user-web/
RUN npm ci --no-audit --no-fund
COPY packages packages
COPY admin-web admin-web
COPY user-web user-web
RUN npm run build

FROM nginx:1.27-alpine
COPY infrastructure/nginx/nginx.conf /etc/nginx/nginx.conf
COPY infrastructure/nginx/omnisend-common.conf infrastructure/nginx/omnisend-proxy.conf /etc/nginx/
COPY --from=build /src/user-web/dist /usr/share/nginx/user
COPY --from=build /src/admin-web/dist /usr/share/nginx/admin
EXPOSE 8080 8081 8082
