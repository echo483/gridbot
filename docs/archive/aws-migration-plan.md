# AWS Learning Path: Migration Plan

**System**: Python 3.13 trading research (esports + prediction markets)  
**Current**: Debian homelab, systemd daemons, local JSONL, Supabase Postgres  
**Goal**: Practical AWS knowledge that is hirable

---

## Deliverables

1. **docs/aws-learning-path.md** — Opinionated learning path (services, migration order, costs, certifications)
2. **docs/aws-migration-decisions.md** — Architecture decisions log (rationale, trade-offs)
3. **docs/aws-migration-checklist.md** — Verification checklist for each step
4. **docs/aws-migration-commands.md** — CLI snippets and code examples

---

## Migration Steps (Execute in Order)

### Step 1: EC2 Lift-and-Shift (~2 hours)
Move data polling daemon to EC2 t4g.micro.
- Learn: EC2 lifecycle, IAM roles, security groups, CloudWatch Logs
- Break: File paths, secrets access, logging destination
- Cost: ~$3.50/month
- Verify: Daemon runs, logs stream to CloudWatch, cost <$4

### Step 2: S3 Offload (~1 hour)
Move JSONL day-files to S3.
- Learn: S3 buckets, IAM policies, lifecycle rules
- Break: File writes become S3 PUTs, analysis scripts need boto3
- Cost: ~$0.30/month
- Verify: Files in S3, lifecycle policy active, cost <$1

### Step 3: Lambda Analysis (~2 hours)
Move batch analysis to Lambda triggered by EventBridge.
- Learn: Lambda packaging, IAM execution roles, cold starts
- Break: Stateful assumptions, local file access
- Cost: ~$0.50/month
- Verify: Lambda runs on schedule, outputs JSON report, cost <$1

### Step 4: Observability (~30 minutes)
Add CloudWatch Alarms + SNS notifications.
- Learn: Metric filters, composite alarms, SNS topics
- Break: Reactive monitoring becomes proactive alerts
- Cost: ~$0.20/month
- Verify: Alarm triggers test notification, cost <$1

### Step 5: DNS + Health (~30 minutes)
Route 53 with health checks.
- Learn: DNS TTLs, health check thresholds, failover routing
- Break: Local DNS resolution
- Cost: ~$0.50/month
- Verify: DNS resolves globally, health check passes, cost <$1

**Total time**: ~6 hours  
**Total cost**: ~$5-7/month (alarm if >$15)

---

## What Stays on Homelab

- **Order placement daemon** — latency-sensitive (sub-ms vs 50-100ms cloud RTT)
- **Cold JSONL archive** — data gravity (egress costs exceed local storage)
- **OPNsense firewall** — physical network boundary control

---

## Bill Guardrails (MUST DO FIRST)

1. **AWS Budget** with alerts at $10, $20, $50
2. **S3 access logs** + metric filter on RequestCount (alarm at 1M/day)
3. **Lambda timeout** set to 5 minutes (no runaway executions)

---

## Learning Outcomes

After completing all steps, you will understand:
- IAM boundaries (vs local root)
- Eventual consistency (vs local FS)
- Cold starts (vs always-on daemon)
- Egress economics (vs local storage)
- Serverless vs VM trade-offs
- Cloud observability patterns

These are hirable skills. The homelab remains your low-latency edge and cold storage anchor.

---

## Next Steps

1. Read `docs/aws-learning-path.md` for the full rationale
2. Review `docs/aws-migration-decisions.md` for architecture context
3. Follow `docs/aws-migration-checklist.md` during migration
4. Use `docs/aws-migration-commands.md` for CLI snippets

**Start with Step 1. Break something. Fix it. That's the curriculum.**
