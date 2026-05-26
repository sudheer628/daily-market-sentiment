import json
import logging
import os
from typing import Any, Dict

import boto3

logger = logging.getLogger("daily_market_sentiment.storage")


class S3Storage:
    """S3 storage helper for Lambda output."""

    def __init__(self, bucket: str, prefix: str = ""):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = boto3.client("s3")

    def _build_key(self, prefix: str, filename: str) -> str:
        if self.prefix:
            return f"{self.prefix}/{prefix}/{filename}"
        return f"{prefix}/{filename}"

    def save_json(self, data: Dict[str, Any], prefix: str) -> str:
        filename = data.get("output_file", data.get("date", "output"))
        if not filename:
            filename = "output"
        if not filename.endswith(".json"):
            filename = f"{filename}.json"

        key = self._build_key(prefix, filename)
        body = json.dumps(data, indent=2, default=str)
        logger.info("Uploading result to s3://%s/%s", self.bucket, key)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body.encode("utf-8"), ContentType="application/json")
        return key
