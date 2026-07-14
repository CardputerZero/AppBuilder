/**
 * CardputerZero developer portal — Cloudflare Worker.
 *
 * Serves the static portal (../site via the assets binding) and the API on
 * the same origin (no CORS involved):
 *
 *   GET  /auth/login      GitHub OAuth (scope: user:email — verified emails)
 *   GET  /auth/callback   code exchange, sets signed session cookie
 *   GET  /auth/logout
 *   GET  /api/me          {login, emails, is_admin}
 *   POST /api/submit      multipart: deb + package/version/arch + source_repo?
 *   POST /api/unpublish   json: {package, version}
 *
 * Trust model: the browser parses the .deb for preview UX, but nothing the
 * client sends is trusted for enforcement. This Worker re-checks ownership
 * and version monotonicity against the published APT index, and the
 * packages-repo GitHub Action re-validates everything with real dpkg-deb
 * before a publish PR is created.
 *
 * Secrets: GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, BOT_TOKEN, SESSION_SECRET.
 * Vars: see wrangler.toml.
 */

import { compareDebVersions, extractEmail } from "../../site/debparse.js";

const SESSION_COOKIE = "cz_session";
const STATE_COOKIE = "cz_oauth_state";
const SESSION_TTL_SECS = 24 * 3600;
const USER_AGENT = "cardputerzero-dev-portal/1.0";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      switch (`${request.method} ${url.pathname}`) {
        case "GET /auth/login": return authLogin(url, env);
        case "GET /auth/callback": return authCallback(request, url, env);
        case "GET /auth/logout": return authLogout(env);
        case "GET /api/me": return apiMe(request, env);
        case "POST /api/submit": return apiSubmit(request, env);
        case "POST /api/unpublish": return apiUnpublish(request, env);
      }
      // Everything else falls through to the static assets.
      return env.ASSETS.fetch(request);
    } catch (err) {
      console.error(err.stack || String(err));
      return json(500, { error: "internal", detail: String(err.message || err) });
    }
  },
};

/* ---------------------------------- auth --------------------------------- */

function authLogin(url, env) {
  const state = crypto.randomUUID();
  const target = new URL("https://github.com/login/oauth/authorize");
  target.searchParams.set("client_id", env.GITHUB_CLIENT_ID);
  target.searchParams.set("redirect_uri", `${url.origin}/auth/callback`);
  target.searchParams.set("scope", "user:email"); // verified email addresses
  target.searchParams.set("state", state);
  return new Response(null, {
    status: 302,
    headers: {
      Location: target.toString(),
      "Set-Cookie": cookie(STATE_COOKIE, state, 600),
    },
  });
}

