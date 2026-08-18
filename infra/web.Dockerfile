# Dev server rather than a static build: this is an internal tool, and serving
# it through Vite keeps the container and `npm run dev` behaving identically.
FROM node:22-slim

WORKDIR /app
COPY apps/web/package*.json ./
RUN npm ci

COPY apps/web/ ./

EXPOSE 5173
# --host so the port is reachable from outside the container; the port stays
# pinned because it is the API's default allowed CORS origin.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
