#!/usr/bin/env python3
"""
Kiwi Music API — Automated External Uptime Monitor & Keep-Alive Daemon
Fulfills Phase 12: Runs externally from the backend process to monitor availability
and prevent cold-start spin-down on platforms like Render.

Target: GET https://<BACKEND-DOMAIN>/v1/health
Interval: Configurable (default 300 seconds / 5 minutes)
"""

import os
import sys
import time
import logging
from datetime import datetime
import urllib.request
import urllib.error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("kiwi_uptime_monitor")

TARGET_URL = os.environ.get("KIWI_HEALTH_URL")
INTERVAL_SECONDS = int(os.environ.get("MONITOR_INTERVAL_SECONDS", "300"))
TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "10"))
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("MAX_CONSECUTIVE_FAILURES", "3"))

def check_health(target_url: str, timeout: int) -> tuple[bool, int, float, str]:
    start_time = time.perf_counter()
    req = urllib.request.Request(
        target_url,
        headers={"User-Agent": "KiwiMusicAPI-UptimeMonitor/1.0 (+https://github.com/)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency_ms = (time.perf_counter() - start_time) * 1000
            body = resp.read().decode("utf-8", errors="replace")
            return (resp.status == 200, resp.status, latency_ms, body)
    except urllib.error.HTTPError as e:
        latency_ms = (time.perf_counter() - start_time) * 1000
        return (False, e.code, latency_ms, str(e))
    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000
        return (False, 0, latency_ms, str(e))

def main():
    if not TARGET_URL:
        logger.error("Environment variable KIWI_HEALTH_URL is not set.")
        logger.info("Example usage: KIWI_HEALTH_URL=https://kiwimusicapi.onrender.com/v1/health python uptime_monitor.py")
        sys.exit(1)

    logger.info("Starting Kiwi Music API External Monitor")
    logger.info("  Target URL: %s", TARGET_URL)
    logger.info("  Check Interval: %d seconds (%.1f minutes)", INTERVAL_SECONDS, INTERVAL_SECONDS / 60)
    logger.info("  Timeout: %d seconds", TIMEOUT_SECONDS)

    consecutive_failures = 0

    while True:
        timestamp = datetime.utcnow().isoformat() + "Z"
        success, status_code, latency_ms, details = check_health(TARGET_URL, TIMEOUT_SECONDS)

        if success:
            consecutive_failures = 0
            logger.info(
                "[UPTIME OK] HTTP %d | Latency: %.2f ms | Target: %s",
                status_code, latency_ms, TARGET_URL
            )
        else:
            consecutive_failures += 1
            logger.warning(
                "[UPTIME ALERT #%d] Health check failed! HTTP %d | Latency: %.2f ms | Detail: %s",
                consecutive_failures, status_code, latency_ms, details[:120]
            )
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error(
                    "[CRITICAL DOWNTIME] Backend at %s has failed %d consecutive health checks!",
                    TARGET_URL, consecutive_failures
                )

        time.sleep(INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
