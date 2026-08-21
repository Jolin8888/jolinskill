# n8n DingTalk delivery

This compose project is deployed on 007 at
`/home/jolin/apps/n8n-zhihu-dingtalk`.

- n8n listens only on `127.0.0.1:5678`.
- The workflow receives title, DingTalk document URL, report date, and an
  idempotency key. It never receives the report body.
- The DingTalk robot token and n8n encryption key exist only in the server
  `.env` file with mode `600`.
- Delivery claims and successful results are retained in a dedicated directory
  inside n8n's persistent data volume for duplicate suppression, while n8n also
  stores execution history.
- A claimed delivery that never reaches a verified DingTalk success remains
  `pending` and blocks automatic retries until an operator checks it. This
  avoids duplicate group messages when the remote result is uncertain.
- The 007 sender records document, access, n8n delivery, and completion markers.

Deployment uses the exact image digest in `compose.yaml`. Import and publish the
workflow with:

```bash
docker compose run --rm n8n import:workflow --input=/files/workflow.json
docker compose run --rm n8n publish:workflow --id=zhihuDingtalkMarketIntel
docker compose up -d
```
