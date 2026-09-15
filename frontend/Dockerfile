FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
RUN adduser -D -u 1000 appuser \
    && sed -i 's/user  nginx;//' /etc/nginx/nginx.conf \
    && touch /var/run/nginx.pid \
    && chown -R appuser:appuser /var/run/nginx.pid /var/cache/nginx /etc/nginx
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
USER appuser
EXPOSE 8080
