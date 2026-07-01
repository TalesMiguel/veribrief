# VeriBrief — Production Readiness Plan

## 1. Assumptions

- **Scale**: 100–1000 analyses/day (per customer)
- **Users**: Legal professionals (attorneys, paralegals) using via web UI
- **Data sensitivity**: Confidential legal documents; CCPA compliance required
- **Latency tolerance**: 30–120 seconds per analysis acceptable (async job model)
- **Availability target**: 99% uptime (legal work is time-sensitive but not critical infrastructure)
- **Cost sensitivity**: Moderate (users pay per analysis or subscription; LLM costs are line item)
- **Provider flexibility**: Multi-provider support (Gemini, OpenAI) with automatic fallback

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│ Web UI (React)                                           │
│ - Document upload                                        │
│ - Job status polling / WebSocket                        │
│ - Report display (findings, confidence breakdown)        │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│ FastAPI Gateway (Stateless)                              │
│ - Auth / Rate limit check                               │
│ - Document validation & upload to S3                     │
│ - Server-Sent Events (SSE) streaming to client           │
│ - Real-time agent progress updates                       │
└──────────────────────┬──────────────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        │                             │
┌───────▼────────┐         ┌─────────▼────────┐
│ RabbitMQ       │         │ PostgreSQL       │
│ (Job Queue)    │         │ - Reports        │
│                │         │ - Jobs           │
│ - Pending      │         │ - Audit logs     │
│ - Processing   │         │ - Rate limits    │
│ - Retry queue  │         │ - Users/orgs     │
└────────┬───────┘         └──────────────────┘
         │
┌────────▼────────────────────────────────────┐
│ Celery Workers (Stateless, horizontally     │
│ scalable)                                    │
│                                              │
│ For each job:                                │
│ 1. CitationExtractorAgent                   │
│ 2. CitationVerifierAgent (with retry)       │
│ 3. FactConsistencyAgent (with retry)        │
│ 4. JudicialMemoAgent                        │
│ 5. Persist report to PostgreSQL             │
│                                              │
│ On failure: mark job, allow user retry      │
└────────┬─────────────────────────────────────┘
         │
┌────────▼────────────────────────────────────┐
│ S3 (Document Storage)                       │
│ - Encrypted at rest                         │
│ - Lifecycle: delete after 90 days          │
│ - Access logs to CloudTrail                 │
└─────────────────────────────────────────────┘
```

---

## 3. AI Workflow Orchestration

### 3.1 Agent Pipeline

Each analysis runs a 4-stage pipeline:

**Stage 1: Citation Extraction**
- Input: Full MSJ text
- Agent: `CitationExtractorAgent`
- Output: Structured list of `Citation` objects
- Failure mode: Return empty list, mark finding as "error"

**Stage 2: Citation Verification** (with retry)
- Input: List of citations
- Agent: `CitationVerifierAgent`
- Output: List of `CitationFinding` objects (verdict + confidence + reasoning)
- Retry logic:
  - On API failure: retry up to 2 times with exponential backoff
  - On persistent failure: mark as `verdict: "error"`, continue pipeline

**Stage 3: Fact Consistency** (with retry)
- Input: MSJ text + police report + medical records + witness statement
- Agent: `FactConsistencyAgent`
- Output: List of `FactFinding` objects (contradictions only)
- Retry logic: same as Stage 2

**Stage 4: Judicial Summary**
- Input: Top findings (sorted by confidence)
- Agent: `JudicialMemoAgent`
- Output: One-paragraph summary for judge
- Failure mode: Return null, UI shows "summary unavailable"

### 3.2 Failure Handling & Graceful Degradation

Each agent wraps LLM calls in try/except:

```python
try:
    response = call_llm(...)
    return parse_response(response)
except APIError:
    # Increment error counter for monitoring
    # Return partial result with verdict="error"
except ValidationError:
    # Malformed response from LLM
    # Return partial result with verdict="error"
