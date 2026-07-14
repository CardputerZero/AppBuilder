/**
 * cardputer.cc submission API — Cloudflare Worker.
 *
 * Accepts .deb uploads from the static store site and hands them to the
 * CardputerZero/packages review pipeline:
 *
 *   browser ──POST /api/submit──▶ this Worker ──▶ buffer Release asset
 *                                      │              (packages repo)
 *                                      └──▶ repository_dispatch "web-submission"
 *                                           → Actions validates + opens the
 *                                             publish PR for maintainer review
 *
 * Identity: GitHub OAuth web flow (empty scope — public profile only). The
 * user's OAuth token is used once to read the login and is never stored;
 * uploads happen with the bot token (BOT_TOKEN secret).
 *
 * Routes:
 *   GET  /auth/login     redirect to GitHub OAuth
 *   GET  /auth/callback  OAuth code exchange, sets session cookie
 *   GET  /auth/logout    clears the session
 *   GET  /api/me         current session {login} or 401
 *   POST /api/submit     multipart form: deb=<file>, source_repo=<url>
 *
 * Required secrets (wrangler secret put ...):
 *   GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET  — OAuth App for the login flow
 *   BOT_TOKEN      — PAT with contents:write on TARGET_OWNER/TARGET_REPO
 *   SESSION_SECRET — random string for HMAC-signing session cookies
 *
 * Vars (wrangler.toml): ALLOWED_ORIGIN, RETURN_URL, TARGET_OWNER,
 *   TARGET_REPO, BUFFER_TAG, MAX_SIZE_MB.
 */

const SESSION_COOKIE = "cz_session";
const STATE_COOKIE = "cz_oauth_state";
const SESSION_TTL_SECS = 24 * 3600;
const USER_AGENT = "cardputer-submit-worker/1.0";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (request.method === "OPTIONS") {
        return new Response(null, { status: 204, headers: cors(env) });
      }
      if (url.pathname === "/auth/login" && request.method === "GET") {
        return authLogin(url, env);
      }
      if (url.pathname === "/auth/callback" && request.method === "GET") {
        return authCallback(request, url, env);
      }
      if (url.pathname === "/auth/logout" && request.method === "GET") {
        return authLogout(env);
      }
      if (url.pathname === "/api/me" && request.method === "GET") {
        return apiMe(request, env);
      }
      if (url.pathname === "/api/submit" && request.method === "POST") {
        return apiSubmit(request, env);
      }
      return json(env, 404, { error: "not_found" });
    } catch (err) {
      console.error(err.stack || String(err));
      return json(env, 500, { error: "internal", detail: String(err.message || err) });
    }
  },
};

/* ---------------------------------- auth --------------------------------- */

function authLogin(url, env) {
  const state = crypto.randomUUID();
  const target = new URL("https://github.com/login/oauth/authorize");
  target.searchParams.set("client_id", env.GITHUB_CLIENT_ID);
  target.searchParams.set("redirect_uri", `${url.origin}/auth/callback`);
  target.searchParams.set("state", state);
  // Empty scope on purpose: we only need the public login for identity.
  return new Response(null, {
    status: 302,
    headers: {
      Location: target.toString(),
      "Set-Cookie": setCookie(STATE_COOKIE, state, 600),
    },
  });
}

async function authCallback(request, url, env) {
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const cookies = parseCookies(request);
  if (!code || !state || cookies[STATE_COOKIE] !== state) {
    return json(env, 400, { error: "bad_oauth_state" });
  }

  const tokenResp = await fetch("https://github.com/login/oauth/access_token", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify({
      client_id: env.GITHUB_CLIENT_ID,
      client_secret: env.GITHUB_CLIENT_SECRET,
      code,
    }),
  });
  const tokenData = await tokenResp.json();
  if (!tokenData.access_token) {
    return json(env, 502, { error: "oauth_exchange_failed", detail: tokenData.error || "" });
  }

  const userResp = await fetch("https://api.github.com/user", {
    headers: {
      authorization: `Bearer ${tokenData.access_token}`,
      accept: "application/vnd.github+json",
      "user-agent": USER_AGENT,
    },
  });
  if (!userResp.ok) {
    return json(env, 502, { error: "user_lookup_failed" });
  }
  const user = await userResp.json();

  const session = await makeSession(env, user.login);
  const headers = new Headers({ Location: env.RETURN_URL });
  headers.append("Set-Cookie", setCookie(SESSION_COOKIE, session, SESSION_TTL_SECS));
  headers.append("Set-Cookie", setCookie(STATE_COOKIE, "", 0));
  return new Response(null, { status: 302, headers });
}

