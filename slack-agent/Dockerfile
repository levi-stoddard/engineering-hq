# Slack agent container: non-root, production dependencies only, health from a heartbeat file.
FROM node:22-alpine

WORKDIR /app
ENV NODE_ENV=production

COPY slack-agent/package.json slack-agent/package-lock.json ./slack-agent/
RUN cd slack-agent && npm ci --omit=dev && npm cache clean --force

COPY slack-agent/src ./slack-agent/src
COPY examples/acme/connector-fixtures.json ./examples/acme/connector-fixtures.json

RUN mkdir -p /app/data && chown -R node:node /app/data
USER node

ENV HEARTBEAT_FILE=data/heartbeat.json \
    REQUEST_OUTBOX=data/outbox \
    REVIEW_DRAFTS=data/review-packages

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD node -e "const h=JSON.parse(require('fs').readFileSync('/app/data/heartbeat.json','utf8'));process.exit(h.status==='ONLINE'&&Date.now()-Date.parse(h.at)<60000?0:1)"

CMD ["node", "slack-agent/src/listener.js"]
