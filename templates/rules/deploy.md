---
description: "Deploy procedures for Vercel and Docker VPS. Loaded when user invokes /deploy or asks about deployment."
---

# Deploy

1. `git add` + `commit` + `push`
2. Platform:
   - **Vercel**: poll status until "Ready", curl verify URL
   - **Docker (VPS)**: SSH → `git pull && docker compose up -d --build` → `docker ps` + `logs --tail=20` + health check
3. Output: status + evidence (HTTP 200 / health OK / clean logs)
4. [CRITICAL] Deploy failed → error logs, do NOT say "completed"
