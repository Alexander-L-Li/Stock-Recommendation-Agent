# Deployment

This deploys the agent as a daily Lambda using AWS SAM. Everything stays within
the AWS free tier for a once-daily job.

## Prerequisites

- AWS account + credentials configured (`aws configure` / SSO).
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html).
- A Reddit **script** app for OAuth creds: https://www.reddit.com/prefs/apps
  → "create another app" → type *script*. Note the client id + secret.
- SES sender/recipient verified — see [SES_SETUP.md](SES_SETUP.md).

## 1. Build the Lambda artifact

The handler package lives under `src/`, so we assemble a deployment dir with the
`stock_agent` package and its dependencies at the archive root:

```bash
./deploy/build_lambda.sh
```

This produces `build/lambda/` (used by the SAM template's `CodeUri`) and
`build/stock-agent-lambda.zip` (for direct `update-function-code`).

> **Binary wheels / runtime match:** the runtime deps here are pure-Python, so a
> local build works. If you ever add a dependency with compiled extensions,
> build on a Cloud Desktop / AmazonLinux container, or add
> `--platform manylinux2014_x86_64 --only-binary=:all:` to the pip install in
> `build_lambda.sh`, so the wheels match the Lambda runtime.

## 2. Deploy with SAM

```bash
sam deploy --guided -t infra/template.yaml
```

You'll be prompted for the parameters:

| Parameter | Example |
|---|---|
| `SenderEmail` | `you@example.com` (SES-verified) |
| `RecipientEmails` | `you@example.com` |
| `RedditClientId` / `RedditClientSecret` | from your Reddit script app |
| `ScheduleExpression` | `cron(0 12 * * ? *)` (12:00 UTC daily) |
| `AlarmEmail` | `you@example.com` (gets CloudWatch error alerts via SNS) |

The stack creates:

- **DynamoDB** table `stock-agent` (single-table watchlist + history).
- **Lambda** `stock-agent` with env vars and least-privilege IAM
  (`DynamoDBCrudPolicy` + `ses:SendEmail`).
- **EventBridge** schedule (daily).
- **CloudWatch alarm** on Lambda `Errors` → **SNS topic** `stock-agent-alarms`
  (confirm the SNS email subscription from your inbox after first deploy).

## 3. Seed your watchlist

```bash
TABLE_NAME=stock-agent AWS_REGION=us-east-1 \
  python -m stock_agent.cli watchlist add NVDA
```

(Discovery from Reddit/news runs automatically; the watchlist just guarantees
certain tickers are always scored.)

## 4. Trigger a manual run

```bash
aws lambda invoke --function-name stock-agent --region us-east-1 /tmp/out.json
cat /tmp/out.json
```

You should receive the report email and see a `RUN#<date>` set of items in
DynamoDB. Errors trigger the CloudWatch alarm + a best-effort failure email.

## 5. Update code later

```bash
./deploy/build_lambda.sh
aws lambda update-function-code --function-name stock-agent \
  --zip-file fileb://build/stock-agent-lambda.zip --region us-east-1
```

## Adjusting the schedule / weights

Schedule is the `ScheduleExpression` parameter (EventBridge cron, **UTC**).
Scoring weights, the hype gate, subreddits, and feeds are all environment
variables on the function — update them in the template or directly:

```bash
aws lambda update-function-configuration --function-name stock-agent \
  --environment "Variables={TABLE_NAME=stock-agent,SENDER_EMAIL=...,HYPE_GATE_MIN_FUNDAMENTALS=45}" \
  --region us-east-1
```

## Teardown

```bash
sam delete --stack-name <your-stack-name>
```

> Note: deleting the stack removes the DynamoDB table and its history. Export
> first if you want to keep it.
