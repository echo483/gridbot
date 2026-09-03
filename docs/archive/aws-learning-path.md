# AWS Learning Path: Migrating a Trading Research System

**Target**: Python 3.13 trading system (esports data + prediction market)  
**Current**: Debian homelab (Proxmox + OPNsense), systemd daemons, local JSONL, Supabase Postgres  
**Goal**: Practical AWS knowledge that is genuinely useful AND hirable

---

## 1. Minimal AWS Service Set (High-Leverage Only)

| Service | Replaces Locally | Why It Matters |
|---------|-----------------|----------------|
| **EC2 (t4g.micro/t4g.small)** | Debian VM running daemons | Compute foundation. Learn IAM roles, security groups, instance types. ARM (Graviton) = 20% cheaper, forces architecture awareness. |
| **S3** | Local JSONL day-files | Object storage. Immutable, versioned, durable. Teaches eventual consistency, lifecycle policies, cost tiers. |
| **Lambda** | Batch analysis scripts | Serverless compute. Forces stateless design, teaches cold starts, IAM boundaries. |
| **EventBridge** | systemd timers | Event-driven orchestration. Decouples scheduling from execution. |
| **Secrets Manager** | OS keyring | Centralized secrets. Rotation patterns, IAM access policies. |
| **CloudWatch Logs** | journald logs | Centralized logging. Retention, filtering, alarms. |
| **CloudWatch Alarms** | Manual monitoring | Observability. Metric thresholds, SNS notifications. |
| **Route 53** | Local DNS | DNS management. Health checks, failover routing. |

**Reject**: RDS (you already have Supabase), EKS (overkill), DynamoDB (no NoSQL need), SQS/SNS (no async messaging yet).

---

## 2. What Stays on Homelab (Permanently)

**Order Placement Daemon** — stays local  
- **Why**: Latency. Even 50-100ms cloud roundtrip can miss fills in thin markets. Local execution = deterministic sub-millisecond response.  
- **Pattern**: Homelab places orders; cloud does everything else (data capture, analysis, monitoring).

**Raw JSONL Archive** — stays local (cold tier)  
- **Why**: Data gravity. 80k rows/day × 365 = ~70GB/year. At-rest storage is cheap locally; egress from S3 costs $0.09/GB.  
- **Pattern**: Hot data (last 30 days) in S3 for analysis; cold archive on homelab ZFS/NAS.

**OPNsense Firewall** — stays local  
- **Why**: Network boundary control. Cloud VPC is abstract; physical firewall teaches packet filtering, IDS/IPS, VLANs.

---

## 3. Migration Order (Learn by Breaking)

### Step 1: Lift-and-Shift Capture Daemon
- **What moves**: Data polling daemon (esports + prediction market APIs) → EC2 t4g.micro  
- **What breaks**: File paths (local → EBS mount), secrets (keyring → Secrets Manager), logging (journald → CloudWatch)  
- **What you learn**: EC2 lifecycle, IAM roles, security groups, SSH bastion, CloudWatch Logs  
- **Cost**: ~$3.50/month (t4g.micro 24/7 + 10GB EBS)

### Step 2: Offload Storage
- **What moves**: JSONL day-files → S3 (hot tier: last 30 days)  
- **What breaks**: File writes become S3 PUTs; analysis scripts need boto3  
- **What you learn**: S3 buckets, IAM policies, eventual consistency, lifecycle rules (hot → IA after 30d)  
- **Cost**: ~$0.30/month (1GB hot + 2GB IA)

### Step 3: Serverless Analysis
- **What moves**: Batch analysis scripts → Lambda (triggered by EventBridge cron)  
- **What breaks**: Stateful assumptions (Lambda is ephemeral), local file access (use /tmp or S3)  
- **What you learn**: Lambda packaging, IAM execution roles, cold starts, timeout patterns  
- **Cost**: ~$0.50/month (10k invocations, 1GB RAM, 5min avg)

### Step 4: Observability
- **What moves**: Manual log checks → CloudWatch Alarms + SNS SMS  
- **What breaks**: Reactive monitoring becomes proactive alerts  
- **What you learn**: Metric filters, composite alarms, SNS topics, cost of verbosity  
- **Cost**: ~$0.20/month (10 alarms, low SMS volume)

### Step 5: DNS + Health Checks
- **What moves**: Local hostname → Route 53 with health checks  
- **What breaks**: Local DNS resolution; now globally resolvable  
- **What you learn**: DNS TTLs, health check thresholds, failover routing  
- **Cost**: ~$0.50/month (1 hosted zone, health checks)

**Total at scale**: ~$5-7/month. If it exceeds $15, you've added unnecessary services.

---

## 4. Three Bill Surprises (and Guardrails)

| Surprise | Guardrail |
|----------|-----------|
| **S3 GET requests** — 100k GETs = $0.004, but analysis scripts can generate millions | Set S3 access logs + CloudWatch metric filter on `RequestCount`. Alarm at 1M requests/day. |
| **Lambda duration** — Unbounded loops or slow APIs burn GB-seconds | Set Lambda timeout to 5 minutes. Use Provisioned Concurrency only after profiling. |
| **Data transfer** — Egress from S3 to homelab for backtesting | Use S3 Batch Operations instead of individual GETs. Archive cold data locally; keep only hot tier in S3. |

**Pro move**: Set AWS Budgets with alerts at $10, $20, $50. Kill switch: delete IAM policies if budget breached.

---

## 5. Docker vs Kubernetes

**Recommendation**: Docker (with ECS Fargate later), skip Kubernetes entirely.

**Why**:
- **Docker** teaches containerization, image layers, volume mounts, networking — all directly applicable to your workload.  
- **Kubernetes** adds 10x complexity for 0x benefit here. No multi-node orchestration need. No autoscaling need.  
- **As learning exercise**: Not worth it. Time spent on k8s YAML could be spent on IAM policies, which actually secure systems.  
- **Path**: Dockerize locally → ECS Fargate (serverless containers) if you outgrow Lambda's 15min limit. Skip EC2 container deployment entirely.

---

## 6. Certifications (Brutal Honesty)

| Certification | Worth It? | Why |
|---------------|-----------|-----|
| **AWS Certified Cloud Practitioner** | ❌ No | Too shallow. You'll learn more shipping Step 1. |
| **AWS Solutions Architect – Associate** | ✅ Yes | Covers EC2, S3, Lambda, IAM, VPC — exactly your migration path. Study while doing Steps 1-3. Exam validates architecture decisions. |
| **AWS Developer – Associate** | ⚠️ Maybe | Heavy on Lambda, DynamoDB, CI/CD. Only pursue if you plan heavy serverless expansion. |
| **AWS SysOps Administrator** | ❌ No | Legacy ops focus (patching, CloudFormation). Less relevant for modern serverless-leaning systems. |

**Verdict**: Ship the system first. If you complete Steps 1-3 and want credential validation, SAA is the one. Time investment: 80 hours study + $150 exam. ROI: interview credibility, not technical capability.

---

## Final Word

This path teaches **real cloud economics** (you'll feel the pain of GET requests), **security boundaries** (IAM is harder than firewalls), and **distributed systems constraints** (eventual consistency, cold starts). 

The homelab stays relevant: it's your low-latency edge and your cold storage anchor. Cloud is your scale layer and your hiring signal.

**Start with Step 1. Break something. Fix it. That's the curriculum.**