async function authCallback(request, url, env) {
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  if (!code || !state || parseCookies(request)[STATE_COOKIE] !== state) {
    return json(400, { error: "bad_oauth_state" });
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
  const { access_token: token } = await tokenResp.json();
  if (!token) return json(502, { error: "oauth_exchange_failed" });

  // Read identity + verified emails, then discard the user token.
  const userHeaders = {
    authorization: `Bearer ${token}`,
    accept: "application/vnd.github+json",
    "user-agent": USER_AGENT,
  };
  const user = await (await fetch("https://api.github.com/user", { headers: userHeaders })).json();
  let verified = [];
  try {
    const emails = await (await fetch("https://api.github.com/user/emails", { headers: userHeaders })).json();
    if (Array.isArray(emails)) {
      verified = emails.filter((e) => e.verified).map((e) => e.email.toLowerCase());
    }
  } catch { /* user:email may be revoked; noreply fallback below still works */ }

  const emails = [...new Set([
    `${user.login.toLowerCase()}@users.noreply.github.com`,
    `${user.id}+${user.login.toLowerCase()}@users.noreply.github.com`,
    ...verified,
  ])].slice(0, 12);

  const isAdmin = (env.ADMIN_LOGINS || "")
    .split(",").map((s) => s.trim().toLowerCase()).filter(Boolean)
    .includes(user.login.toLowerCase());

  const session = await sealSession(env, {
    l: user.login,
    e: emails,
    a: isAdmin,
    x: Math.floor(Date.now() / 1000) + SESSION_TTL_SECS,
  });
  const headers = new Headers({ Location: env.RETURN_PATH || "/" });
  headers.append("Set-Cookie", cookie(SESSION_COOKIE, session, SESSION_TTL_SECS));
  headers.append("Set-Cookie", cookie(STATE_COOKIE, "", 0));
  return new Response(null, { status: 302, headers });
}

function authLogout(env) {
  return new Response(null, {
    status: 302,
    headers: {
      Location: env.RETURN_PATH || "/",
      "Set-Cookie": cookie(SESSION_COOKIE, "", 0),
    },
  });
}

async function apiMe(request, env) {
  const s = await openSession(request, env);
  if (!s) return json(401, { error: "not_logged_in" });
  return json(200, { login: s.l, emails: s.e, is_admin: s.a });
}

/* --------------------------------- submit -------------------------------- */

async function apiSubmit(request, env) {
  const s = await openSession(request, env);
  if (!s) return json(401, { error: "not_logged_in" });

  const form = await request.formData();
  const file = form.get("deb");
  const pkg = String(form.get("package") || "").trim();
  const version = String(form.get("version") || "").trim();
  const arch = String(form.get("arch") || "").trim();
  const sourceRepo = String(form.get("source_repo") || "").trim();

  if (!(file && typeof file.arrayBuffer === "function")) return json(400, { error: "missing_deb_file" });
  if (!/^[a-z0-9][a-z0-9.+-]+$/.test(pkg)) return json(400, { error: "bad_package_name" });
  if (!/^[a-zA-Z0-9.+~:-]+$/.test(version)) return json(400, { error: "bad_version" });
  if (!["arm64", "all"].includes(arch)) return json(400, { error: "bad_arch" });
  if (sourceRepo && !/^https:\/\/[\w.-]+\/[\w./-]+$/.test(sourceRepo)) {
    return json(400, { error: "bad_source_repo" });
  }

  const maxBytes = Number(env.MAX_SIZE_MB || "64") * 1024 * 1024;
  const buf = await file.arrayBuffer();
  if (buf.byteLength === 0 || buf.byteLength > maxBytes) {
    return json(413, { error: "bad_size", detail: `1B ~ ${env.MAX_SIZE_MB || "64"}MB` });
  }
  if (new TextDecoder().decode(new Uint8Array(buf, 0, 8)) !== "!<arch>\n") {
    return json(400, { error: "not_a_deb" });
  }

  // Pre-flight against the published index: name squatting + version bump.
  // (Authoritative re-check happens in the packages-repo Action.)
  const index = await fetchIndex(env);
  const entries = index.get(pkg) || [];
  if (entries.length) {
    const owner = extractEmail(entries[0].maintainer).toLowerCase();
    if (!s.a && !s.e.includes(owner)) {
      return json(403, {
        error: "not_owner",
        detail: `包名 "${pkg}" 已被 ${maskEmail(owner)} 占用，只有该邮箱对应的 GitHub 账号或管理员可以更新/下架`,
      });
    }
    const latest = entries.map((e) => e.version).sort(compareDebVersions).pop();
    if (compareDebVersions(version, latest) === 0) {
      return json(409, { error: "version_exists", detail: `版本 ${version} 已存在，请提升版本号` });
    }
    if (compareDebVersions(version, latest) < 0) {
      return json(409, { error: "version_too_old", detail: `版本必须高于已发布的 ${latest}` });
    }
  }

  const sha256 = hex(await crypto.subtle.digest("SHA-256", buf));
  const canonical = `${pkg}_${version}_${arch}.deb`.replace(/~/g, ".");
  const assetName = `${s.l}__${canonical}`;

  const release = await ensureBufferRelease(env);
  await deleteAsset(env, release, assetName);
  const upload = await fetch(
    `https://uploads.github.com/repos/${env.TARGET_OWNER}/${env.TARGET_REPO}` +
    `/releases/${release.id}/assets?name=${encodeURIComponent(assetName)}`,
    { method: "POST", headers: { ...bot(env), "content-type": "application/octet-stream" }, body: buf },
  );
  if (!upload.ok) return json(502, { error: "upload_failed", detail: await safeText(upload) });
  const asset = await upload.json();

  const dispatch = await gh(env, `/repos/${env.TARGET_OWNER}/${env.TARGET_REPO}/dispatches`, {
    method: "POST",
    body: JSON.stringify({
      event_type: "web-submission",
      client_payload: {
        login: s.l,
        emails: s.e,
        is_admin: s.a,
        package: pkg,
        version,
        architecture: arch,
        filename: canonical,
        url: asset.browser_download_url,
        sha256,
        size: buf.byteLength,
        source_repo: sourceRepo,
      },
    }),
  });
  if (dispatch.status !== 204) return json(502, { error: "dispatch_failed", detail: await safeText(dispatch) });

  return json(200, {
    ok: true,
    sha256,
    message: "已提交，服务器正在做最终校验；通过后会自动生成发布 PR。",
    track_url: `https://github.com/${env.TARGET_OWNER}/${env.TARGET_REPO}/pulls?q=is%3Apr+${pkg}`,
    actions_url: `https://github.com/${env.TARGET_OWNER}/${env.TARGET_REPO}/actions/workflows/process-web-submission.yml`,
  });
}

/* -------------------------------- unpublish ------------------------------ */

async function apiUnpublish(request, env) {
  const s = await openSession(request, env);
  if (!s) return json(401, { error: "not_logged_in" });

  const { package: pkg, version } = await request.json();
  if (!/^[a-z0-9][a-z0-9.+-]+$/.test(pkg || "")) return json(400, { error: "bad_package_name" });
  if (!/^[a-zA-Z0-9.+~:-]+$/.test(version || "")) return json(400, { error: "bad_version" });

  const index = await fetchIndex(env);
  const entries = index.get(pkg) || [];
  const entry = entries.find((e) => e.version === version);
  if (!entry) return json(404, { error: "not_published", detail: `${pkg} ${version} 不在线上索引中` });

  const owner = extractEmail(entry.maintainer).toLowerCase();
  if (!s.a && !s.e.includes(owner)) {
    return json(403, { error: "not_owner", detail: "只有包的 Maintainer 邮箱对应的账号或管理员可以下架" });
  }

  const dispatch = await gh(env, `/repos/${env.TARGET_OWNER}/${env.TARGET_REPO}/dispatches`, {
    method: "POST",
    body: JSON.stringify({
      event_type: "web-unpublish",
      client_payload: {
        login: s.l,
        emails: s.e,
        is_admin: s.a,
        package: pkg,
        version,
        architecture: entry.architecture || "arm64",
      },
    }),
  });
  if (dispatch.status !== 204) return json(502, { error: "dispatch_failed", detail: await safeText(dispatch) });

  return json(200, {
    ok: true,
    message: "下架请求已提交，将自动生成移除 PR。",
    track_url: `https://github.com/${env.TARGET_OWNER}/${env.TARGET_REPO}/pulls?q=is%3Apr+unpublish+${pkg}`,
  });
}

/* ------------------------------ APT index -------------------------------- */

/** Parse the published Packages index into Map<name, [{version, maintainer, architecture}]>. */
async function fetchIndex(env) {
  const map = new Map();
  let text = "";
  try {
    const resp = await fetch(env.PACKAGES_INDEX_URL, {
      headers: { "user-agent": USER_AGENT },
      cf: { cacheTtl: 60 },
    });
    if (resp.ok) text = await resp.text();
  } catch { /* index unreachable — treat as empty; Action re-checks anyway */ }

  for (const para of text.split(/\n\n+/)) {
    const fields = {};
    for (const line of para.split("\n")) {
      const idx = line.indexOf(": ");
      if (idx > 0 && !/^\s/.test(line)) fields[line.slice(0, idx)] = line.slice(idx + 2).trim();
    }
    if (!fields.Package) continue;
    if (!map.has(fields.Package)) map.set(fields.Package, []);
    map.get(fields.Package).push({
      version: fields.Version || "",
      maintainer: fields.Maintainer || "",
      architecture: fields.Architecture || "",
    });
  }
  return map;
}

/* ------------------------------ github utils ----------------------------- */

function bot(env) {
  return {
    authorization: `Bearer ${env.BOT_TOKEN}`,
    accept: "application/vnd.github+json",
    "user-agent": USER_AGENT,
  };
}

function gh(env, path, init = {}) {
  return fetch(`https://api.github.com${path}`, { ...init, headers: { ...bot(env), ...(init.headers || {}) } });
}

async function ensureBufferRelease(env) {
  const tag = env.BUFFER_TAG || "web-upload-buffer";
  const existing = await gh(env, `/repos/${env.TARGET_OWNER}/${env.TARGET_REPO}/releases/tags/${tag}`);
  if (existing.ok) return existing.json();
  if (existing.status !== 404) throw new Error(`release lookup failed: ${existing.status}`);
  const created = await gh(env, `/repos/${env.TARGET_OWNER}/${env.TARGET_REPO}/releases`, {
    method: "POST",
    body: JSON.stringify({
      tag_name: tag,
      name: "web upload buffer",
      prerelease: true,
      body: "Holds .deb files uploaded via the developer portal pending review.",
    }),
  });
  if (!created.ok) throw new Error(`release create failed: ${created.status}`);
  return created.json();
}

async function deleteAsset(env, release, name) {
  const found = (release.assets || []).find((a) => a.name === name);
  if (found) {
    await gh(env, `/repos/${env.TARGET_OWNER}/${env.TARGET_REPO}/releases/assets/${found.id}`, { method: "DELETE" });
  }
}

/* -------------------------------- sessions ------------------------------- */

async function sealSession(env, obj) {
  const payload = b64url(new TextEncoder().encode(JSON.stringify(obj)));
  return `${payload}.${await hmac(env.SESSION_SECRET, payload)}`;
}

async function openSession(request, env) {
  const raw = parseCookies(request)[SESSION_COOKIE];
  if (!raw) return null;
  const idx = raw.lastIndexOf(".");
  if (idx <= 0) return null;
  const payload = raw.slice(0, idx);
  if (raw.slice(idx + 1) !== (await hmac(env.SESSION_SECRET, payload))) return null;
  try {
    const obj = JSON.parse(new TextDecoder().decode(unb64url(payload)));
    if (!obj.l || !Array.isArray(obj.e) || obj.x < Date.now() / 1000) return null;
    obj.e = obj.e.map((e) => String(e).toLowerCase());
    return obj;
  } catch {
    return null;
  }
}

/* --------------------------------- misc ---------------------------------- */

function json(status, obj) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function cookie(name, value, maxAge) {
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

async function hmac(secret, message) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  return hex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message)));
}

function hex(buf) {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function b64url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function unb64url(str) {
  const s = str.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
}

function maskEmail(email) {
  const [user, domain] = email.split("@");
  if (!domain) return "***";
  return `${user.slice(0, 2)}***@${domain}`;
}
