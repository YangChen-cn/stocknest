"""Bounded market-data worker; strips email credentials from its environment."""
import json
import os
import sys

for key in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "REPORT_EMAIL"):
    os.environ.pop(key, None)

if __name__ == "__main__":
    from stockwatch.providers.openbb_provider import execute_request

    try:
        print(json.dumps(execute_request(json.loads(sys.stdin.read()))))
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__}))