```

**Pipeline continuation:** If CitationVerifier fails on 3 of 10 citations, return 7 verified + 3 errored. Do not block entire analysis.

### 3.3 Model Fallback Strategy

When an LLM call fails, attempt fallback in this order:

1. **Same provider, cheaper model:**
   - Primary: `gemini-3.1-flash` (Gemini)
   - Fallback: `gemini-1.5-flash` (same provider, older/cheaper)

2. **Different provider:**
   - Primary: OpenAI `gpt-4o-mini`
   - Fallback: Claude via Anthropic API (if configured)

3. **If all fail:** Mark job as failed, allow user to retry with different provider override

**Implementation:** Store list of `AvailableModels` per provider in config; agent tries them in order.

---

## 4. Database & Persistence

### 4.1 PostgreSQL Schema (Multitenancy)

```sql
-- Organizations (firms, solo practitioners)
CREATE TABLE organizations (
  id UUID PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  ccpa_contact_email VARCHAR(255),
  created_at TIMESTAMP DEFAULT NOW()
);

-- Users
CREATE TABLE users (
  id UUID PRIMARY KEY,
  organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
  email VARCHAR(255) NOT NULL UNIQUE,
  role ENUM('admin', 'analyst') DEFAULT 'analyst',
  created_at TIMESTAMP DEFAULT NOW()
);

-- Rate limit quotas
CREATE TABLE quotas (
  id UUID PRIMARY KEY,
  organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
  analyses_per_day INT DEFAULT 100,
  tokens_per_day INT DEFAULT 1000000,
  reset_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT NOW()
);

-- Jobs (one per analysis request)
CREATE TABLE jobs (
  id UUID PRIMARY KEY,
  organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
  user_id UUID REFERENCES users(id),
  status ENUM('pending', 'processing', 'completed', 'failed') DEFAULT 'pending',
  document_s3_path VARCHAR(512),  -- s3://bucket/org_id/job_id/docs.zip
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  error_message TEXT,  -- if status='failed'
  created_at TIMESTAMP DEFAULT NOW()
);

-- Reports (analysis results)
CREATE TABLE reports (
  id UUID PRIMARY KEY,
  job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
  citations_json JSONB NOT NULL,  -- array of CitationFinding
  facts_json JSONB NOT NULL,      -- array of FactFinding
  judicial_memo TEXT,
  summary_json JSONB,  -- num_citations, num_issues, overall_confidence
  created_at TIMESTAMP DEFAULT NOW()
);

-- Audit log
CREATE TABLE audit_logs (
  id UUID PRIMARY KEY,
  organization_id UUID REFERENCES organizations(id),
  user_id UUID REFERENCES users(id),
  action VARCHAR(50),  -- 'upload_document', 'start_analysis', 'download_report'
  resource_id UUID,    -- job_id or report_id
  ip_address INET,
  timestamp TIMESTAMP DEFAULT NOW()
);

