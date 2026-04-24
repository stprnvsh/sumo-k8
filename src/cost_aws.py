"""Resolve per-job cost rates: manual env, or EC2 on-demand list price via AWS Price List API."""
from __future__ import annotations

import json
import logging
import re
from typing import Optional, Tuple

from .config import (
    AWS_REGION,
    JOB_COST_AWS_INSTANCE_TYPE,
    JOB_COST_CPU_USD_PER_HOUR,
    JOB_COST_MEMORY_GIB_USD_PER_HOUR,
)

logger = logging.getLogger(__name__)

# AWS Price List API (GetProducts) uses display names, not region codes.
_REGION_TO_LOCATION = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "eu-central-1": "EU (Frankfurt)",
    "eu-central-2": "Europe (Zurich)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-west-3": "EU (Paris)",
    "eu-north-1": "EU (Stockholm)",
    "eu-south-1": "EU (Milan)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-northeast-3": "Asia Pacific (Osaka)",
    "ca-central-1": "Canada (Central)",
    "sa-east-1": "South America (Sao Paulo)",
    "me-south-1": "Middle East (Bahrain)",
    "af-south-1": "Africa (Cape Town)",
}

_aws_rates_cache: Optional[Tuple[float, float, str]] = None


def _parse_memory_gib(s: str) -> float:
    m = re.search(r"([0-9.]+)\s*GiB", s or "", re.I)
    if m:
        return float(m.group(1))
    return 0.0


def _hourly_on_demand_usd(price_list_json: str) -> Optional[float]:
    try:
        d = json.loads(price_list_json)
    except json.JSONDecodeError:
        return None
    for term in d.get("terms", {}).get("OnDemand", {}).values():
        for pd in term.get("priceDimensions", {}).values():
            if pd.get("unit") != "Hrs":
                continue
            usd = (pd.get("pricePerUnit") or {}).get("USD")
            if usd is not None:
                return float(usd)
    return None


def _fetch_ec2_od_rates_usd(instance_type: str, region_code: str) -> Optional[Tuple[float, float]]:
    """Returns (usd_per_cpu_hour, usd_per_gib_hour) from Linux shared On-Demand hourly / 2 split."""
    location = _REGION_TO_LOCATION.get(region_code)
    if not location:
        logger.warning("JOB_COST AWS pricing: unknown region code %s (add mapping in cost_aws.py)", region_code)
        return None
    try:
        import boto3
    except ImportError:
        logger.warning("boto3 required for AWS job cost rates")
        return None

    client = boto3.client("pricing", region_name="us-east-1")
    flt = [
        {"Type": "TERM_MATCH", "Field": "ServiceCode", "Value": "AmazonEC2"},
        {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
        {"Type": "TERM_MATCH", "Field": "location", "Value": location},
        {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
        {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
        {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
        {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
    ]
    resp = client.get_products(ServiceCode="AmazonEC2", Filters=flt, MaxResults=10)
    plist = resp.get("PriceList") or []
    for raw in plist:
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        attrs = d.get("product", {}).get("attributes", {})
        hourly = _hourly_on_demand_usd(raw)
        if hourly is None:
            continue
        try:
            vcpu = float(attrs.get("vcpu", 0))
        except (TypeError, ValueError):
            vcpu = 0.0
        mem_gib = _parse_memory_gib(attrs.get("memory", ""))
        if vcpu <= 0 or mem_gib <= 0:
            continue
        # Split instance hourly 50/50 between CPU and memory buckets (same model as manual rates).
        cpu_rate = (hourly * 0.5) / vcpu
        mem_rate = (hourly * 0.5) / mem_gib
        return (cpu_rate, mem_rate)
    logger.warning(
        "AWS Price List: no On-Demand Linux price for %s in %s (%s)",
        instance_type,
        region_code,
        location,
    )
    return None


def get_job_cost_rates() -> Optional[Tuple[float, float]]:
    """(usd/vCPU/h, usd/GiB/h). Env wins if either rate > 0; else optional AWS list price."""
    ec = max(JOB_COST_CPU_USD_PER_HOUR, 0.0)
    em = max(JOB_COST_MEMORY_GIB_USD_PER_HOUR, 0.0)
    if ec > 0 or em > 0:
        return (ec, em)

    it = JOB_COST_AWS_INSTANCE_TYPE
    if not it:
        return None

    global _aws_rates_cache
    region = AWS_REGION or "us-east-1"
    cache_key = f"{region}:{it}"
    if _aws_rates_cache and _aws_rates_cache[2] == cache_key:
        return (_aws_rates_cache[0], _aws_rates_cache[1])

    rates = _fetch_ec2_od_rates_usd(it, region)
    if not rates:
        return None
    _aws_rates_cache = (rates[0], rates[1], cache_key)
    return rates
