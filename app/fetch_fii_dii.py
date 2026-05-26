import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("daily_market_sentiment.fetch_fii_dii")


class FIIDIIFetcher:
    def __init__(self):
        self.minimum_cash_rows = 5

    def _parse_val(self, val_str: str) -> float:
        text = str(val_str or "").split('<br>')[0].replace(' Cr', '').replace(',', '').strip()
        text = text.replace('\\-', '-')
        if text.endswith('L'):
            try:
                return float(text[:-1]) * 100000
            except ValueError:
                return 0.0
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0

    def _parse_date(self, date_str: str) -> str:
        cleaned = " ".join(str(date_str or "").split())
        for fmt in ("%d %b %Y", "%d %b"):
            try:
                if fmt == "%d %b":
                    parsed = datetime.strptime(f"{cleaned} {datetime.now().year}", "%d %b %Y")
                else:
                    parsed = datetime.strptime(cleaned, fmt)
                return parsed.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return cleaned

    def _parse_markdown_data(self, markdown_text: str) -> Dict[str, List[Dict[str, Any]]]:
        rows_cash = []
        rows_fno = []
        lines = str(markdown_text or "").split('\n')
        data_lines = [line for line in lines if line.startswith('|') and '---' not in line]

        for line in data_lines:
            cells = [cell.strip() for cell in line.split('|')[1:-1]]
            if len(cells) < 10:
                continue
            date_str = cells[0]
            if date_str in ('Date', 'FII Call OI Chg', 'Buy/Sell(Amt)'):
                continue
            if not any(month in date_str for month in ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')):
                continue

            rows_cash.append({
                'date': self._parse_date(date_str),
                'FII Net Purchase / Sales': self._parse_val(cells[8]),
                'DII Net Purchase / Sales': self._parse_val(cells[9]),
            })
            rows_fno.append({
                'date': self._parse_date(date_str),
                'FII FUTURES Net Purchase / Sales': self._parse_val(cells[4]),
                'FII OPTIONS Net Purchase / Sales': self._parse_val(cells[2]) - self._parse_val(cells[3]),
            })

        return {'FII_DII_Cash': rows_cash, 'FII_FNO_Index': rows_fno}

    def _build_sources(self) -> List[Dict[str, Optional[str]]]:
        primary_token = os.getenv('APIFY_TOKEN') or os.getenv('APIFY_FII_DII_PRIMARY_TOKEN')
        primary_endpoint = os.getenv(
            'APIFY_FII_DII_PRIMARY_DATASET_URL',
            'https://api.apify.com/v2/acts/apify~website-content-crawler/runs/last/dataset/items'
        )
        fallback_token = os.getenv('APIFY_FII_DII_FALLBACK_TOKEN')
        fallback_endpoint = os.getenv('APIFY_FII_DII_FALLBACK_DATASET_URL')

        sources = [
            {
                'name': 'primary',
                'label': 'Apify Sensibull crawler',
                'endpoint': primary_endpoint,
                'token': primary_token,
                'parser': 'sensibull_markdown',
            }
        ]
        if fallback_endpoint and fallback_token:
            sources.append(
                {
                    'name': 'fallback',
                    'label': 'Apify Groww crawler',
                    'endpoint': fallback_endpoint,
                    'token': fallback_token,
                    'parser': 'groww_text',
                }
            )
        return sources

    def _parse_source_payload(self, source: Dict[str, Optional[str]], payload: Any) -> Dict[str, List[Dict[str, Any]]]:
        if not payload or not isinstance(payload, list) or not isinstance(payload[0], dict):
            raise ValueError('response is empty or invalid')
        first_item = payload[0]
        parser = source.get('parser')
        if parser == 'sensibull_markdown':
            markdown = first_item.get('markdown', '')
            return self._parse_markdown_data(markdown)
        raise ValueError(f'unsupported parser: {parser}')

    def _has_required_cash_data(self, parsed: Dict[str, Any]) -> bool:
        return len(parsed.get('FII_DII_Cash', [])) >= self.minimum_cash_rows

    def fetch_fii_dii_data(self) -> Dict[str, Any]:
        sources = self._build_sources()
        if not sources or not sources[0].get('token'):
            logger.error('Missing APIFY token for FII/DII fetcher')
            return {'FII_DII_Cash': [], 'FII_FNO_Index': [], '_fetch_debug': {'failure_reason': 'missing_token'}}

        attempts = []
        for source in sources:
            endpoint = source.get('endpoint')
            token = source.get('token')
            if not endpoint or not token:
                attempts.append({'source': source.get('name'), 'error': 'missing endpoint or token'})
                continue

            try:
                response = requests.get(endpoint, params={'token': token}, timeout=30)
                response.raise_for_status()
                payload = response.json()
                parsed = self._parse_source_payload(source, payload)
                if self._has_required_cash_data(parsed):
                    parsed['_fetch_meta'] = {
                        'source_name': source.get('name'),
                        'source_label': source.get('label'),
                        'endpoint': endpoint,
                        'cash_rows': len(parsed.get('FII_DII_Cash', [])),
                        'fno_rows': len(parsed.get('FII_FNO_Index', [])),
                    }
                    return parsed
                attempts.append({'source': source.get('name'), 'error': 'insufficient_cash_rows'})
            except Exception as exc:
                attempts.append({'source': source.get('name'), 'error': str(exc)})

        return {'FII_DII_Cash': [], 'FII_FNO_Index': [], '_fetch_debug': {'failure_reason': 'all_sources_failed', 'attempts': attempts}}

    def calculate_5day_metrics(self, cash_data: List[Dict[str, Any]], fno_data: List[Dict[str, Any]]) -> Dict[str, float]:
        cash_recent = cash_data[:5]
        fno_recent = fno_data[:5]
        return {
            'fii_cash_5d_sum': round(sum(row.get('FII Net Purchase / Sales', 0) for row in cash_recent), 2),
            'dii_cash_5d_sum': round(sum(row.get('DII Net Purchase / Sales', 0) for row in cash_recent), 2),
            'fii_futures_5d_sum': round(sum(row.get('FII FUTURES Net Purchase / Sales', 0) for row in fno_recent), 2),
            'fii_options_5d_sum': round(sum(row.get('FII OPTIONS Net Purchase / Sales', 0) for row in fno_recent), 2),
        }

    def classify_cash_direction(self, fii_cash_sum: float, dii_cash_sum: float, threshold: float = 3000.0) -> str:
        if fii_cash_sum <= -threshold and dii_cash_sum >= threshold:
            return 'FII_SELLING_DII_BUYING'
        if fii_cash_sum >= threshold and dii_cash_sum <= -threshold:
            return 'FII_BUYING_DII_SELLING'
        if fii_cash_sum >= threshold and dii_cash_sum >= threshold:
            return 'BOTH_BUYING'
        if fii_cash_sum <= -threshold and dii_cash_sum <= -threshold:
            return 'BOTH_SELLING'
        return 'MIXED'

    def classify_fno_bias(self, fii_fut_sum: float, fii_opt_sum: float) -> str:
        if fii_fut_sum > 0 and fii_opt_sum > 0:
            return 'BULLISH_POSITIONING'
        if fii_fut_sum < 0 and fii_opt_sum < 0:
            return 'BEARISH_POSITIONING'
        return 'HEDGED_OR_NEUTRAL'

    def calculate_certainty(self, fii_cash_sum: float, max_threshold: float = 10000.0) -> float:
        return round(min(abs(fii_cash_sum) / max_threshold, 1.0), 2)

    def determine_market_impact(self, cash_direction: str, fno_bias: str) -> str:
        if cash_direction == 'FII_SELLING_DII_BUYING':
            if fno_bias == 'HEDGED_OR_NEUTRAL':
                return 'SUPPORTIVE_BUT_VOLATILE'
            if fno_bias == 'BEARISH_POSITIONING':
                return 'BEARISH_PRESSURE'
            return 'MIXED_SIGNALS'
        if cash_direction == 'FII_BUYING_DII_SELLING':
            return 'BULLISH_INSTITUTIONAL'
        if cash_direction == 'BOTH_BUYING':
            return 'STRONG_BULLISH'
        if cash_direction == 'BOTH_SELLING':
            return 'STRONG_BEARISH'
        return 'NEUTRAL_OR_MIXED'

    def create_storage_item(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        cash_data = raw_data.get('FII_DII_Cash', [])
        fno_data = raw_data.get('FII_FNO_Index', [])
        if not cash_data:
            raise ValueError('No cash data available')

        metrics = self.calculate_5day_metrics(cash_data, fno_data)
        cash_direction = self.classify_cash_direction(metrics['fii_cash_5d_sum'], metrics['dii_cash_5d_sum'])
        fno_bias = self.classify_fno_bias(metrics['fii_futures_5d_sum'], metrics['fii_options_5d_sum'])
        certainty = self.calculate_certainty(metrics['fii_cash_5d_sum'])
        impact = self.determine_market_impact(cash_direction, fno_bias)

        today = datetime.now(timezone.utc)
        today_date = today.strftime('%Y-%m-%d')
        start_date = cash_data[min(len(cash_data), 5) - 1]['date'] if cash_data else today_date

        item = {
            'pk': 'FLOW#FII_DII#DAILY',
            'ts': int(today.timestamp()),
            'date': today_date,
            'output_file': f'fii_dii-{today.strftime("%Y-%m-%dT%H%M%SZ")}',
            'data_window': {
                'start_date': start_date,
                'end_date': cash_data[0]['date'] if cash_data else today_date,
                'days_included': min(len(cash_data), 5),
            },
            'cash_flow': {
                'fii_net_5d_sum': metrics['fii_cash_5d_sum'],
                'dii_net_5d_sum': metrics['dii_cash_5d_sum'],
                'direction': cash_direction,
            },
            'fno_positioning': {
                'fii_futures_5d_sum': metrics['fii_futures_5d_sum'],
                'fii_options_5d_sum': metrics['fii_options_5d_sum'],
                'bias': fno_bias,
            },
            'market_impact_bias': impact,
            'certainty': certainty,
            'data_quality': 'ok',
            'generated_by': 'daily-market-sentiment-lambda',
            'created_at': today.isoformat().replace('+00:00', 'Z'),
        }

        if raw_data.get('_fetch_meta'):
            item['fetch_source'] = raw_data['_fetch_meta']

        return item


class MockFIIDIIFetcher(FIIDIIFetcher):
    def fetch_fii_dii_data(self) -> Dict[str, Any]:
        logger.info('Using mock FII/DII data')
        mock_cash = [
            {'date': '2026-01-23', 'FII Net Purchase / Sales': -4113.4, 'DII Net Purchase / Sales': 4102.6},
            {'date': '2026-01-22', 'FII Net Purchase / Sales': -2549.8, 'DII Net Purchase / Sales': 4223.0},
            {'date': '2026-01-21', 'FII Net Purchase / Sales': -1787.7, 'DII Net Purchase / Sales': 4520.5},
            {'date': '2026-01-20', 'FII Net Purchase / Sales': -2938.3, 'DII Net Purchase / Sales': 3665.7},
            {'date': '2026-01-19', 'FII Net Purchase / Sales': -3262.8, 'DII Net Purchase / Sales': 4234.3},
        ]
        mock_fno = [
            {'date': '2026-01-23', 'FII FUTURES Net Purchase / Sales': -1929.2, 'FII OPTIONS Net Purchase / Sales': 21659.7},
            {'date': '2026-01-22', 'FII FUTURES Net Purchase / Sales': -1957.5, 'FII OPTIONS Net Purchase / Sales': -983.0},
            {'date': '2026-01-21', 'FII FUTURES Net Purchase / Sales': -569.1, 'FII OPTIONS Net Purchase / Sales': -9905.0},
            {'date': '2026-01-20', 'FII FUTURES Net Purchase / Sales': -1459.6, 'FII OPTIONS Net Purchase / Sales': -14105.2},
            {'date': '2026-01-19', 'FII FUTURES Net Purchase / Sales': -479.3, 'FII OPTIONS Net Purchase / Sales': -7706.5},
        ]
        return {'FII_DII_Cash': mock_cash, 'FII_FNO_Index': mock_fno}


class FiiDiiJob:
    def __init__(self, use_mock: bool = False):
        self.use_mock = use_mock
        self.fetcher = MockFIIDIIFetcher() if use_mock else FIIDIIFetcher()

    def run(self) -> Dict[str, Any]:
        raw_data = self.fetcher.fetch_fii_dii_data()
        if not raw_data.get('FII_DII_Cash'):
            raise RuntimeError('Failed to fetch FII/DII data')

        output = self.fetcher.create_storage_item(raw_data)
        print("=== FII/DII JOB SUMMARY ===")
        print(f"Source: {'mock' if self.use_mock else 'apify'}")
        print(f"Date: {output['date']}")
        print(f"Data window: {output['data_window']['start_date']} to {output['data_window']['end_date']}")
        print(f"Cash direction: {output['cash_flow']['direction']}")
        print(f"FII net 5d sum: {output['cash_flow']['fii_net_5d_sum']}")
        print(f"DII net 5d sum: {output['cash_flow']['dii_net_5d_sum']}")
        print(f"F&O bias: {output['fno_positioning']['bias']}")
        print(f"Market impact bias: {output['market_impact_bias']}")
        print(f"Certainty: {output['certainty']}")
        print(f"Result payload file name: {output['output_file']}.json")
        return output
