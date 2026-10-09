# Coin Pattern v86 — Web Deploy Package

## Streamlit Cloud entry point

Set **Main file path** to `streamlit_app.py` (or use `dashboard_final.py` directly). This package includes both entry points.

## What the web build does

- On the first visit to a fresh app instance, it starts one shared background job to collect Upbit KRW + Binance Spot USDT daily history and build the analysis outputs.
- The bootstrap lock, status and log are stored under the same `COIN_PATTERN_HOME` data directory as the SQLite databases, not inside the Git checkout. The lock is created atomically to prevent multiple visitors from starting duplicate jobs.
- Once output files exist, visitors read the prepared results; the live market snapshot is refreshed separately and does not require rebuilding 730-day history.
- The bootstrap can resume without re-downloading successfully stored market data only to the extent that the existing database rows are present. A failed or interrupted market fetch is logged and can be retried.

## Important hosting limitation

Streamlit Community Cloud's local filesystem should be treated as **ephemeral**, not as a permanent database. A restart, rebuild, or new instance can require historical data to be collected again. For durable production use, configure `COIN_PATTERN_HOME` to a genuinely persistent mounted volume where supported, or deploy the analysis worker/API and database on a persistent host and set `COIN_PATTERN_ANALYSIS_API` to its reachable HTTPS base URL. The existing analysis API module is a separate service; `127.0.0.1` on Streamlit Cloud points to the Streamlit container itself, not to another server.

## Required packages

See `requirements.txt`.

## Secrets

Do not commit API keys, passwords, tokens, local databases, or `.streamlit/secrets.toml` to GitHub. Use the hosting platform's secrets/environment settings.

## Suggested deploy steps

1. Upload/commit the contents of this ZIP to the `main` branch.
2. In Streamlit Cloud, select the repository and branch `main`.
3. Set **Main file path** to `streamlit_app.py`.
4. Deploy and keep the app page open during the first historical bootstrap. The first build can take a while because it requests daily candles for many markets.
5. For a production/public launch, set up persistent storage or a separate analysis API before advertising that historical results survive server restarts.
