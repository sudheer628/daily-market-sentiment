import json
import logging
import os
from datetime import datetime, timezone

from .fetch_fii_dii import FiiDiiJob
from .fetch_news import MarketNewsJob
from .storage import S3Storage
from .emailer import send_email

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("daily_market_sentiment.handler")


def _current_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def lambda_handler(event, context):
    """AWS Lambda entrypoint."""
    logger.info("Lambda invoked: %s", json.dumps(event))
    print("Lambda invocation event:", json.dumps(event))

    task = str(event.get("task", "daily_batch")).strip().lower()
    if task == "fetch_news":
        task = "news"
    elif task == "fetch_fii_dii":
        task = "fii_dii"

    use_mock = bool(event.get("mock", os.getenv("USE_MOCK", "false").lower() in ("1", "true", "yes")))
    output_bucket = os.getenv("OUTPUT_BUCKET")
    output_prefix = os.getenv("OUTPUT_PREFIX", "daily-market-sentiment")

    if output_bucket:
        storage = S3Storage(bucket=output_bucket, prefix=output_prefix)
    else:
        storage = None
        logger.warning("OUTPUT_BUCKET is not configured; skipping S3 upload.")
        print("OUTPUT_BUCKET is not configured; skipping S3 upload.")

    results = {
        "timestamp": _current_utc_iso(),
        "task": task,
        "status": "running",
        "outputs": []
    }

    try:
        if task in ("daily_batch", "both", "all"):
            jobs = ["news", "fii_dii"]
        else:
            jobs = [task]

        for job_name in jobs:
            if job_name == "news":
                logger.info("Running market news job")
                print("=== START NEWS JOB ===")
                job = MarketNewsJob(use_mock=use_mock)
                result = job.run()
                print("=== NEWS JOB OUTPUT ===")
                print(json.dumps(result, indent=2, default=str))
                if storage is not None:
                    key = storage.save_json(result, prefix="news")
                    results["outputs"].append({"job": "news", "s3_key": key})
                    print(f"Saved news output to s3://{output_bucket}/{key}")
                else:
                    results["outputs"].append({"job": "news", "status": "printed_only"})
            elif job_name == "fii_dii":
                logger.info("Running FII/DII job")
                print("=== START FII/DII JOB ===")
                job = FiiDiiJob(use_mock=use_mock)
                result = job.run()
                print("=== FII/DII JOB OUTPUT ===")
                print(json.dumps(result, indent=2, default=str))
                if storage is not None:
                    key = storage.save_json(result, prefix="fii_dii")
                    results["outputs"].append({"job": "fii_dii", "s3_key": key})
                    print(f"Saved FII/DII output to s3://{output_bucket}/{key}")
                else:
                    results["outputs"].append({"job": "fii_dii", "status": "printed_only"})
            else:
                logger.warning("Unknown task: %s", job_name)
                print(f"Unknown task: {job_name}. Supported tasks: news, fetch_news, fii_dii, fetch_fii_dii, daily_batch.")
                results["outputs"].append({"job": job_name, "status": "skipped"})

        results["status"] = "success"
        logger.info("Lambda completed successfully")
        print("Lambda execution finished successfully")

        # Send an email summary if email env vars are configured
        try:
            subject = f"daily-market-sentiment: {task} completed {results['status']}"
            body_lines = [f"Task: {task}", f"Status: {results['status']}", f"Timestamp: {results['timestamp']}", "Outputs:"]
            for out in results.get("outputs", []):
                body_lines.append(str(out))
            body = "\n".join(body_lines)
            sent = send_email(subject, body)
            if sent:
                print("Notification email sent")
            else:
                print("Notification email not sent (missing config or failed)")
        except Exception:
            logger.exception("Failed to send notification email")
    except Exception as exc:
        logger.exception("Lambda execution failed")
        results["status"] = "failure"
        results["error"] = str(exc)
        raise

    return results
