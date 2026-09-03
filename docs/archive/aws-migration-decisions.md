# AWS Migration Decision Log

## Context
- System: Python 3.13 trading research (esports + prediction markets)
- Current: Debian homelab, systemd daemons, local JSONL, Supabase Postgres
- Goal: Practical AWS knowledge that is hirable, not resume padding

## Decisions

### Minimal Service Set
Selected 8 services that directly replace local components:
- EC2 (compute), S3 (storage), Lambda (batch), EventBridge (scheduling)
- Secrets Manager (secrets), CloudWatch (logs/alarms), Route 53 (DNS)

Rejected RDS (Supabase exists), EKS (overkill), DynamoDB (no need), SQS/SNS (no async yet).

### Homelab Retention
- **Order daemon stays local**: Latency-sensitive (sub-ms vs 50-100ms cloud RTT)
- **Cold JSONL stays local**: Data gravity (egress costs exceed local storage)
- **OPNsense stays local**: Physical network boundary control

### Migration Order
5 steps, each teaching one core concept:
1. EC2 lift-and-shift (compute/IAM)
2. S3 offload (storage/lifecycle)
3. Lambda analysis (serverless/stateless)
4. CloudWatch observability (metrics/alarms)
5. Route 53 DNS (global resolution)

Total cost target: <$15/month. If exceeded, unnecessary services added.

### Bill Guardrails
1. S3 GET requests → metric filter + alarm at 1M/day
2. Lambda duration → 5min timeout, no provisioned concurrency until profiled
3. Data transfer → batch operations, cold archive locally

### Docker vs Kubernetes
Docker only. ECS Fargate later if Lambda limits hit. Kubernetes is 10x complexity for 0x benefit here; not worth as learning exercise.

### Certifications
- Cloud Practitioner: ❌ too shallow
- Solutions Architect Associate: ✅ covers exact migration path
- Developer Associate: ⚠️ only if heavy serverless expansion planned
- SysOps: ❌ legacy ops focus

Verdict: Ship system first. SAA only if credential validation needed post-migration.

## Rationale
This path forces confrontation with real cloud constraints:
- IAM boundaries (vs local root)
- Eventual consistency (vs local FS)
- Cold starts (vs always-on daemon)
- Egress economics (vs local storage)

Homelab remains the low-latency edge and cold storage anchor. Cloud is the scale layer and hiring signal.

## References
- AWS Pricing Calculator: https://calculator.aws/
- Well-Architected Framework: https://aws.amazon.com/architecture/well-architected/
- S3 Cost Optimization: https://aws.amazon.com/s3/storage-classes/
