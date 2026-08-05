"""GitHub API client — mirrors the Rust GitHubClient."""

import http.client
import io
import json
import random
import socket
import ssl
import time
import urllib.request
import urllib.error
import urllib.parse
from typing import Optional

GITHUB_API = "https://api.github.com"
GITHUB_UPLOADS = "https://uploads.github.com"

USER_AGENT = "czdev/0.1"
HTTP_TIMEOUT = 60
# A release asset can be hundreds of MB on a slow uplink, so it gets its own
# budget instead of the API timeout.
UPLOAD_TIMEOUT = 3600
MAX_ATTEMPTS = 4
# Statuses where GitHub is telling us this is not its final answer.
RETRY_STATUSES = (429, 500, 502, 503, 504)


class GitHubError(Exception):
    """A request could not be completed, or the reply was not usable."""


class _Transient(Exception):
    """A network-level failure that is worth retrying."""


def _read_body(resp) -> bytes:
    """Read a response body in full.

    A body that stops short of its Content-Length means the connection was cut
    mid-response — the signature of a flaky link or an intercepting proxy. That
    is transient rather than a protocol error, so it is reported as such instead
    of escaping as an IncompleteRead from inside json.loads.
    """
    try:
        return resp.read()
    except http.client.IncompleteRead as e:
        got = len(e.partial)
        want = f" of {got + e.expected}" if e.expected else ""
        raise _Transient(f"connection closed after {got}{want} bytes") from e
    except (socket.timeout, TimeoutError, ConnectionError) as e:
        raise _Transient(str(e) or e.__class__.__name__) from e


def _snippet(raw: bytes, limit: int = 200) -> str:
    return " ".join(raw.decode("utf-8", "replace").split())[:limit] or "(empty body)"


def _parse_json(raw: bytes, status: int, headers, url: str) -> dict:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        ctype = headers.get("content-type") or "an unknown content type"
        raise GitHubError(f"HTTP {status} from {url} returned {ctype} instead "
                          f"of JSON. Body: {_snippet(raw)}") from e


def _http_error(url: str, status: int, headers, raw: bytes) -> urllib.error.HTTPError:
    """Build the HTTPError callers switch on, with the body in the message."""
    return urllib.error.HTTPError(url, status, _snippet(raw), headers, io.BytesIO(raw))


def _sleep_before_retry(attempt: int, retry_after: Optional[str] = None) -> None:
    delay = min(2 ** attempt, 30) * (0.5 + random.random())
    if retry_after:
        try:
            delay = max(delay, min(float(retry_after), 60))
        except ValueError:
            pass
    time.sleep(delay)


class Permission:
    NONE = 0
    READ = 1
    WRITE = 2
    ADMIN = 3


class User:
    def __init__(self, login: str, email: Optional[str] = None):
        self.login = login
        self.email = email


class PullRequestResponse:
    def __init__(self, html_url: str, number: int):
        self.html_url = html_url
        self.number = number


