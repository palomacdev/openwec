FROM python:3.12-slim

WORKDIR /app

COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

COPY api/ ./api/

EXPOSE 8000

# --no-proxy-headers: uvicorn rewrites scope["client"] from X-Forwarded-For
# whenever the peer is in its own trusted list, whose default is 127.0.0.1.
# That is a second, weaker trust policy running ahead of ours -- measured:
# with it on, six forged headers bought six separate rate-limit budgets.
# One policy, in api/deps.py, reading TRUSTED_PROXIES.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
