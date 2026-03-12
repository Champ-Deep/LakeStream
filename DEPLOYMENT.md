# LakeStream Deployment Guide

## Local Testing

### Prerequisites
- Docker Desktop running
- Python 3.12+ installed
- OpenSSL for generating JWT secret

### Quick Start

1. **Run the automated test script:**
   ```bash
   ./scripts/test_local_full.sh
   ```

2. **Manual steps (alternative):**
   ```bash
   # 1. Start Docker services
   make docker-up

   # 2. Set up environment
   cp .env.example .env
   # Edit .env and set JWT_SECRET (run: openssl rand -hex 32)

   # 3. Run migrations
   make migrate

   # 4. Install Playwright browsers
   playwright install chromium

   # 5. Start API server (terminal 1)
   make dev

   # 6. Start worker (terminal 2)
   make worker
   ```

3. **Test the features:**
   - Open http://localhost:8000
   - Should redirect to `/login` (auth protection working ✅)
   - Click "Sign up" to create account
   - Fill in: Organization Name, Full Name, Email, Password
   - After signup, automatically logged in and redirected to dashboard
   - Test Quick Scrape with a URL (e.g., https://blog.hubspot.com)
   - Select tier: `headless` (Playwright)
   - Click "Scrape"
   - Job should show RUNNING → COMPLETED (with data) or FAILED (if no data)

### Test Cases

**Test 1: Signup Flow**
- Visit `/signup`
- Create account with valid details
- Should auto-login and redirect to dashboard
- Check database: `SELECT * FROM users;` should show new user
- Check database: `SELECT * FROM organizations;` should show new org

**Test 2: Login Flow**
- Logout (click logout button)
- Visit `/login`
- Enter email/password from signup
- Should redirect to dashboard with user data

**Test 3: False Success Fix**
- Scrape an invalid domain: `https://nonexistent-domain-12345.com`
- Job should show **FAILED** (not COMPLETED)
- Error message: "No data extracted from domain (empty site or blocked)"

**Test 4: Playwright Networkidle**
- Scrape a JS-heavy site: `https://blog.hubspot.com`
- Select tier: `headless`
- Job should extract content (pages_scraped > 0, data_count > 0)
- Check logs for: `playwright_networkidle_complete`

**Test 5: Strategy Display**
- Check job detail page: `/jobs/{job_id}`
- Strategy should show: "Playwright" (not "headless_browser")
- Legacy jobs should show: "Playwright (legacy)"

---

## Railway Deployment

### Prerequisites
- Railway CLI installed: `npm i -g @railway/cli`
- Railway account connected: `railway login`
- Railway project initialized: `railway init`

### Environment Variables

Set these in Railway dashboard or via CLI:

**Required:**
```bash
railway variables set JWT_SECRET=$(openssl rand -hex 32)
railway variables set DATABASE_URL=<postgres_url_from_railway>
railway variables set REDIS_URL=<redis_url_from_railway>
```

**Optional (Proxies):**
```bash
railway variables set BRIGHTDATA_PROXY_URL=<your_brightdata_url>
railway variables set SMARTPROXY_URL=<your_smartproxy_url>
```

**Optional (LakeCurrent):**
```bash
railway variables set LAKECURRENT_BASE_URL=<lakecurrent_backend_url>
railway variables set LAKECURRENT_ENABLED=true
```

### Deployment Steps

1. **Commit all changes:**
   ```bash
   git add .
   git commit -m "feat: production-ready with auth flow + false success fix + Playwright networkidle"
   ```

2. **Deploy to Railway:**
   ```bash
   # If using Railway CLI
   railway up

   # Or push to GitHub (if connected to Railway)
   git push origin main
   ```

3. **Set up services:**
   - Railway will detect `Procfile` and create 2 services:
     - **web**: API server (uvicorn)
     - **worker**: arq worker
   - Make sure both services are enabled

4. **Add Postgres + Redis:**
   - In Railway dashboard, add:
     - PostgreSQL (Railway Postgres)
     - Redis (Railway Redis)
   - Railway will auto-set `DATABASE_URL` and `REDIS_URL`

5. **Run migrations (one-time):**
   ```bash
   # In Railway web service console, or via CLI:
   railway run python -m src.db.migrate
   ```

6. **Verify deployment:**
   - Open your Railway domain: `https://<your-app>.railway.app`
   - Should redirect to `/login`
   - Test signup/login flow
   - Test scraping with a real URL

### Service Configuration

**Web Service (API):**
- **Start Command**: `uvicorn src.server:app --host 0.0.0.0 --port $PORT`
- **Health Check**: `/api/ping`
- **Replicas**: 1 (scale up as needed)

**Worker Service:**
- **Start Command**: `arq src.queue.worker.WorkerSettings`
- **Health Check**: None (arq workers don't expose HTTP)
- **Replicas**: 1-3 (scale based on job volume)

### Post-Deployment Checks

1. **Health Check:**
   ```bash
   curl https://<your-app>.railway.app/api/ping
   # Should return: {"status":"ok"}
   ```

2. **Create Test Account:**
   - Visit `https://<your-app>.railway.app/signup`
   - Create organization + first user
   - Should auto-login and show dashboard

3. **Test Scraping:**
   - Submit a scrape job via Quick Scrape
   - Check job status updates from PENDING → RUNNING → COMPLETED/FAILED
   - Verify data appears in database

4. **Check Logs:**
   ```bash
   # Railway CLI
   railway logs --service web
   railway logs --service worker
   ```

5. **Monitor Worker:**
   - Check worker logs for job processing
   - Should see: `job_started`, `playwright_networkidle_complete`, `job_completed`

### Troubleshooting

**Issue: Jobs stuck in PENDING**
- Check worker logs: `railway logs --service worker`
- Verify Redis connection: `REDIS_URL` is set correctly
- Restart worker service

**Issue: Playwright crashes**
- Worker needs sufficient memory (at least 1GB)
- In Railway, increase worker service memory limit
- Check logs for: `playwright._impl._api_types.Error`

**Issue: False success (COMPLETED with 0 data)**
- This should be fixed! Check code version deployed
- Verify `src/queue/jobs.py` has the validation logic (lines 143-183)

**Issue: Login redirects to /login infinitely**
- Check `JWT_SECRET` is set in Railway
- Verify cookie domain settings (should work automatically)

---

## Rollback Plan

If deployment fails:

1. **Revert code:**
   ```bash
   git revert HEAD
   git push origin main
   ```

2. **Or rollback in Railway:**
   - Railway dashboard → Deployments → Select previous version → Redeploy

---

## Success Metrics

After deployment, verify:
- ✅ Signup flow creates org + user
- ✅ Login flow authenticates and redirects to dashboard
- ✅ Jobs with 0 data show FAILED (not COMPLETED)
- ✅ Playwright scraping extracts content from JS-heavy sites
- ✅ Strategy display shows "Playwright" (not "headless_browser")
- ✅ Worker processes jobs without getting stuck

---

## Monitoring

**Key Metrics to Track:**
- Job success rate: `SELECT COUNT(*) FROM scrape_jobs WHERE status = 'completed';`
- False success rate: Should be 0% (COMPLETED with pages_scraped = 0)
- Worker health: Check arq logs every hour
- Playwright crashes: Monitor worker logs for errors

**Alerts to Set Up:**
- Worker offline for >5 minutes
- Job failure rate >20%
- Postgres connection errors
- Redis connection errors

---

## Next Steps

After successful deployment:
1. Set up monitoring (Railway Metrics or external APM)
2. Configure email notifications (ChampMail integration)
3. Add proxy services (Bright Data, Smartproxy)
4. Test with production data
5. Invite team members to create accounts
