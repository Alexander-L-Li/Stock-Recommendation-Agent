#!/usr/bin/env bash
# Deploy the stock-agent as a daily Lambda using only the AWS CLI (no SAM/Docker).
# Idempotent-ish: creates resources if missing, updates code if the function
# already exists. Requires AWS credentials with IAM/Lambda/EventBridge/SNS/
# CloudWatch/DynamoDB/SES permissions.
#
# Usage:
#   SENDER_EMAIL=you@example.com RECIPIENT_EMAILS=you@example.com \
#   ALARM_EMAIL=you@example.com bash deploy/deploy_aws.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION="${AWS_REGION:-us-east-1}"
FUNCTION="${FUNCTION_NAME:-stock-agent}"
TABLE="${TABLE_NAME:-stock-agent}"
ROLE="${ROLE_NAME:-stock-agent-role}"
RULE="${RULE_NAME:-stock-agent-daily}"
TOPIC="${TOPIC_NAME:-stock-agent-alarms}"
SCHEDULE="${SCHEDULE_EXPRESSION:-cron(0 12 * * ? *)}"
SENDER_EMAIL="${SENDER_EMAIL:?set SENDER_EMAIL}"
RECIPIENT_EMAILS="${RECIPIENT_EMAILS:?set RECIPIENT_EMAILS}"
ALARM_EMAIL="${ALARM_EMAIL:-$SENDER_EMAIL}"
ENABLE_REDDIT="${ENABLE_REDDIT:-false}"
ENABLE_STOCKTWITS="${ENABLE_STOCKTWITS:-true}"
STOCKTWITS_SYMBOL_LIMIT="${STOCKTWITS_SYMBOL_LIMIT:-15}"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text --region "$REGION")
echo "Account=$ACCOUNT Region=$REGION Function=$FUNCTION"

echo "== DynamoDB table =="
aws dynamodb describe-table --table-name "$TABLE" --region "$REGION" >/dev/null 2>&1 || \
aws dynamodb create-table --table-name "$TABLE" --region "$REGION" \
  --attribute-definitions AttributeName=PK,AttributeType=S AttributeName=SK,AttributeType=S \
  --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST >/dev/null
aws dynamodb wait table-exists --table-name "$TABLE" --region "$REGION"

echo "== IAM role =="
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  sleep 10
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name stock-agent-data-access \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"dynamodb:GetItem\",\"dynamodb:PutItem\",\"dynamodb:Query\",\"dynamodb:BatchWriteItem\",\"dynamodb:UpdateItem\",\"dynamodb:DeleteItem\"],\"Resource\":\"arn:aws:dynamodb:${REGION}:${ACCOUNT}:table/${TABLE}\"},{\"Effect\":\"Allow\",\"Action\":[\"ses:SendEmail\",\"ses:SendRawEmail\"],\"Resource\":\"*\"}]}"
ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${ROLE}"

echo "== Build artifact =="
PYTHON="${PYTHON:-python3}" bash "$ROOT/deploy/build_lambda.sh"
ZIP="$ROOT/build/stock-agent-lambda.zip"

ENV_VARS="Variables={TABLE_NAME=$TABLE,SENDER_EMAIL=$SENDER_EMAIL,RECIPIENT_EMAILS=$RECIPIENT_EMAILS,ERROR_EMAIL=$ALARM_EMAIL,ENABLE_REDDIT=$ENABLE_REDDIT,ENABLE_STOCKTWITS=$ENABLE_STOCKTWITS,STOCKTWITS_SYMBOL_LIMIT=$STOCKTWITS_SYMBOL_LIMIT,LOG_LEVEL=INFO}"

echo "== Lambda function =="
if aws lambda get-function --function-name "$FUNCTION" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FUNCTION" \
    --zip-file "fileb://$ZIP" --region "$REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FUNCTION" \
    --environment "$ENV_VARS" --region "$REGION" >/dev/null
else
  aws lambda create-function --function-name "$FUNCTION" \
    --runtime python3.13 --handler stock_agent.lambda_handler.lambda_handler \
    --role "$ROLE_ARN" --timeout 300 --memory-size 512 \
    --zip-file "fileb://$ZIP" --environment "$ENV_VARS" --region "$REGION" >/dev/null
fi
aws lambda wait function-active --function-name "$FUNCTION" --region "$REGION"

echo "== SNS alarm topic + subscription =="
TOPIC_ARN=$(aws sns create-topic --name "$TOPIC" --region "$REGION" --query TopicArn --output text)
aws sns subscribe --topic-arn "$TOPIC_ARN" --protocol email \
  --notification-endpoint "$ALARM_EMAIL" --region "$REGION" >/dev/null

echo "== CloudWatch error alarm =="
aws cloudwatch put-metric-alarm --alarm-name stock-agent-errors --region "$REGION" \
  --alarm-description "Stock agent Lambda errored on its daily run." \
  --namespace AWS/Lambda --metric-name Errors --statistic Sum --period 86400 \
  --evaluation-periods 1 --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold --treat-missing-data notBreaching \
  --dimensions Name=FunctionName,Value="$FUNCTION" --alarm-actions "$TOPIC_ARN"

echo "== EventBridge daily schedule =="
aws events put-rule --name "$RULE" --schedule-expression "$SCHEDULE" \
  --state ENABLED --region "$REGION" \
  --description "Triggers the stock-agent Lambda once per day." >/dev/null
aws lambda add-permission --function-name "$FUNCTION" --region "$REGION" \
  --statement-id eventbridge-daily-invoke --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:${REGION}:${ACCOUNT}:rule/${RULE}" >/dev/null 2>&1 || true
aws events put-targets --rule "$RULE" --region "$REGION" \
  --targets "Id=stock-agent-lambda,Arn=arn:aws:lambda:${REGION}:${ACCOUNT}:function:${FUNCTION}" >/dev/null

echo
echo "Deployed. Confirm the SNS subscription email, then test:"
echo "  aws lambda invoke --function-name $FUNCTION --region $REGION /tmp/out.json && cat /tmp/out.json"
