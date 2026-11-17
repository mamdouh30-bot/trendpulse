#!/bin/sh
# TrendPulse scheduler: run one production cycle, sleep, repeat.
# Interval defaults to 12 hours (43200 s) — override with env var.
INTERVAL="${TRENDPULSE_INTERVAL_SECONDS:-43200}"
echo "[entrypoint] TrendPulse container started; interval=${INTERVAL}s"
while true; do
  echo "[entrypoint] $(date -u '+%Y-%m-%d %H:%M UTC') — starting production cycle"
  python3 run_cycle.py || true
  echo "[entrypoint] cycle finished; sleeping ${INTERVAL}s"
  sleep "$INTERVAL"
done
