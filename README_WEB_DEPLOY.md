# Coin Pattern v86 — Web Deploy Package

This package preserves the v86 application entry point (`dashboard_final.py`) and its analysis/runtime modules for web deployment.

## Streamlit entry point

`dashboard_final.py`

## Required packages

See `requirements.txt`.

## Important

The v86 application was originally designed with a local-development fallback and separate analysis/auth services. A first Streamlit deployment can launch the UI, but production use of member authentication, prepared analysis API, persistent database, and payments requires those services to be hosted and their URLs/secrets configured for the deployment environment.

Do **not** commit API keys, passwords, tokens, local databases, or `.streamlit/secrets.toml` to GitHub.
