# cardputer.cc 在线提交：网页直接上传 .deb

利用自有域名 `cardputer.cc` + 一个 Cloudflare Worker，实现「开发者在网页上直接
上传 `.deb` → 自动校验 → 自动开发布 PR → 管理员审核上架」。浏览器上传不再受
GitHub 的 CORS 限制，因为文件是 POST 到我们自己的 `api.cardputer.cc`，
它返回什么 CORS 头由我们说了算；与 GitHub 的所有交互都发生在 Worker
服务端（服务端调用不受 CORS 约束）。

```
浏览器 (cardputer.cc/submit.html)
   │ ① GET /auth/login → GitHub OAuth（仅公开身份，不要仓库权限）
   │ ② POST /api/submit（multipart：.deb + 源码仓库 URL）
   ▼
Cloudflare Worker (api.cardputer.cc)
   │ ③ 校验（大小 / ar 魔数 / 文件名）+ 计算 sha256
   │ ④ 用 BOT_TOKEN 把 .deb 存到 packages 仓库 web-upload-buffer Release
   │ ⑤ repository_dispatch "web-submission"（带 login/sha256/url/源码仓库）
   ▼
CardputerZero/packages Actions (process-web-submission.yml)
   │ ⑥ 完整校验：sha256、control 字段、.desktop、Maintainer 邮箱 == 提交者、
   │    版本必须高于线上；克隆源码仓库读取 app-builder.json store 段 + 截图
   │ ⑦ 通过 → 自动开发布 PR（@提交者）；失败 → 开 issue @提交者说明原因
   ▼
管理员审核合并 → update-index.yml 提升 .deb 进 apt-pool → APT 索引重建 → 上架
```

产物格式（`meta.json` + `*.deb.release.json` manifest + 截图）与 `czdev publish`
完全一致，两条通道并存，审核管线共用。

## 目录

| 路径 | 部署到哪 |
|------|----------|
| `worker/` | Cloudflare Workers（`wrangler deploy`） |
| `packages-workflow/process-web-submission.yml` | `CardputerZero/packages` 的 `.github/workflows/` |
| `site/submit.html` | `CardputerZero/cardputerzero.github.io` 根目录（或并入 hub SPA） |

## 部署步骤

### 1. 域名接入 Cloudflare（免费版即可）

1. Cloudflare 控制台 → Add site → `cardputer.cc`，按提示把域名的 NS 记录
   切到 Cloudflare（在注册商处修改）。
2. 站点主域：GitHub Pages 侧设置 custom domain 为 `cardputer.cc`
   （`cardputerzero.github.io` 仓库 → Settings → Pages → Custom domain，
   会自动提交 CNAME 文件），Cloudflare 里按 GitHub 文档加 A/AAAA 或 CNAME 记录。
   > 也可以继续用 `cardputerzero.github.io` 访问站点，只把 API 放在
   > `api.cardputer.cc`，则 `wrangler.toml` 的 `ALLOWED_ORIGIN` / `RETURN_URL`
   > 改成 Pages 域名即可。

### 2. 创建 GitHub OAuth App（用于网页登录）

GitHub → Settings → Developer settings → OAuth Apps → New：

- Homepage URL: `https://cardputer.cc`
- Authorization callback URL: `https://api.cardputer.cc/auth/callback`

记下 Client ID / Client Secret。

### 3. 准备 bot token

建议用机器人账号（或组织管理员）创建 fine-grained PAT：

- Repository access: 仅 `CardputerZero/packages`
- Permissions: **Contents: Read and write**（上传 Release 资产 + 触发 dispatch）

### 4. 部署 Worker

```bash
cd online-submit/worker
npm i -g wrangler          # 如未安装
wrangler login
wrangler secret put GITHUB_CLIENT_ID
wrangler secret put GITHUB_CLIENT_SECRET
wrangler secret put BOT_TOKEN
wrangler secret put SESSION_SECRET   # openssl rand -hex 32
wrangler deploy
```

`wrangler.toml` 已配置 `api.cardputer.cc` 自定义域（zone 在 Cloudflare 上时
自动签发证书并接管路由）。

### 5. 安装 packages 侧 workflow

把 `packages-workflow/process-web-submission.yml` 复制到
`CardputerZero/packages` 的 `.github/workflows/`，并在仓库建一个
`web-submission-failed` label（失败反馈 issue 用）。

### 6. 部署提交页

把 `site/submit.html` 放到 `cardputerzero.github.io` 仓库根目录。如站点没
挂 `cardputer.cc` 主域，把文件顶部 `const API` 保持指向
`https://api.cardputer.cc` 即可（Worker 的 `ALLOWED_ORIGIN` 填站点实际来源）。

## 安全设计

- **身份**：OAuth 空 scope，只读公开资料；Worker 用 HMAC 签名的 HttpOnly
  cookie 维持 24h 会话，不保存用户 token。
- **信任边界**：用户上传内容一律视为不可信——Worker 只做魔数/大小/文件名
  检查；语义校验全部在 packages 仓库 Actions 里用 `dpkg-deb` 完成（与
  `validate-pr.yml` 同一套规则），`.deb` 永不被执行。
- **身份绑定**：包的 `Maintainer` 邮箱必须等于提交者的
  `<login>@users.noreply.github.com` 或其 GitHub 公开邮箱，与 czdev/PR
  通道的规则一致，防止冒名顶替或覆盖他人包。
- **payload 传递**：workflow 中所有来自 dispatch payload 的值只经 env 传入
  shell，杜绝模板注入。
- **上限**：Worker 默认 64 MB（`MAX_SIZE_MB`），Cloudflare 免费版请求体上限
  100 MB。
- 可选加固：Cloudflare 侧对 `/api/submit` 配 rate limiting 规则防刷。

## 与 OSS 的关系

如果更希望文件落在阿里云 OSS（国内直传快）：OSS bucket 支持配置 CORS 允许
浏览器直传，但生成上传签名同样需要一个服务端（函数计算/STS），之后还要一跳
回调 GitHub API。整体链路比 Worker 方案多一层，且 packages 管线最终仍要从
URL 拉取 `.deb` 校验。因此推荐先用本方案（.deb 直接进 GitHub Release，与现有
apt-pool 分发/OSS 镜像同步逻辑无缝衔接）；将来如需国内上传加速，只需把
Worker 的第④步换成"签名直传 OSS + manifest url 指向 OSS"即可，其余不变。
