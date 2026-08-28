# AWS Migration Verification Checklist

Use this to validate each migration step before proceeding.

## Step 1: EC2 Lift-and-Shift
- [ ] EC2 instance running (t4g.micro, ARM64)
- [ ] IAM role attached with minimal permissions
- [ ] Security group allows only necessary inbound (SSH via bastion)
- [ ] Secrets Manager populated, daemon reads via boto3
- [ ] CloudWatch Logs streaming (no journald dependency)
- [ ] Cost: <$4/month (verified in Cost Explorer)
- [ ] Daemon survives instance reboot (systemd enabled)

## Step 2: S3 Offload
- [ ] JSONL writes use boto3 S3 client (not local FS)
- [ ] Bucket versioning enabled
- [ ] Lifecycle policy: transition to IA after 30 days
- [ ] IAM policy scoped to bucket prefix only
- [ ] Analysis scripts read from S3 (not local)
- [ ] Cost: <$1/month (verified)
- [ ] Test: retrieve file from S3, validate checksum

## Step 3: Lambda Analysis
- [ ] Lambda function packaged with dependencies (zip or layer)
- [ ] IAM execution role has S3 read + Secrets Manager access
- [ ] Timeout set to 5 minutes (not default 3 seconds)
- [ ] Memory: 1GB (matches local script needs)
- [ ] EventBridge rule triggers on schedule (cron expression)
- [ ] Logs appear in CloudWatch Logs (not stdout lost)
- [ ] Cost: <$1/month (verified)
- [ ] Test: invoke manually, validate output JSON

## Step 4: Observability
- [ ] CloudWatch metric filters on error patterns
- [ ] Alarms configured (e.g., "no data captured in 1 hour")
- [ ] SNS topic with SMS/email subscription
- [ ] Alarm triggers test notification
- [ ] Cost: <$1/month (verified)

## Step 5: DNS + Health
- [ ] Route 53 hosted zone created
- [ ] Health check monitors EC2 endpoint
- [ ] DNS resolves globally (test from multiple regions)
- [ ] Failover routing configured (if multi-instance later)
- [ ] Cost: <$1/month (verified)

## Bill Guardrails (MUST DO)
- [ ] AWS Budget created with alerts at $10, $20, $50
- [ ] Cost Explorer shows current month < $15
- [ ] S3 access logs enabled + metric filter on RequestCount
- [ ] Lambda timeout enforced (no runaway executions)
- [ ] No cross-AZ data transfer (single AZ deployment)

## Homelab Retention Verification
- [ ] Order placement daemon still runs locally
- [ ] Latency test: local execution < 1ms, cloud path > 50ms
- [ ] Cold JSONL archive on ZFS/NAS (not in S3)
- [ ] OPNsense firewall rules documented

## Final Sign-off
- [ ] All steps verified in order
- [ ] Total monthly cost <$15
- [ ] No unnecessary services added
- [ ] System functions identically to local (except order daemon)
- [ ] Documentation updated (this checklist completed)
