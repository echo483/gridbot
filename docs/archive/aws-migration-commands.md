# AWS Migration Commands & Snippets

Quick reference for each migration step.

## Step 1: EC2 Lift-and-Shift

### Launch t4g.micro (ARM64)
```bash
# Create IAM role for EC2
aws iam create-role --role-name TradingDaemonRole --assume-role-policy-document '{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}'

# Attach policies (minimal)
aws iam attach-role-policy --role-name TradingDaemonRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam attach-role-policy --role-name TradingDaemonRole \
  --policy-arn arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy

# Launch instance
aws ec2 run-instances \
  --image-id ami-0abcdef1234567890 \  # Amazon Linux 2023 ARM64
  --instance-type t4g.micro \
  --key-name your-key \
  --iam-instance-profile Name=TradingDaemonRole \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=trading-daemon}]'

# Get public IP
aws ec2 describe-instances --filters "Name=tag:Name,Values=trading-daemon" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text
```

### Secrets Manager
```bash
# Store API keys
aws secretsmanager create-secret --name trading/grid-api-key --secret-string "your-key"
aws secretsmanager create-secret --name trading/polymarket-api-key --secret-string "your-key"

# Python read (in daemon)
import boto3
secrets = boto3.client('secretsmanager', region_name='us-east-1')
grid_key = secrets.get_secret_value(SecretId='trading/grid-api-key')['SecretString']
```

### CloudWatch Logs
```bash
# Install CloudWatch agent
sudo yum install amazon-cloudwatch-agent -y
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 -s -c ssm:AmazonCloudWatch-linux

# Or use systemd journal upload
sudo yum install awslogs -y
sudo systemctl enable awslogs
sudo systemctl start awslogs
```

---

## Step 2: S3 Offload

### Create Bucket
```bash
aws s3 mb s3://trading-data-$(date +%Y%m%d%H%M%S)
aws s3api put-bucket-versioning --bucket trading-data-xxxxxx \
  --versioning-configuration Status=Enabled
```

### Lifecycle Policy
```json
// lifecycle.json
{
  "Rules": [{
    "ID": "ArchiveOldData",
    "Status": "Enabled",
    "Prefix": "",
    "Transitions": [{
      "Days": 30,
      "StorageClass": "STANDARD_IA"
    }],
    "NoncurrentVersionTransitions": [{
      "NoncurrentDays": 30,
      "StorageClass": "GLACIER"
    }]
  }]
}
```
```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket trading-data-xxxxxx --lifecycle-configuration file://lifecycle.json
```

### Python Write
```python
import boto3, json
s3 = boto3.client('s3')
s3.put_object(
    Bucket='trading-data-xxxxxx',
    Key=f'capture/{date}/{timestamp}.jsonl',
    Body='\n'.join(json.dumps(row) for row in rows)
)
```

---

## Step 3: Lambda Analysis

### Package Function
```bash
# Create deployment package
mkdir lambda-package && cd lambda-package
pip install -r requirements.txt -t .
cp ../analysis.py .
zip -r ../function.zip .

# Deploy
aws lambda create-function \
  --function-name trading-analysis \
  --runtime python3.11 \
  --handler analysis.handler \
  --zip-file fileb://function.zip \
  --role arn:aws:iam::123456789012:role/LambdaExecutionRole \
  --timeout 300 \
  --memory-size 1024
```

### EventBridge Schedule
```bash
aws events put-rule \
  --name daily-analysis \
  --schedule-expression "cron(0 2 * * ? *)"  # 2 AM UTC daily

aws events put-targets \
  --rule daily-analysis \
  --targets "Id"="1","Arn"="arn:aws:lambda:us-east-1:123456789012:function:trading-analysis"
```

### Lambda Handler
```python
import boto3, json

def handler(event, context):
    s3 = boto3.client('s3')
    # Read last 30 days from S3
    # Run analysis
    # Write report to S3
    return {'statusCode': 200}
```

---

## Step 4: Observability

### CloudWatch Metric Filter
```bash
aws logs put-metric-filter \
  --log-group-name "/aws/ec2/trading-daemon" \
  --filter-name "ErrorCount" \
  --filter-pattern "ERROR" \
  --metric-transformations metricName=ErrorCount,metricNamespace=Trading,metricValue=1
```

### Alarm
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "TradingDaemonErrors" \
  --metric-name ErrorCount \
  --namespace Trading \
  --statistic Sum \
  --period 300 \
  --threshold 5 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 1 \
  --alarm-actions arn:aws:sns:us-east-1:123456789012:trading-alerts
```

### SNS Topic
```bash
aws sns create-topic --name trading-alerts
aws sns subscribe \
  --topic-arn arn:aws:sns:us-east-1:123456789012:trading-alerts \
  --protocol sms \
  --notification-endpoint "+1234567890"
```

---

## Step 5: Route 53

### Hosted Zone
```bash
aws route53 create-hosted-zone --name trading.example.com --caller-reference $(date +%s)
```

### Health Check
```bash
aws route53 create-health-check --caller-reference $(date +%s) \
  --health-check-config '{
    "IPAddress": "EC2_PUBLIC_IP",
    "Port": 80,
    "Type": "HTTP",
    "ResourcePath": "/health",
    "RequestInterval": 30,
    "FailureThreshold": 3
  }'
```

---

## Bill Guardrails

### Budget Alerts
```bash
aws budgets create-budget \
  --account-id 123456789012 \
  --budget '{
    "BudgetName": "TradingSystem",
    "BudgetType": "COST",
    "BudgetLimit": {"Amount": "50", "Unit": "USD"},
    "TimeUnit": "MONTHLY",
    "BudgetRules": [{
      "BudgetRuleName": "AlertAt10",
      "Notification": {"NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN", "Threshold": 10},
      "Subscribers": [{"SubscriptionType": "EMAIL", "Address": "you@example.com"}]
    }]
  }'
```

### S3 Access Logs
```bash
aws s3api put-bucket-logging \
  --bucket trading-data-xxxxxx \
  --bucket-logging-status '{
    "LoggingEnabled": {
      "TargetBucket": "trading-logs-xxxxxx",
      "TargetPrefix": "access-logs/"
    }
  }'
```

---

## Cost Verification

### Monthly Estimate
```bash
# After deployment, check actual spend
aws ce get-cost-and-usage \
  --time-period Start=$(date -d 'this month' +%Y-%m-01),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics AmortizedCost \
  --group-by Type=DIMENSION,Key=SERVICE
```

**Target**: <$15/month total. If exceeded, review:
- S3 request count (GET/PUT)
- Lambda duration (GB-seconds)
- Data transfer (egress)