class GitHubClient:
    def __init__(self, token: str):
        self.token = token
        self._ctx = ssl.create_default_context()

    def _send(self, req, timeout):
        """One round trip. An error status is an answer, not an exception."""
        try:
            resp = urllib.request.urlopen(req, timeout=timeout, context=self._ctx)
            return resp.status, resp.headers, _read_body(resp)
        except urllib.error.HTTPError as e:
            return e.code, e.headers, _read_body(e)
        except urllib.error.URLError as e:
            raise _Transient(str(e.reason)) from e
        except (socket.timeout, TimeoutError, ConnectionError) as e:
            raise _Transient(str(e) or e.__class__.__name__) from e

    def _fetch(self, method: str, url: str, data=None,
               accept="application/vnd.github+json", content_type=None,
               timeout=HTTP_TIMEOUT, retry_safe=None, retry_statuses=True):
        """Run one call with retries, returning (status, headers, body).

        Only GETs are replayed after a network failure: a POST that died
        mid-response may still have been applied, and re-sending it could create
        a second commit, ref or pull request. Callers that know how to clean up
        after themselves pass retry_safe=True.
        """
        if retry_safe is None:
            retry_safe = method in ("GET", "HEAD")
        for attempt in range(MAX_ATTEMPTS):
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Authorization", f"Bearer {self.token}")
            req.add_header("User-Agent", USER_AGENT)
            req.add_header("Accept", accept)
            if content_type:
                req.add_header("Content-Type", content_type)
            try:
                status, headers, raw = self._send(req, timeout)
            except _Transient as e:
                if not retry_safe:
                    raise GitHubError(f"{method} {url} failed: {e}") from e
                if attempt == MAX_ATTEMPTS - 1:
                    raise GitHubError(
                        f"{method} {url} failed {MAX_ATTEMPTS} times: {e}. "
                        f"A proxy or VPN cutting connections to GitHub looks "
                        f"exactly like this — try another network.") from e
                _sleep_before_retry(attempt)
                continue
            if retry_statuses and status in RETRY_STATUSES and attempt < MAX_ATTEMPTS - 1:
                _sleep_before_retry(attempt, headers.get("retry-after"))
                continue
            return status, headers, raw

    def _request(self, method: str, path: str, body=None, accept="application/vnd.github+json") -> dict:
        url = f"{GITHUB_API}{path}" if path.startswith("/") else path
        data = json.dumps(body).encode() if body is not None else None
        status, headers, raw = self._fetch(
            method, url, data=data, accept=accept,
            content_type="application/json" if data is not None else None)
        if status >= 400:
            raise _http_error(url, status, headers, raw)
        if status == 204 or not raw.strip():
            return {}
        return _parse_json(raw, status, headers, url)

    def _get(self, path: str, accept="application/vnd.github+json"):
        return self._request("GET", path, accept=accept)

    def _post(self, path: str, body=None):
        return self._request("POST", path, body=body)

    def get_user(self) -> User:
        data = self._get("/user")
        return User(login=data["login"], email=data.get("email"))

    def check_permission(self, owner: str, repo: str) -> int:
        """Return the authenticated user's permission level on a repo.

        Uses `GET /repos/{owner}/{repo}`, whose `permissions` object reflects the
        token owner's access. Unlike the collaborators/{user}/permission endpoint,
        this does NOT require the caller to already have push access, so external
        contributors (who should fall through to the fork+PR path) get a clean
        answer instead of a 403 Forbidden.
        """
        try:
            data = self._get(f"/repos/{owner}/{repo}")
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return Permission.NONE
            raise
        perms = data.get("permissions", {})
        if perms.get("admin"):
            return Permission.ADMIN
        if perms.get("maintain") or perms.get("push"):
            return Permission.WRITE
        if perms.get("pull") or perms.get("triage"):
            return Permission.READ
        return Permission.NONE

    def fork_repo(self, owner: str, repo: str) -> str:
        data = self._post(f"/repos/{owner}/{repo}/forks", body={})
        return data["full_name"]

    def get_ref_sha(self, owner: str, repo: str, ref_name: str) -> str:
        data = self._get(f"/repos/{owner}/{repo}/git/ref/{ref_name}")
        return data["object"]["sha"]

    def get_commit(self, owner: str, repo: str, sha: str) -> tuple:
        data = self._get(f"/repos/{owner}/{repo}/git/commits/{sha}")
        return (data["sha"], data["tree"]["sha"])

    def create_blob(self, owner: str, repo: str, content_base64: str) -> str:
        data = self._post(f"/repos/{owner}/{repo}/git/blobs", body={
            "content": content_base64,
            "encoding": "base64",
        })
        return data["sha"]

    def create_tree(self, owner: str, repo: str, base_tree: str, path: str, blob_sha: Optional[str]) -> str:
        entry = {
            "path": path,
            "mode": "100644",
            "type": "blob",
        }
        if blob_sha is not None:
            entry["sha"] = blob_sha
        else:
            entry["sha"] = None
        data = self._post(f"/repos/{owner}/{repo}/git/trees", body={
            "base_tree": base_tree,
            "tree": [entry],
        })
        return data["sha"]

    def create_commit(self, owner: str, repo: str, message: str, tree_sha: str, parent_sha: str) -> str:
        data = self._post(f"/repos/{owner}/{repo}/git/commits", body={
            "message": message,
            "tree": tree_sha,
            "parents": [parent_sha],
        })
        return data["sha"]

    def create_ref(self, owner: str, repo: str, ref_name: str, sha: str):
        self._post(f"/repos/{owner}/{repo}/git/refs", body={
            "ref": f"refs/heads/{ref_name}",
            "sha": sha,
        })

    def create_pull_request(self, owner: str, repo: str, title: str, body: str, head: str, base: str) -> PullRequestResponse:
        data = self._post(f"/repos/{owner}/{repo}/pulls", body={
            "title": title,
            "body": body,
            "head": head,
            "base": base,
        })
        return PullRequestResponse(html_url=data["html_url"], number=data["number"])

    def ensure_release(self, owner: str, repo: str, tag: str,
                       name: Optional[str] = None, prerelease: bool = True) -> dict:
        """Return the release for `tag`, creating it (prerelease) if missing."""
        try:
            return self._get(f"/repos/{owner}/{repo}/releases/tags/{tag}")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        return self._post(f"/repos/{owner}/{repo}/releases", body={
            "tag_name": tag,
            "name": name or tag,
            "prerelease": prerelease,
            "body": "czdev upload buffer. Holds .deb assets referenced by package PRs.",
        })

    def find_release_asset(self, release: dict, name: str) -> Optional[dict]:
        for asset in release.get("assets", []):
            if asset.get("name") == name:
                return asset
        return None

    def delete_release_asset(self, owner: str, repo: str, asset_id: int) -> None:
        self._request("DELETE", f"/repos/{owner}/{repo}/releases/assets/{asset_id}")

    def upload_release_asset(self, owner: str, repo: str, release: dict,
                             file_path: str, name: str) -> str:
        """Upload `file_path` as a release asset, replacing any existing one.

        Returns the browser_download_url.
        """
        release_id = release["id"]
        url = f"{GITHUB_UPLOADS}/repos/{owner}/{repo}/releases/{release_id}/assets?name={urllib.parse.quote(name)}"
        with open(file_path, "rb") as f:
            data = f.read()

        for attempt in range(MAX_ATTEMPTS):
            # Re-read the release on every round: a previous attempt may have
            # left a half-uploaded asset behind, and GitHub rejects a duplicate
            # name with a 422.
            current = release if attempt == 0 else self._get(
                f"/repos/{owner}/{repo}/releases/{release_id}")
            existing = self.find_release_asset(current, name)
            if existing:
                self.delete_release_asset(owner, repo, existing["id"])
            try:
                status, headers, raw = self._fetch(
                    "POST", url, data=data,
                    content_type="application/octet-stream",
                    timeout=UPLOAD_TIMEOUT, retry_safe=False, retry_statuses=False)
            except GitHubError as e:
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                print(f"\n    upload failed ({e}); retrying... ", end="", flush=True)
                _sleep_before_retry(attempt)
                continue
            if status in RETRY_STATUSES and attempt < MAX_ATTEMPTS - 1:
                _sleep_before_retry(attempt, headers.get("retry-after"))
                continue
            if status >= 400:
                raise _http_error(url, status, headers, raw)
            return _parse_json(raw, status, headers, url)["browser_download_url"]

    def get_file_content(self, owner: str, repo: str, path: str) -> bytes:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
        status, headers, raw = self._fetch(
            "GET", url, accept="application/vnd.github.raw+json")
        if status == 404:
            raise FileNotFoundError(f"file not found: {path}")
        if status >= 400:
            raise _http_error(url, status, headers, raw)
        return raw