function authLogout(env) {
  return new Response(null, {
    status: 302,
    headers: {
      Location: env.RETURN_URL,
      "Set-Cookie": setCookie(SESSION_COOKIE, "", 0),
    },
  });
}

async function apiMe(request, env) {
  const login = await readSession(request, env);
  if (!login) return json(env, 401, { error: "not_logged_in" });
  return json(env, 200, { login });
}

/* --------------------------------- submit -------------------------------- */

async function apiSubmit(request, env) {
  const login = await readSession(request, env);
  if (!login) return json(env, 401, { error: "not_logged_in" });

  const form = await request.formData();
  const file = form.get("deb");
  const sourceRepo = String(form.get("source_repo") || "").trim();

  if (!(file && typeof file.arrayBuffer === "function" && file.name)) {
    return json(env, 400, { error: "missing_deb_file" });
  }
  if (!/^https:\/\/[\w.-]+\/[\w.-]+\/[\w.-]+\/?$/.test(sourceRepo)) {
    return json(env, 400, {
      error: "bad_source_repo",
      detail: "source_repo must be a public https git URL like https://github.com/you/app",
    });
  }
  const fileName = file.name;
  if (!/^[a-z0-9][a-z0-9.+~_-]*\.deb$/.test(fileName)) {
    return json(env, 400, {
      error: "bad_filename",
      detail: "expected <package>_<version>_<arch>.deb",
    });
  }

  const maxBytes = Number(env.MAX_SIZE_MB || "64") * 1024 * 1024;
  const buf = await file.arrayBuffer();
  if (buf.byteLength === 0) return json(env, 400, { error: "empty_file" });
  if (buf.byteLength > maxBytes) {
    return json(env, 413, { error: "too_large", detail: `limit is ${env.MAX_SIZE_MB || "64"} MB` });
  }

  // .deb files are `ar` archives: magic "!<arch>\n".
  const magic = new TextDecoder().decode(new Uint8Array(buf, 0, 8));
  if (magic !== "!<arch>\n") {
    return json(env, 400, { error: "not_a_deb", detail: "file is not a Debian package" });
  }

  const sha256 = hex(await crypto.subtle.digest("SHA-256", buf));

  // Namespace the buffer asset by uploader; the workflow renames to the
  // canonical <pkg>_<ver>_<arch>.deb when writing the manifest.
  const assetName = `${login}__${fileName}`.replace(/~/g, ".");

  const release = await ensureBufferRelease(env);
  await deleteExistingAsset(env, release, assetName);

  const uploadUrl =
    `https://uploads.github.com/repos/${env.TARGET_OWNER}/${env.TARGET_REPO}` +
    `/releases/${release.id}/assets?name=${encodeURIComponent(assetName)}`;
  const uploadResp = await fetch(uploadUrl, {
    method: "POST",
    headers: {
      ...botHeaders(env),
      "content-type": "application/octet-stream",
    },
    body: buf,
  });
  if (!uploadResp.ok) {
    return json(env, 502, { error: "upload_failed", detail: await safeText(uploadResp) });
  }
  const asset = await uploadResp.json();

  const dispatchResp = await gh(env, `/repos/${env.TARGET_OWNER}/${env.TARGET_REPO}/dispatches`, {
    method: "POST",
    body: JSON.stringify({
      event_type: "web-submission",
      client_payload: {
        login,
        filename: fileName,
        asset_name: assetName,
        url: asset.browser_download_url,
        sha256,
        size: buf.byteLength,
        source_repo: sourceRepo,
      },
    }),
  });
  if (dispatchResp.status !== 204) {
    return json(env, 502, { error: "dispatch_failed", detail: await safeText(dispatchResp) });
  }

  return json(env, 200, {
    ok: true,
    login,
    sha256,
    message: "Submitted. Validation runs in CI; a publish PR will mention you when it opens.",
    track_url: `https://github.com/${env.TARGET_OWNER}/${env.TARGET_REPO}/pulls?q=is%3Apr+${encodeURIComponent(login)}`,
  });
}

