# daily-market-sentiment

A lightweight AWS Lambda container for daily pre-market market sentiment and FII/DII flow extraction.

This repository contains the Lambda application, container build definition, and deployment guidance to replace the existing EC2-based `news-analyzer-for-market-sentiment` execution schedule.

## What it does

- `news` task: fetches pre-market news signals from Serper and formats them as a daily sentiment payload
- `fii_dii` task: fetches FII/DII cash and F&O flow data from Apify dataset endpoints and derives directional metrics
- outputs daily JSON files to S3
- deploys as an AWS Lambda container image to reduce EC2 cost and simplify weekday scheduling

## Repository layout

- `app/handler.py` - Lambda entrypoint
- `app/fetch_news.py` - market news fetcher and result formatter
- `app/fetch_fii_dii.py` - FII/DII flow fetcher and aggregator
- `app/storage.py` - S3 result persistence
- `Dockerfile` - AWS Lambda Python container image
- `requirements.txt` - runtime dependencies

## Build the Docker image

```bash
cd daily-market-sentiment
docker build -t daily-market-sentiment:latest .
```

## Local container test

Create a local `.env` from `.env.example`, then run the container with your real values and Redis configuration:

```bash
cp .env.example .env
# edit .env to set REDIS_HOST, REDIS_PORT, REDIS_USERNAME, REDIS_PASSWORD, APIFY_TOKEN, OUTPUT_BUCKET, etc.

docker run --rm --env-file .env -p 9000:8080 daily-market-sentiment:latest
```

Invoke the Lambda runtime API from another shell:

```bash
curl -s -XPOST "http://localhost:9000/2015-03-31/functions/function/invocations" \
  -H "Content-Type: application/json" \
  -d '{"task":"daily_batch"}'
```

If you want to test only print output without S3 persistence, omit `OUTPUT_BUCKET` and the function will still run and emit the result to Lambda logs.

## Environment variables

- `OUTPUT_BUCKET` - optional S3 bucket for JSON result storage; if omitted, results are printed only
- `OUTPUT_PREFIX` - optional S3 prefix (default: `daily-market-sentiment`)
- `APIFY_TOKEN` - required for FII/DII fetch when `task` is `fii_dii` or `fetch_fii_dii`
- `USE_MOCK` - optional, set `true` to run mock data instead of live APIs
- `LOG_LEVEL` - optional logging level
- `REDIS_HOST`, `REDIS_PORT`, `REDIS_USERNAME`, `REDIS_PASSWORD` - Redis Cloud credentials used to fetch `SERPER_API_KEY` from the Redis key `serper_api_key`
- `SERPER_API_KEY` - optional local fallback if Redis is unavailable; primary source is Redis Cloud
- `SERPER_BASE_URL` - optional override for Serper endpoint

## Deployment outline

1. Create an ECR repository
2. Build and tag the image
3. Push the image to ECR
4. Create a Lambda function with the ECR image
5. Set environment variables in Lambda
6. Create EventBridge rules for weekday schedules

## Recommended AWS schedule

Use EventBridge cron rules for IST weekdays:

- `fii_dii` trigger: 8:48 AM IST → `cron(18 3 ? * MON-FRI *)`
- `news` trigger: 8:55 AM IST → `cron(25 3 ? * MON-FRI *)`

If you prefer one Lambda invocation for both tasks:

- `daily_batch` trigger: 8:55 AM IST → `cron(25 3 ? * MON-FRI *)`

## Example Lambda invocation payloads

The Lambda supports both shorthand and explicit task names.

```json
{"task": "news"}
```

```json
{"task": "fetch_news"}
```

```json
{"task": "fii_dii"}
```

```json
{"task": "fetch_fii_dii"}
```

```json
{"task": "daily_batch"}
```

## Management of Process (MoP)

### Goal
Replace the `news-analyzer-for-market-sentiment` EC2 weekday workload with a Lambda container image for daily pre-market sentiment and institutional flow inference.

### Daily operations

- Run on weekdays only (Monday to Friday)
- Execute before market open:
  - `fii_dii` at 8:48 AM IST
  - `news` at 8:55 AM IST
- Optionally combine both into a single `daily_batch` invocation at 8:55 AM IST

### Inputs

- Live Serper API key for news extraction
- Apify token for FII/DII dataset access
- S3 bucket for output artifacts

### Outputs

- JSON result files written to S3 under `OUTPUT_PREFIX/news/` and `OUTPUT_PREFIX/fii_dii/`
- Each file includes summary metadata, generated timestamp, and the raw payload

### Health and monitoring

- Use CloudWatch Logs for `app.handler.lambda_handler`
- Track invocation failures and API errors
- Alert if either task fails or if no output file is written for a scheduled run

### Cost control

- Lambda is invoked only when needed; no persistent EC2 runtime cost
- Keep memory sizing moderate (e.g. 512MB or 1024MB) to balance execution speed and cost

### Troubleshooting

- If news fetch fails, verify `SERPER_API_KEY` and the Serper endpoint
- If FII/DII fetch fails, verify `APIFY_TOKEN` and dataset URL environment variables
- Use `USE_MOCK=true` for dry-run validation without live APIs

