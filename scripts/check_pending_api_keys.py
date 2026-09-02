#!/usr/bin/env python3

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path("/opt/openwec")
ENV_FILE = PROJECT_ROOT / ".env.notifications"

COMMAND = [
    "python3",
    "-m",
    "database.admin.manage_api_keys",
    "--list-pending",
]


def load_env():
    if not ENV_FILE.exists():
        raise RuntimeError(f"Environment file not found: {ENV_FILE}")

    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def get_pending_keys():
    env = os.environ.copy()

    env["DB_HOST"] = "127.0.0.1"
    env["DB_PORT"] = "5433"

    result = subprocess.run(
        COMMAND,
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "manage_api_keys failed:\n\n"
            f"STDOUT:\n{result.stdout}\n\n"
            f"STDERR:\n{result.stderr}"
        )

    output = result.stdout.strip()

    pending_count = len(
        re.findall(
            r"^\[\s*\d+\]",
            output,
            flags=re.MULTILINE,
        )
    )

    return pending_count, output


def send_email(pending_count, output):
    api_key = os.environ["RESEND_API_KEY"]
    to_email = os.environ["ALERT_EMAIL"]

    from_email = os.environ.get(
        "ALERT_FROM",
        "OpenWEC <onboarding@resend.dev>",
    )

    now = datetime.now(timezone.utc)

    if pending_count == 0:
        subject = "OpenWEC — No pending API keys"

        body = f"""OpenWEC API Key Report

No API key requests are currently waiting for approval.

Checked at:
{now:%Y-%m-%d %H:%M:%S UTC}

—
OpenWEC automated administration
"""

    else:
        subject = (
            f"OpenWEC — {pending_count} pending API key"
            f"{'s' if pending_count != 1 else ''}"
        )

        body = f"""OpenWEC API Key Report

There are {pending_count} API key request(s) waiting for review.

------------------------------------------------------------
{output}
------------------------------------------------------------

To review them manually:

cd /opt/openwec
DB_HOST=127.0.0.1 DB_PORT=5433 \\
python3 -m database.admin.manage_api_keys --list-pending

Checked at:
{now:%Y-%m-%d %H:%M:%S UTC}

—
OpenWEC automated administration
"""

    payload = json.dumps(
        {
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "text": body,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
    "https://api.resend.com/emails",
    data=payload,
    method="POST",
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "OpenWEC/1.0 (+https://openwec.com)",
    },
)

    try:
        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:
            response_body = response.read().decode("utf-8")

            if response.status not in (200, 201):
                raise RuntimeError(
                    f"Resend returned HTTP {response.status}: "
                    f"{response_body}"
                )

    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"Resend returned HTTP {exc.code}: {error_body}"
        ) from exc


def main():
    load_env()

    pending_count, output = get_pending_keys()

    send_email(pending_count, output)

    print(
        "OK - API key report sent "
        f"({pending_count} pending request(s))"
    )


if __name__ == "__main__":
    main()