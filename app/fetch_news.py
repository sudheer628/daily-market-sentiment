import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests
import redis
from dateutil.parser import parse as parse_date

logger = logging.getLogger("daily_market_sentiment.fetch_news")


class SerperNewsFetcher:
    BASE_QUERIES = {
        "fii_dii": [
            "FII FPI selling buying India today",
            "Foreign institutional investors India equity",
            "DII domestic investors India market",
        ],
        "rupee": [
            "Indian rupee dollar today",
            "USDINR exchange rate today",
        ],
        "earnings": [
            "Q3 earnings India market impact",
            "corporate results India sentiment",
        ],
        "global": [
            "global markets India impact",
            "US Fed policy India",
            "risk sentiment emerging markets",
        ],
        # High-signal pre-market queries (overnight / pre-open signals)
        "market_sentiment": [
            "US market close impact on Indian market today",
            "GIFT Nifty pre-market indication India today",
            "crude oil and USDINR impact on Indian markets today",
        ],
    }

    def __init__(self, api_key: Optional[str] = None, max_age_hours: int = 12):
        # Attempt to read Serper API key from Redis first (key: 'serper_api_key'),
        # then fall back to environment variable `SERPER_API_KEY`.
        self.api_key = api_key or os.getenv("SERPER_API_KEY", "")

        def _get_redis_client():
            # Mirror market_signal_agent.get_redis_client behavior using REDIS_HOST/REDIS_PORT.
            # Redis Cloud setup generally provides host, port, username, and password.
            try:
                host = os.getenv("REDIS_HOST")
                port = os.getenv("REDIS_PORT")
                if not host or not port:
                    return None

                return redis.Redis(
                    host=host,
                    port=int(port),
                    username=os.getenv("REDIS_USERNAME") or None,
                    password=os.getenv("REDIS_PASSWORD") or None,
                    decode_responses=True,
                    socket_timeout=10,
                )
            except Exception:
                logger.exception("Failed to create Redis client")
                return None

        try:
            if not self.api_key:
                rc = _get_redis_client()
                if rc:
                    val = rc.get("serper_api_key")
                    if val:
                        self.api_key = val
                        logger.info("Loaded SERPER API key from Redis")
        except Exception:
            logger.exception("Failed to read SERPER API key from Redis; falling back to environment variable.")
        self.base_url = os.getenv("SERPER_BASE_URL", "https://google.serper.dev/news")
        self.max_age_hours = max_age_hours
        self.today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if not self.api_key:
            logger.warning("SERPER_API_KEY is not configured. News fetcher may fail.")

    def fetch_news(self, query: str, num_results: int = 8, time_range: str = "d") -> List[Dict[str, Any]]:
        if not self.api_key:
            return []

        payload = {
            "q": f"{query} today {self.today}",
            "num": num_results,
            "tbs": f"qdr:{time_range}",
            "gl": "in",
            "hl": "en",
        }
        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(self.base_url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            articles = data.get("news", [])
            normalized = [self._normalize_article(article, query) for article in articles if self._is_recent(article)]
            logger.info("Fetched %d articles for query '%s'", len(normalized), query)
            return normalized
        except requests.RequestException as exc:
            logger.error("Serper news fetch failed for query '%s': %s", query, exc)
            return []

    def _normalize_article(self, article: Dict[str, Any], source_query: str) -> Dict[str, Any]:
        url = article.get("link", "") or article.get("url", "")
        canonical_url = self._canonicalize_url(url)
        published_epoch = self._parse_published_epoch(article.get("date", ""))
        published_at = (
            datetime.fromtimestamp(published_epoch, tz=timezone.utc).isoformat()
            if published_epoch is not None
            else ""
        )

        return {
            "source_query": source_query,
            "title": article.get("title", ""),
            "snippet": article.get("snippet", ""),
            "url": url,
            "canonical_url": canonical_url,
            "url_hash": self._hash_value(canonical_url),
            "source": article.get("source", ""),
            "published_hint": article.get("date", ""),
            "published_at": published_at,
            "published_epoch": published_epoch,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    def _is_recent(self, article: Dict[str, Any]) -> bool:
        published = article.get("date", "")
        epoch = self._parse_published_epoch(published)
        if epoch is None:
            return True
        return (datetime.now(timezone.utc) - datetime.fromtimestamp(epoch, tz=timezone.utc)) <= timedelta(hours=self.max_age_hours)

    def _parse_published_epoch(self, published_hint: str) -> Optional[int]:
        if not published_hint:
            return None
        text = published_hint.strip().lower()
        now = datetime.now(timezone.utc)
        relative_match = re.search(r"(\d+)\s*(minute|min|hr|hour|day|week)s?\s*ago", text)
        if relative_match:
            qty = int(relative_match.group(1))
            unit = relative_match.group(2)
            if unit.startswith("min"):
                delta = timedelta(minutes=qty)
            elif unit.startswith("hr") or unit.startswith("hour"):
                delta = timedelta(hours=qty)
            elif unit.startswith("day"):
                delta = timedelta(days=qty)
            else:
                delta = timedelta(weeks=qty)
            return int((now - delta).timestamp())

        try:
            parsed = parse_date(published_hint)
            if parsed is None:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
            return int(parsed.timestamp())
        except Exception:
            return None

    def _canonicalize_url(self, url: str) -> str:
        if not url:
            return ""
        try:
            cleaned = re.sub(r"[?&](utm_[^=]+|gclid|fbclid)=[^&]*", "", url)
            return cleaned.strip().lower()
        except Exception:
            return url.strip().lower()

    def _hash_value(self, value: str) -> str:
        return hashlib.md5(value.encode("utf-8")).hexdigest()

    def fetch_market_drivers(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        # Iterate through curated queries; include a small number of
        # high-signal additional queries to capture market sentiment.
        for category, queries in self.BASE_QUERIES.items():
            for query in queries:
                results.extend(self.fetch_news(query, num_results=6))
        return results
        return results


class MockSerperFetcher(SerperNewsFetcher):
    MOCK_ARTICLES = [
        {
            "title": "FII selling pressure continues on weakness in global cues",
            "snippet": "Foreign institutional investors remained net sellers, and the rupee weakness adds downside risk.",
            "link": "https://example.com/mock-fii-selling",
            "source": "Mock News",
            "date": "2 hours ago",
        },
        {
            "title": "Rupee trades lower ahead of RBI policy announcement",
            "snippet": "Currency and macro news dominate the pre-market outlook for Indian equities.",
            "link": "https://example.com/mock-rupee",
            "source": "Mock News",
            "date": "3 hours ago",
        },
    ]

    def fetch_market_drivers(self) -> List[Dict[str, Any]]:
        items = []
        for article in self.MOCK_ARTICLES:
            items.append(self._normalize_article(article, "mock"))
        return items


class MarketNewsJob:
    def __init__(self, use_mock: bool = False, max_age_hours: int = 12):
        self.use_mock = use_mock
        self.fetcher = MockSerperFetcher(max_age_hours=max_age_hours) if use_mock else SerperNewsFetcher(max_age_hours=max_age_hours)

    def run(self) -> Dict[str, Any]:
        articles = self.fetcher.fetch_market_drivers()
        unique_urls = set(article["canonical_url"] for article in articles if article.get("canonical_url"))
        output = {
            "pk": "NEWS#MARKET#DAILY",
            "ts": int(datetime.now(timezone.utc).timestamp()),
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "output_file": f"news-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')}",
            "source": "serper" if not self.use_mock else "mock",
            "news_count": len(articles),
            "unique_urls": len(unique_urls),
            "headline_tags": list({article.get("source_query", "unknown") for article in articles}),
            "raw_articles": articles,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        print("=== MARKET NEWS JOB SUMMARY ===")
        print(f"Source: {output['source']}")
        print(f"News count: {output['news_count']}")
        print(f"Unique URLs: {output['unique_urls']}")
        print(f"Headline tags: {output['headline_tags']}")
        print("Top 3 articles:")
        for article in articles[:3]:
            print(f"  - {article.get('title', 'N/A')} | source={article.get('source', 'unknown')} | published={article.get('published_hint', '')}")
        print(f"Result payload file name: {output['output_file']}.json")
        return output