## Next step

Deploy the image to AWS Lambda and configure EventBridge schedules to fully replace the EC2-based pre-market workload.

## EC2 build and ECR publish (MoP)

This project is public on GitHub. The recommended first deployment workflow is:

1. Build and test the container image on an EC2 (Ubuntu) builder instance.
2. Push the image to Amazon ECR from the EC2 instance.
3. Create a Lambda function from the pushed ECR image and configure environment variables.

Commands to run on the EC2 builder (Ubuntu) after cloning the repo:

```bash
# Install Docker and AWS CLI if missing (Ubuntu)
sudo apt update
sudo apt install -y docker.io awscli
sudo systemctl enable --now docker

# Optional: add your user to docker group (log out/in required)
sudo usermod -aG docker $USER

# Build the image
cd ~/daily-market-sentiment
docker build -t daily-market-sentiment:latest .

# Authenticate Docker to ECR (replace <aws-region> and <account-id>)
aws ecr get-login-password --region <aws-region> | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<aws-region>.amazonaws.com

# Create ECR repo (one-time)
aws ecr create-repository --repository-name daily-market-sentiment --region <aws-region> || true

# Tag and push
docker tag daily-market-sentiment:latest <account-id>.dkr.ecr.<aws-region>.amazonaws.com/daily-market-sentiment:latest
docker push <account-id>.dkr.ecr.<aws-region>.amazonaws.com/daily-market-sentiment:latest
```

## Rebuild and redeploy after code changes

If you make changes to the code, rebuild and replace the image in ECR with these steps:

```bash
# Remove the old local image
docker image rm daily-market-sentiment:latest
# or force remove if needed
docker image rm -f daily-market-sentiment:latest

# Rebuild the container image
docker build -t daily-market-sentiment:latest .

# Re-tag and push to ECR
docker tag daily-market-sentiment:latest <account-id>.dkr.ecr.<aws-region>.amazonaws.com/daily-market-sentiment:latest
docker push <account-id>.dkr.ecr.<aws-region>.amazonaws.com/daily-market-sentiment:latest
```

If you want to keep the previous image tag, use a versioned tag instead of `latest`, for example `daily-market-sentiment:v1.1.0`.

Notes:
- Prefer assigning an IAM role to the EC2 instance with `ecr:CreateRepository`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`, `ecr:BatchCheckLayerAvailability`, and `ecr:PutImage`, or configure `aws configure` with credentials.
- Keep the EC2 builder ephemeral; remove images or terminate the instance after pushing to save cost.

## Credentials, tokens and .env handling

This repo uses several runtime environment variables and third-party tokens. Do NOT commit secrets to the repository. Use one of the following secure options instead:

- AWS Lambda environment variables (set in the Lambda console) for non-sensitive values and references
- A secure secret store or parameter store for API keys and tokens, then grant the Lambda role the minimum required permissions to read them
- GitHub Actions secrets or other CI secrets for automated builds (do not write secrets into images)

Primary variables used by the application:

- `SERPER_API_KEY` — local fallback Serper news API key; primary source for production is Redis Cloud key `serper_api_key`
- `SERPER_BASE_URL` — optional override for Serper endpoint
- `APIFY_TOKEN` — primary Apify token for FII/DII dataset access (used by `fii_dii` task)
- `APIFY_FII_DII_PRIMARY_DATASET_URL` — primary dataset URL (optional override)
- `APIFY_FII_DII_FALLBACK_TOKEN` and `APIFY_FII_DII_FALLBACK_DATASET_URL` — optional fallback
- `OUTPUT_BUCKET` — S3 bucket to write JSON outputs. If omitted, Lambda prints results only
- `OUTPUT_PREFIX` — optional S3 prefix
- `USE_MOCK` — `true`/`false` to run mock mode for testing
- `LOG_LEVEL` — `INFO`/`DEBUG` etc.

Handling best-practices:

- For Lambda: keep secrets and API keys out of images. Use environment variables or a secure secret store and attach an IAM role that permits only the required AWS actions.
- For Redis Cloud: set `REDIS_HOST`, `REDIS_PORT`, `REDIS_USERNAME`, `REDIS_PASSWORD` in your Lambda environment to allow the application to read `SERPER_API_KEY` from Redis.
- If you must test locally on EC2, create a `.env` file from `.env.example` and keep it out of git (`.gitignore` already configured). Example `.env` usage is provided for local testing only.
- For CI/CD: use GitHub Secrets or OIDC role assumption to avoid long-lived credentials.

Example: safely reading secrets in Lambda

1. Store `SERPER_API_KEY` in a secure secret or parameter store.
2. Grant the Lambda role permission to read only the specific secret.
3. In the Lambda environment, configure a variable that points to the secret location and load it at startup.

## Quick checklist before production cutover

- Build and push latest image to ECR from EC2.
- Create Lambda function using the ECR image and attach an IAM role with S3 write and secret read permissions.
- Populate Lambda environment variables or configure secure secret access as described.
- Create EventBridge rules for weekday triggers (see above cron expressions).
- Monitor the first few runs via CloudWatch Logs and ensure JSON artifacts appear in S3 (if configured).
