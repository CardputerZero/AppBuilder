"""GitHub device-flow authentication — mirrors the Rust auth module."""

import json
import os
import stat
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional

from .github_client import GitHubClient

GITHUB_CLIENT_ID = "Ov23li06cv5RkdEJXrhL"
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"

# These endpoints live on github.com (not api.github.com), which answers with an
# HTML page — not JSON — when it rate-limits a client or when a proxy/captive
# portal intercepts the request. A default `Python-urllib/x.y` User-Agent makes
# that more likely, so identify ourselves like github_client.py does.
USER_AGENT = "czdev/0.1"
HTTP_TIMEOUT = 30

# A transient hiccup must not abort a login the user may already be authorizing
# in the browser, so bad polls are retried before giving up.
MAX_SOFT_FAILURES = 5


class AuthError(Exception):
    """GitHub replied with something that isn't a device-flow response."""


def credentials_path() -> Path:
    return Path.home() / ".czdev" / "credentials"


def load_token() -> str:
    path = credentials_path()
    if not path.exists():
        print("Not logged in. Run `czdev login` first.", file=sys.stderr)
        sys.exit(1)
    data = json.loads(path.read_text())
    return data["github_token"]


def load_credentials() -> dict:
    path = credentials_path()
    if not path.exists():
        print("Not logged in. Run `czdev login` first.", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text())


def save_credentials(creds: dict):
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(creds, indent=2))
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def post_form(url: str, fields: dict) -> dict:
    """POST form fields to an OAuth endpoint and decode the reply as a dict.

    GitHub answers some device-flow states with a 4xx whose body is still a
    valid JSON error, so the HTTPError body is decoded rather than raised.
    The endpoint's *default* encoding is form-urlencoded (we only get JSON
    because of the Accept header), so that form is accepted as a fallback in
    case something strips the header. Anything else — an HTML rate-limit or
    captive-portal page, an empty body — raises AuthError carrying the status
    and a body snippet, instead of surfacing as a bare JSONDecodeError.
    """
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    try:
        resp = urllib.request.urlopen(req, timeout=HTTP_TIMEOUT)
        status, raw = resp.status, resp.read()
        ctype = resp.headers.get("content-type", "")
        # urllib follows redirects silently, so a 302 to some HTML sign-in or
        # portal page arrives here as a 200. Report where we actually landed.
        final_url = resp.geturl()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
        ctype = e.headers.get("content-type", "")
        final_url = url

    text = raw.decode("utf-8", "replace").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        if "access_token=" in text or "error=" in text:
            return {k: v[0] for k, v in urllib.parse.parse_qs(text).items()}

    where = f"{url} (redirected to {final_url})" if final_url != url else url
    snippet = " ".join(text.split())[:200] or "(empty body)"
    raise AuthError(f"HTTP {status} from {where} returned "
                    f"{ctype or 'an unknown content type'} instead of JSON. "
                    f"Body: {snippet}")


def login():
    print("Requesting device code from GitHub...")

    try:
        device_resp = post_form(DEVICE_CODE_URL, {
            "client_id": GITHUB_CLIENT_ID,
            "scope": "public_repo",
        })
    except (AuthError, OSError) as e:
        print(f"Could not request a device code: {e}", file=sys.stderr)
        sys.exit(1)

    if "device_code" not in device_resp:
        detail = (device_resp.get("error_description")
                  or device_resp.get("error") or device_resp)
        print(f"GitHub refused the device-code request: {detail}", file=sys.stderr)
        sys.exit(1)

    device_code = device_resp["device_code"]
    user_code = device_resp["user_code"]
    verification_uri = device_resp["verification_uri"]
    interval = int(device_resp.get("interval", 5))
    expires_in = int(device_resp.get("expires_in", 900))

    print()
    print(f"  Open:  {verification_uri}")
    print()
    print(f"  \033[1;91m{user_code}\033[0m")
    print()

    try:
        webbrowser.open(verification_uri)
    except Exception:
        pass

    print("Waiting for authorization (press Ctrl-C to cancel)...")

    try:
        token = poll_for_token(device_code, interval, expires_in)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        sys.exit(130)

    gh = GitHubClient(token)
    user = gh.get_user()

    creds = {
        "github_token": token,
        "github_username": user.login,
        "created_at": str(int(time.time())),
    }
    save_credentials(creds)

    print()
    print(f"✓ Logged in as {user.login} ({user.email or ''})")
    print(f"  Token saved to {credentials_path()}")


def poll_for_token(device_code: str, interval: int, expires_in: int) -> str:
    """Poll the token endpoint until the user authorizes, or give up."""
    deadline = time.monotonic() + expires_in
    soft_failures = 0

    while True:
        time.sleep(interval)
        if time.monotonic() > deadline:
            print("\nDevice code expired before it was authorized. "
                  "Run `czdev login` again.", file=sys.stderr)
            sys.exit(1)

        try:
            resp = post_form(ACCESS_TOKEN_URL, {
                "client_id": GITHUB_CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            })
        except (AuthError, OSError) as e:
            soft_failures += 1
            if soft_failures >= MAX_SOFT_FAILURES:
                print(f"\nGiving up after {soft_failures} failed polls.", file=sys.stderr)
                print(f"  {e}", file=sys.stderr)
                print("  If this looks like an HTML page, you are probably being "
                      "rate-limited by github.com or going through a proxy; wait a "
                      "minute and retry.", file=sys.stderr)
                sys.exit(1)
            print(f"  (retrying, {e})")
            continue
        soft_failures = 0

        if resp.get("access_token"):
            return resp["access_token"]

        error = resp.get("error", "")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            # The device-flow spec requires the interval to grow for the rest of
            # the session; keeping the old one just earns another slow_down.
            interval = int(resp.get("interval", interval + 5))
            continue
        if error == "expired_token":
            print("\nDevice code expired, please run `czdev login` again.", file=sys.stderr)
            sys.exit(1)
        if error == "access_denied":
            print("\nAuthorization was denied in the browser.", file=sys.stderr)
            sys.exit(1)

        detail = resp.get("error_description") or error or resp
        print(f"\nOAuth error: {detail}", file=sys.stderr)
        sys.exit(1)


def logout():
    path = credentials_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            username = data.get("github_username", "")
        except Exception:
            username = ""
        path.unlink()
        if username:
            print(f"Removed credentials for {username}.")
        else:
            print("Credentials removed.")
    else:
        print("Not logged in.")
