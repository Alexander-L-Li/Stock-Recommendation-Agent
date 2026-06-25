# SES Setup (Sandbox) — Email Yourself for Free

The agent emails its daily report via Amazon SES. A new SES account starts in
the **sandbox**, which is perfectly fine for a personal "email myself" agent:

- You can send **only to and from verified email addresses**.
- Daily cap is 200 messages / 1 msg-per-second — far more than one daily report.
- **You do NOT need to request production access.**

## 1. Verify your sender and recipient addresses

If you email yourself, the sender and recipient can be the same address. Verify
it once:

```bash
aws ses verify-email-identity --email-address you@example.com --region us-east-1
```

Check your inbox and click the verification link. Confirm status:

```bash
aws ses list-identities --region us-east-1
aws ses get-identity-verification-attributes \
    --identities you@example.com --region us-east-1
```

`VerificationStatus` should be `Success`. Repeat for any additional recipients.

> Pick a region where SES is available and use it consistently for the Lambda,
> the `AWS_REGION`/`aws_region` config, and these commands. `us-east-1` is a safe
> default.

## 2. Configure the agent

Set these environment variables (locally or as Lambda env vars):

```bash
export SENDER_EMAIL="you@example.com"
export RECIPIENT_EMAILS="you@example.com"   # comma-separated for multiple
export ERROR_EMAIL="you@example.com"        # optional; defaults to first recipient
export AWS_REGION="us-east-1"
```

## 3. Send a test email

```bash
.venv/bin/stock-agent send-test
```

You should receive a short test email at the verified address. If you get
`MessageRejected: Email address is not verified`, the sender or a recipient
hasn't completed verification (step 1).

## 4. (Optional) Leaving the sandbox

Only needed if you ever want to email **unverified** addresses (e.g. share with
friends). Request production access in the SES console → "Account dashboard" →
"Request production access". Not required for personal use.

## IAM permission required

The Lambda execution role (or your local credentials) needs:

```json
{
  "Effect": "Allow",
  "Action": ["ses:SendEmail", "ses:SendRawEmail"],
  "Resource": "*"
}
```