-- LLM usage (for cost tracking)
CREATE TABLE llm_usage (
  id UUID PRIMARY KEY,
  job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
  organization_id UUID REFERENCES organizations(id),
  provider VARCHAR(50),  -- 'gemini', 'openai', etc
  model VARCHAR(100),
  input_tokens INT,
  output_tokens INT,
  cost_usd DECIMAL(10, 6),  -- calculated from rates
  created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_jobs_org_id ON jobs(organization_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_reports_job_id ON reports(job_id);
CREATE INDEX idx_audit_org_id ON audit_logs(organization_id);
CREATE INDEX idx_llm_usage_org_id ON llm_usage(organization_id);
```

### 4.2 S3 Structure

```
s3://bs-detector-prod/
├── {org_id}/
│   ├── {job_id}/
│   │   ├── documents.zip  (user upload)
│   │   ├── msj.txt        (extracted)
│   │   ├── police_report.txt
│   │   └── ...
│   └── ...
```

**Lifecycle policy:**
- Transition to Glacier after 30 days (audit compliance)
- Delete after 90 days (CCPA retention limit)

### 4.3 Connection Pooling

- **PostgreSQL**: Use `sqlalchemy.pool.QueuePool` (default)
- **Connection limit**: 20–50 per Celery worker (depends on load)
- **Timeout**: 5s acquire, 30s idle before recycle

---

## 5. Security & Compliance

### 5.1 Data Isolation (CCPA)

- Every query includes `WHERE organization_id = ?` (tenant isolation)
- No cross-org queries (enforced in ORM)
- Document deletion on request: cascade delete from S3 + PostgreSQL

### 5.2 Encryption

- **In transit**: TLS 1.2+ (enforced at load balancer)
- **At rest (S3)**: AWS KMS server-side encryption
- **At rest (PostgreSQL)**: EBS encryption (AWS managed keys)
- **In application**: No plaintext logging of document content

### 5.3 Authentication

- OAuth2 (via Cognito or similar)
- API key for programmatic access (hashed in database)
- Session timeout: 1 hour idle

### 5.4 Rate Limiting

Implemented at FastAPI gateway level:

```python
@app.post("/analyze")
async def analyze(request: AnalyzeRequest):
    org_id = get_org_from_token(request.auth)
    
    # Check quota
    remaining_analyses = check_quota(org_id, "analyses_per_day")
    remaining_tokens = check_quota(org_id, "tokens_per_day")
    
    if remaining_analyses <= 0:
        # Queue request with priority
        job = enqueue_job(org_id, priority="low", request)
        return {"job_id": job.id, "status": "queued"}
    
    # Proceed
    ...
```

---

## 6. Infrastructure

### 6.1 Deployment Stack

- **Container orchestration**: Kubernetes (EKS on AWS) or Docker Swarm
- **API gateway**: FastAPI + Gunicorn (8 workers per instance)
- **Message queue**: RabbitMQ (managed or self-hosted)
- **Workers**: Celery (2–8 workers per instance, depending on concurrency model)
- **Database**: AWS RDS PostgreSQL (Multi-AZ for HA)
- **Storage**: AWS S3 with lifecycle policies
- **Monitoring**: CloudWatch + Prometheus + Grafana
- **Logging**: CloudWatch Logs + ELK stack (optional)

### 6.2 Scaling Strategy

**Horizontal scaling:**
- API instances: auto-scale based on request queue depth (target: <500ms p99)
- Celery workers: auto-scale based on queue length (target: <5s job start latency)
- RabbitMQ: cluster mode (3 nodes minimum for HA)

**Vertical scaling:**
- Initial: `t3.large` instances (2 CPU, 8GB RAM)
- Monitor: CPU, memory, network I/O
- Scale up if p99 latency > 60s or queue depth > 1000

### 6.3 Load Balancer

- ALB (Application Load Balancer) with health checks every 5s
- Sticky sessions disabled (stateless API)
- WAF rules: rate limit by IP (100 req/min), by API key (1000 req/min)

---

## 7. Reliability & Error Handling

### 7.1 Streaming Response (Server-Sent Events)

The prototype uses SSE to stream agent progress in real-time:

```
Client request: POST /analyze
Server response: text/event-stream
Timeline:
- Event: {"stage": "extracting", "message": "..."}
- Event: {"stage": "citations_extracted", "count": 11}
- Event: {"stage": "verifying", "message": "..."}
- Event: {"stage": "citations_verified", "count": 11, "problematic": 3}
- Event: {"stage": "checking_facts", "message": "..."}
- Event: {"stage": "facts_checked", "count": 3}
- Event: {"stage": "summarizing", "message": "..."}
- Event: {"stage": "complete", "report": {...}}
```

**Advantages:**
- User sees real-time progress (no "analyzing..." limbo)
- Early failure detection (if agent fails, client sees it immediately)
- Better UX for long-running analyses

**For Celery/async in production:**
- Replace SSE with job queue + polling or WebSocket
- Client polls `/job/{job_id}` status endpoint
- Or WebSocket for bidirectional updates

### 7.2 Job Retry Strategy

**Celery task configuration:**

```python
@app.task(
    max_retries=3,
    autoretry_for=(APIError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=600,  # max 10 min between retries
    retry_jitter=True,
)
def analyze_document(job_id):
    ...
```

**Manual retry:** User can re-submit job via UI with option to change provider/model.

### 7.2 Circuit Breaker (Provider Level)

If Gemini API returns 5xx for 10 consecutive requests, skip to OpenAI:

```python
class ProviderCircuitBreaker:
    def __init__(self, provider, failure_threshold=10, timeout_duration=300):
        self.provider = provider
        self.failure_count = 0
        self.last_failure_time = None
        self.is_open = False
    
    def record_failure(self):
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.is_open = True
            self.last_failure_time = now()
    
    def can_try(self):
        if not self.is_open:
            return True
        # Half-open after timeout
        if now() - self.last_failure_time > self.timeout_duration:
            self.is_open = False
            self.failure_count = 0
            return True
        return False
```

---

## 8. Observability & Monitoring

### 8.1 Metrics (per Celery task)

**Per-agent metrics:**
```
bs_detector_agent_latency_seconds{agent="citation_verifier", org_id="X"}
bs_detector_agent_errors_total{agent="citation_verifier", error_type="api_error"}
bs_detector_agent_retry_total{agent="citation_verifier"}
```

**LLM usage:**
```
bs_detector_llm_tokens_total{provider="gemini", model="3.1-flash", org_id="X"}
bs_detector_llm_cost_usd_total{provider="gemini", org_id="X"}
```

**Job lifecycle:**
```
bs_detector_job_queue_depth{priority="high"}
bs_detector_job_latency_seconds{status="completed"}
bs_detector_job_errors_total{error_reason="provider_failure"}
```

### 8.2 Dashboards

**Operations dashboard:**
- Queue depth over time (alert if > 1000)
- Job success rate (alert if < 95%)
- API p99 latency (alert if > 60s)
- RabbitMQ/PostgreSQL health

**Cost dashboard:**
- LLM spend per org per day
- Tokens per model per provider
- Cost per analysis

### 8.3 Logging

**Structured logs (JSON) with fields:**
```json
{
  "timestamp": "2026-06-24T10:30:45Z",
  "level": "INFO",
  "job_id": "uuid",
  "org_id": "uuid",
  "agent": "citation_verifier",
  "event": "citation_verified",
  "citation": "Privette v. Superior Court",
  "verdict": "does_not_support",
  "confidence": 1.0,
  "latency_ms": 2340,
  "provider": "gemini",
  "model": "3.1-flash"
}
```

**No document content in logs** (PII/confidentiality).

---

## 9. Cost Controls

### 9.1 Per-Organization Quotas

```sql
UPDATE quotas 
SET analyses_per_day = 100, tokens_per_day = 5000000
WHERE organization_id = 'org-123';
```

**Enforcement:**
- Check before enqueuing job
- If over quota, queue with low priority (processed after reset)
- Daily reset at midnight UTC

### 9.2 Cost Tracking

**Real-time:**
- Log every LLM call with provider, model, token counts
- Calculate cost using published rate cards (Gemini: $0.075/1M input, OpenAI: varies)
- Update `organizations.monthly_spend` every 100 calls

**Billing:**
- Export `llm_usage` table monthly to billing system
- Alert if spend > $10k/month for any org (configurable)

### 9.3 Model Selection for Cost

**Default strategy:**
1. Try `gemini-3.1-flash` (cheapest, ~$0.075/1M tokens)
2. If provider down, try `gpt-4o-mini` (~$0.15/1M tokens)
3. Log cost difference for post-mortem

---

## 10. Implementation Roadmap

### Phase 1: MVP (Weeks 1–4)
**Goal:** Async job pipeline with basic monitoring

- [ ] Celery + RabbitMQ setup (local/staging)
- [ ] PostgreSQL schema (jobs, reports, audit_logs)
- [ ] S3 integration (upload, lifecycle policy)
- [ ] Job submission endpoint (async)
- [ ] Job status polling endpoint
- [ ] Basic error handling (graceful degradation)
- [ ] CloudWatch metrics (job latency, error rate)

**Success criteria:**
- 100 concurrent jobs without queuing
- <5s job start latency
- 0 document leaks (audit log confirms deletion)

### Phase 2: Multi-Provider & Reliability (Weeks 5–8)
**Goal:** Provider fallback, circuit breaker, retry logic

- [ ] Implement provider registry (config-driven fallback)
- [ ] Circuit breaker for each provider
- [ ] Celery task retry with exponential backoff
- [ ] Cost tracking (llm_usage table)
- [ ] Dashboard (cost, queue depth, success rate)

**Success criteria:**
- Fallback to OpenAI if Gemini down
- <1% task loss (all jobs eventually complete)
- Cost tracking accurate within 1%

### Phase 3: Security & Scale (Weeks 9–12)
**Goal:** Multi-tenancy, quota enforcement, HA

- [ ] Tenant isolation (organization_id in all queries)
- [ ] Rate limiting (analyses/day, tokens/day)
- [ ] CCPA deletion workflow (request → S3 + DB cleanup)
- [ ] RabbitMQ clustering (3 nodes)
- [ ] PostgreSQL Multi-AZ failover
- [ ] Load testing (1000 concurrent users)

**Success criteria:**
- Zero cross-org data leaks
- Quota enforcement = 100% accurate
- RTO < 5 min, RPO < 1 min

### Phase 4: Production Hardening (Weeks 13+)
**Goal:** Runbooks, SLA compliance, cost optimization

- [ ] Kubernetes migration (EKS)
- [ ] Autoscaling policies (CPU/queue-based)
- [ ] Runbook for on-call (PagerDuty integration)
- [ ] Cost optimization (reserved instances, spot instances for workers)
- [ ] Quarterly eval harness on production sample

---

## 11. Tradeoffs & Decisions

### Tradeoff 1: Async vs Sync

**Decision: Async (Celery + RabbitMQ)**

- **Pro:** Can handle 1000+ analyses without blocking API; graceful degradation if LLM slow
- **Con:** Complexity (job state, polling, WebSocket for real-time updates)
- **Why:** Legal work is time-sensitive but not real-time; 30s latency acceptable

### Tradeoff 2: Single Database vs Per-Org Sharding

**Decision: Single PostgreSQL with tenant_id**

- **Pro:** Simpler ops, easier backups, ACID guarantees
- **Con:** Single point of failure; harder to scale >100k orgs
- **Why:** the client organization likely <1000 orgs initially; sharding deferred

### Tradeoff 3: Store Documents vs References

**Decision: Store in S3 with 90-day retention**

- **Pro:** Audit compliance, enables re-analysis, CCPA-compliant deletion
- **Con:** Storage costs (~$0.023/GB/month)
- **Why:** Legal discovery requires document history; costs negligible at scale

### Tradeoff 4: Model Fallback Granularity

**Decision: Same provider → cheaper model first, then switch provider**

- **Pro:** Minimizes cost (stay on Gemini if possible); minimizes latency (avoid provider switch)
- **Con:** More complex config; need to maintain model hierarchy
- **Why:** Cost matters more than pure availability; user can override manually

### Open Question: Evaluation in Production

**Current approach:** Run eval harness on 5% sample of completed reports monthly.

**Better approach:** Build continuous eval pipeline (LLM-as-judge) that scores reports against expert annotations. Requires labeled dataset (~100 reports).

**Decision deferred** to Phase 2 (need production data first).

---

## 12. Non-Goals (Out of Scope for MVP)

- Real-time document editing (upload, analyze, done)
- Multi-document comparison (all docs analyzed together)
- Integration with Westlaw/LexisNexis (citation validation against real databases)
- Custom model fine-tuning (use off-the-shelf models)
- Workflow automation (users trigger manually via UI)

These are valuable but add complexity. Sequence them after Phase 1 MVP.

---

## Summary

This production system balances:

- **Reliability:** Async queue, graceful degradation, multi-provider fallback
- **Security:** Tenant isolation, encryption, CCPA compliance, audit logs
- **Cost:** Per-org quotas, model selection, token tracking
- **Simplicity:** Avoid microservices excess; stick to Postgres + Celery + S3

The roadmap is realistic: MVP in 4 weeks, HA + scale in 12 weeks.