async function ensureBufferRelease(env) {
  const tag = env.BUFFER_TAG || "web-upload-buffer";
  const existing = await gh(env, `/repos/${env.TARGET_OWNER}/${env.TARGET_REPO}/releases/tags/${tag}`);
  if (existing.ok) return existing.json();
  if (existing.status !== 404) {
    throw new Error(`release lookup failed: ${existing.status}`);
  }
  const created = await gh(env, `/repos/${env.TARGET_OWNER}/${env.TARGET_REPO}/releases`, {
    method: "POST",
    body: JSON.stringify({
      tag_name: tag,
      name: "web upload buffer",
      prerelease: true,
      body: "Holds .deb files uploaded via cardputer.cc pending review.",
    }),
  });
  if (!created.ok) throw new Error(`release create failed: ${created.status}`);
  return created.json();
}

async function deleteExistingAsset(env, release, assetName) {
  const found = (release.assets || []).find((a) => a.name === assetName);
  if (!found) return;
  await gh(env, `/repos/${env.TARGET_OWNER}/${env.TARGET_REPO}/releases/assets/${found.id}`, {
    method: "DELETE",
  });
}

/* --------------------------------- helpers ------------------------------- */

function cors(env) {
  return {
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    Vary: "Origin",
  };
}

function json(env, status, obj) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...cors(env) },
  });
}

function botHeaders(env) {
  return {
    authorization: `Bearer ${env.BOT_TOKEN}`,
    accept: "application/vnd.github+json",
    "user-agent": USER_AGENT,
  };
}

function gh(env, path, init = {}) {
  return fetch(`https://api.github.com${path}`, {
    ...init,
    headers: { ...botHeaders(env), ...(init.headers || {}) },
  });
}

async function safeText(resp) {
  try {
    return (await resp.text()).slice(0, 500);
  } catch {
    return "";
  }
}

function setCookie(name, value, maxAge) {
  return `${name}=${value}; Max-Age=${maxAge}; Path=/; Secure; HttpOnly; SameSite=Lax`;
}

function parseCookies(request) {
  const out = {};
  for (const part of (request.headers.get("cookie") || "").split(";")) {
    const idx = part.indexOf("=");
    if (idx > 0) out[part.slice(0, idx).trim()] = part.slice(idx + 1).trim();
  }
  return out;
}

async function makeSession(env, login) {
  const exp = Math.floor(Date.now() / 1000) + SESSION_TTL_SECS;
  const payload = `${login}.${exp}`;
  return `${payload}.${await hmac(env.SESSION_SECRET, payload)}`;
}

async function readSession(request, env) {
  const raw = parseCookies(request)[SESSION_COOKIE];
  if (!raw) return null;
  const idx = raw.lastIndexOf(".");
  if (idx <= 0) return null;
  const payload = raw.slice(0, idx);
  const sig = raw.slice(idx + 1);
  if (sig !== (await hmac(env.SESSION_SECRET, payload))) return null;
  const sep = payload.lastIndexOf(".");
  const login = payload.slice(0, sep);
  const exp = Number(payload.slice(sep + 1));
  if (!login || !Number.isFinite(exp) || exp < Date.now() / 1000) return null;
  return login;
}

async function hmac(secret, message) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return hex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message)));
}

function hex(buf) {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
