"""GitHub API client — mirrors the Rust GitHubClient."""

import urllib.request
import urllib.error
import urllib.parse
import json
import ssl
from typing import Optional

GITHUB_API = "https://api.github.com"
GITHUB_UPLOADS = "https://uploads.github.com"


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

    def _request(self, method: str, path: str, body=None, accept="application/vnd.github+json") -> dict:
        url = f"{GITHUB_API}{path}" if path.startswith("/") else path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("User-Agent", "czdev/0.1")
        req.add_header("Accept", accept)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        resp = urllib.request.urlopen(req, context=self._ctx)
        if resp.status == 204:
            return {}
        return json.loads(resp.read().decode())

    def _get(self, path: str, accept="application/vnd.github+json"):
        return self._request("GET", path, accept=accept)

    def _post(self, path: str, body=None):
        return self._request("POST", path, body=body)

    def get_user(self) -> User:
        data = self._get("/user")
        return User(login=data["login"], email=data.get("email"))

    def check_permission(self, owner: str, repo: str, username: str) -> int:
        try:
            data = self._get(f"/repos/{owner}/{repo}/collaborators/{username}/permission")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return Permission.NONE
            raise
        perm = data.get("permission", "")
        if perm == "admin":
            return Permission.ADMIN
        if perm in ("maintain", "write"):
            return Permission.WRITE
        if perm in ("read", "triage"):
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
        existing = self.find_release_asset(release, name)
        if existing:
            self.delete_release_asset(owner, repo, existing["id"])
        release_id = release["id"]
        url = f"{GITHUB_UPLOADS}/repos/{owner}/{repo}/releases/{release_id}/assets?name={urllib.parse.quote(name)}"
        with open(file_path, "rb") as f:
            data = f.read()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("User-Agent", "czdev/0.1")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Content-Type", "application/octet-stream")
        resp = urllib.request.urlopen(req, context=self._ctx)
        return json.loads(resp.read().decode())["browser_download_url"]

    def get_file_content(self, owner: str, repo: str, path: str) -> bytes:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("User-Agent", "czdev/0.1")
        req.add_header("Accept", "application/vnd.github.raw+json")
        try:
            resp = urllib.request.urlopen(req, context=self._ctx)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise FileNotFoundError(f"file not found: {path}")
            raise
        return resp.read()
