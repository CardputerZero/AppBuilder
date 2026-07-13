# 在线提交方案：cardputerzero.github.io 网页端发布 .deb（issue-ops）

> 状态：设计定稿，代码就绪。落地需要对 `CardputerZero/packages` 与
> `CardputerZero/cardputerzero.github.io` 两个仓库的写权限（见文末）。

## 背景

当前开发者发布应用到 AppStore 只能走本地 `czdev` CLI：

1. `./czdev login`（GitHub 设备流 OAuth）
2. `./czdev publish --deb xxx.deb` — 把 `.deb` 上传到开发者自己 fork 的
   `czdev-buffer` Release，再向 `CardputerZero/packages` 提一个只含
   `meta.json` + `*.deb.release.json` manifest + 截图的 PR
3. 管理员审核合并后，`packages` 仓库 CI 把 `.deb` 提升进 `apt-pool`
   Release 并重建 APT 索引，Hub 同步 registry

痛点：要求本地有 Python 3、git、dpkg-deb，且流程对非 CLI 用户不友好。

## 为什么纯静态网页做不到"直接复刻 czdev"

`cardputerzero.github.io` 是 GitHub Pages 纯静态站，没有后端。实测（2026-07）：

| 端点 | 用途 | 浏览器可用性 |
|------|------|--------------|
| `api.github.com` | fork、建分支、提 PR、Git Data API | ✅ `Access-Control-Allow-Origin: *` |
| `uploads.github.com` | 上传 Release 资产（.deb 就靠它） | ❌ 无任何 CORS 头 |
| `github.com/login/device/code` 与 `.../oauth/access_token` | OAuth 设备流 / 令牌交换 | ❌ 无 CORS，且 web flow 换 token 需要 `client_secret` |

即：**浏览器里既拿不到用户 token（除非让用户手贴 PAT），也传不了
Release 资产**。要么加后端/CORS 代理（引入运维与安全成本），要么换思路。

## 推荐方案：issue-ops（零后端、零 CORS、GitHub 原生登录）

把"提交"变成"在 `CardputerZero/packages` 开一个结构化 Issue"：

```
开发者                cardputerzero.github.io          CardputerZero/packages
  │                          │                                  │
  │ 1. 打开"在线提交"页面     │                                  │
  │ 2. 填表单（deb URL、     │                                  │
  │    sha256、源码仓库）    │                                  │
  │ ────────────────────────▶ 3. 生成预填的 issue URL           │
  │ 4. 跳转 GitHub（自动要求登录）──────────────────────────────▶│
  │ 5. 确认并提交 Issue                                          │
  │                                                              │ 6. Action 触发：
  │                                                              │    下载 .deb → 校验
  │                                                              │    （复用 validate-pr 的
  │                                                              │    全部检查 + 作者身份）
  │                                                              │    克隆源码仓库读取
  │                                                              │    app-builder.json store
  │                                                              │    → 建分支、提 PR
  │ ◀──────────────── 7. Issue 下自动回帖：校验结果 / PR 链接 ────│
  │                                                              │ 8. 管理员审核合并
  │                                                              │    → apt 索引重建、上架
```

关键点：

- **登录交给 GitHub**：开 Issue 必须登录 GitHub，天然拿到提交者身份，
  Action 里用 issue author 做 Maintainer 邮箱比对（与现有 PR 校验同一套规则）。
- **`.deb` 不经过网页**：开发者把 `.deb` 挂在自己仓库的 Release 上
  （网页上传 Release 资产 GitHub 官网支持，无需 CLI），表单里只填 URL + sha256。
  manifest 的 `url` 直接指向开发者的 Release，合并时 `update-index.yml`
  照常校验 sha256 并提升进 `apt-pool`，与 czdev 流程完全一致。
- **元数据来源一致**：Action 克隆开发者源码仓库，读取与 czdev 相同的
  `app-builder.json` `store` 段与截图，保证两条通道产物格式相同。

## 需要落地的改动

| 仓库 | 改动 |
|------|------|
| `CardputerZero/packages` | 新增 `.github/ISSUE_TEMPLATE/submit-package.yml`（Issue 表单）与 `.github/workflows/process-submission.yml`（提交处理器） |
| `CardputerZero/cardputerzero.github.io` | 新增"在线提交"页面（静态表单 → 预填 issue URL），提交文档加入口 |
| 本仓库 | 无（czdev CLI 照常可用，两条通道并存） |

### 1. Issue 表单（`packages` 仓库 `.github/ISSUE_TEMPLATE/submit-package.yml`）

```yaml
name: Submit App Package
description: Submit a .deb package to the CardputerZero AppStore (online flow)
title: "[submit] <package> <version>"
labels: [package-submission]
body:
  - type: markdown
    attributes:
      value: |
        Upload your `.deb` as a **Release asset on your own repository** first,
        then fill in the fields below. Validation results will be posted as a
        comment on this issue.
  - type: input
    id: deb-url
    attributes:
      label: .deb download URL
      description: Direct download URL of the .deb (e.g. your repo's Release asset)
      placeholder: https://github.com/<you>/<app>/releases/download/v1.0.0/myapp_1.0.0_arm64.deb
    validations:
      required: true
  - type: input
    id: sha256
    attributes:
      label: SHA-256
      description: "Output of: sha256sum myapp_1.0.0_arm64.deb"
    validations:
      required: true
  - type: input
    id: source-repo
    attributes:
      label: Source repository
      description: Public git URL containing app-builder.json (with a "store" section and screenshots)
      placeholder: https://github.com/<you>/<app>
    validations:
      required: true
  - type: textarea
    id: notes
    attributes:
      label: Notes for reviewers (optional)
```

### 2. 提交处理 workflow（`packages` 仓库 `.github/workflows/process-submission.yml`）

```yaml
name: Process Package Submission

on:
  issues:
    types: [opened, edited]

permissions:
  contents: write
  issues: write
  pull-requests: write

jobs:
  process:
    if: contains(github.event.issue.labels.*.name, 'package-submission')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install tools
        run: sudo apt-get update && sudo apt-get install -y dpkg-dev jq

      - name: Parse issue form
        id: parse
        env:
          # Untrusted input: only ever consumed via env, never interpolated
          # into shell text.
          ISSUE_BODY: ${{ github.event.issue.body }}
        run: |
          python3 - <<'EOF'
          import os, re, json
          body = os.environ["ISSUE_BODY"]
          def field(label):
              m = re.search(rf"### {re.escape(label)}\s*\n+(.*?)(?=\n### |\Z)", body, re.S)
              return (m.group(1).strip() if m else "").splitlines()[0].strip() if m else ""
          out = {
              "deb_url": field(".deb download URL"),
              "sha256": field("SHA-256").lower(),
              "source_repo": field("Source repository"),
          }
          with open(os.environ["GITHUB_OUTPUT"], "a") as f:
              for k, v in out.items():
                  f.write(f"{k}={v}\n")
          EOF

      - name: Download and validate .deb
        id: validate
        env:
          DEB_URL: ${{ steps.parse.outputs.deb_url }}
          WANT_SHA: ${{ steps.parse.outputs.sha256 }}
          ISSUE_AUTHOR: ${{ github.event.issue.user.login }}
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          set -euo pipefail
          ERR=""
          case "$DEB_URL" in
            https://github.com/*/releases/download/*) ;;
            *) ERR="- ❌ .deb URL must be a GitHub Release asset URL\n" ;;
          esac
          curl -fL --retry 3 --max-time 600 -o /tmp/pkg.deb "$DEB_URL" || ERR="${ERR}- ❌ download failed\n"
          if [ -f /tmp/pkg.deb ]; then
            GOT=$(sha256sum /tmp/pkg.deb | awk '{print $1}')
            [ "$GOT" = "$WANT_SHA" ] || ERR="${ERR}- ❌ sha256 mismatch (got \`$GOT\`)\n"
            PKG=$(dpkg-deb -f /tmp/pkg.deb Package || true)
            VER=$(dpkg-deb -f /tmp/pkg.deb Version || true)
            ARCH=$(dpkg-deb -f /tmp/pkg.deb Architecture || true)
            MAINT=$(dpkg-deb -f /tmp/pkg.deb Maintainer || true)
            [ -n "$PKG" ] || ERR="${ERR}- ❌ not a valid .deb\n"
            echo "$PKG" | grep -qP '^[a-z0-9][a-z0-9.+\-]+$' || ERR="${ERR}- ❌ invalid package name\n"
            dpkg-deb -c /tmp/pkg.deb > /tmp/contents.txt || true
            grep -q '\.desktop$' /tmp/contents.txt || ERR="${ERR}- ❌ missing .desktop file\n"
            # Maintainer email must match the issue author (noreply or public email).
            EMAIL=$(echo "$MAINT" | grep -oP '<\K[^>]+' || echo "$MAINT")
            NOREPLY="${ISSUE_AUTHOR}@users.noreply.github.com"
            PUB=$(gh api "users/$ISSUE_AUTHOR" --jq '.email // empty' || true)
            if [ "$EMAIL" != "$NOREPLY" ] && [ "$EMAIL" != "$PUB" ]; then
              ERR="${ERR}- ❌ Maintainer \`$EMAIL\` does not match issue author\n"
            fi
            # Version must be newer than the latest manifest on main.
            EXISTING=""
            for m in pool/main/"$PKG"/*.deb.release.json; do
              [ -f "$m" ] || continue
              v=$(jq -r '.version // empty' "$m")
              if [ -n "$v" ] && { [ -z "$EXISTING" ] || dpkg --compare-versions "$v" gt "$EXISTING"; }; then EXISTING="$v"; fi
            done
            if [ -n "$EXISTING" ] && dpkg --compare-versions "$VER" le "$EXISTING"; then
              ERR="${ERR}- ❌ version $VER is not newer than $EXISTING\n"
            fi
            { echo "pkg=$PKG"; echo "ver=$VER"; echo "arch=$ARCH"; } >> "$GITHUB_OUTPUT"
          fi
          printf 'errors<<EOM\n%b\nEOM\n' "$ERR" >> "$GITHUB_OUTPUT"

      - name: Collect store metadata from source repo
        if: steps.validate.outputs.errors == ''
        env:
          SOURCE_REPO: ${{ steps.parse.outputs.source_repo }}
          PKG: ${{ steps.validate.outputs.pkg }}
        run: |
          set -euo pipefail
          git clone --depth=1 "$SOURCE_REPO" /tmp/src
          # Find the app-builder.json whose package_name matches; copy the
          # store section + screenshots + icon the same way czdev does.
          python3 - <<'EOF'
          import json, os, shutil, sys
          from pathlib import Path
          pkg = os.environ["PKG"]
          for mf in Path("/tmp/src").rglob("app-builder.json"):
              raw = json.loads(mf.read_text())
              if raw.get("package_name") != pkg or "store" not in raw:
                  continue
              store, app_dir = raw["store"], mf.parent
              dest = Path(f"pool/main/{pkg}")
              dest.mkdir(parents=True, exist_ok=True)
              meta = {"title": raw.get("app_name", ""),
                      "summary": store.get("summary", ""),
                      "categories": store.get("categories", []),
                      "screenshots": store.get("screenshots", [])}
              for k in ("description", "locales", "license", "source_repo", "icon", "permissions"):
                  if store.get(k):
                      meta[k] = store[k]
              if not meta["screenshots"]:
                  sys.exit("no screenshots in app-builder.json store section")
              shots = dest / "screenshots"
              shots.mkdir(exist_ok=True)
              for s in meta["screenshots"]:
                  shutil.copy2(app_dir / s, shots / Path(s).name)
              if store.get("icon"):
                  shutil.copy2(app_dir / store["icon"], dest / Path(store["icon"]).name)
              (dest / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
              sys.exit(0)
          sys.exit(f"no app-builder.json with package_name={pkg} and a store section found")
          EOF

      - name: Create publish PR
        if: steps.validate.outputs.errors == ''
        id: pr
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          DEB_URL: ${{ steps.parse.outputs.deb_url }}
          WANT_SHA: ${{ steps.parse.outputs.sha256 }}
          PKG: ${{ steps.validate.outputs.pkg }}
          VER: ${{ steps.validate.outputs.ver }}
          ARCH: ${{ steps.validate.outputs.arch }}
          ISSUE_NUM: ${{ github.event.issue.number }}
          ISSUE_AUTHOR: ${{ github.event.issue.user.login }}
        run: |
          set -euo pipefail
          NAME="${PKG}_${VER}_${ARCH}.deb"
          SIZE=$(stat -c%s /tmp/pkg.deb)
          jq -n --arg f "${NAME//\~/.}" --arg u "$DEB_URL" --arg s "$WANT_SHA" \
                --arg p "$PKG" --arg v "$VER" --arg a "$ARCH" --argjson z "$SIZE" \
                '{filename:$f,url:$u,sha256:$s,size:$z,package:$p,version:$v,architecture:$a}' \
                > "pool/main/$PKG/${NAME//\~/.}.release.json"
          BRANCH="publish/${PKG}-${VER}-$(date +%s)"
          git config user.name "cardputerzero-bot"
          git config user.email "bot@users.noreply.github.com"
          git checkout -b "$BRANCH"
          git add "pool/main/$PKG"
          git commit -m "publish: $PKG $VER ($ARCH) via online submission #$ISSUE_NUM"
          git push origin "$BRANCH"
          gh pr create --base main --head "$BRANCH" \
            --title "publish: $PKG $VER (online submission by @$ISSUE_AUTHOR)" \
            --body "Submitted online via #$ISSUE_NUM by @$ISSUE_AUTHOR. Validation ran in [this workflow](${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }})." \
            | tail -1 > /tmp/pr-url.txt
          echo "url=$(cat /tmp/pr-url.txt)" >> "$GITHUB_OUTPUT"

      - name: Report result on the issue
        if: always()
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ERRORS: ${{ steps.validate.outputs.errors }}
          PR_URL: ${{ steps.pr.outputs.url }}
        run: |
          if [ -n "$ERRORS" ]; then
            printf '## ❌ Submission validation failed\n\n%b\nFix the issues and edit this issue to retry.\n' "$ERRORS" > /tmp/c.md
          elif [ -n "$PR_URL" ]; then
            printf '## ✅ Submission accepted\n\nPublish PR: %s\nA maintainer will review and merge it.\n' "$PR_URL" > /tmp/c.md
          else
            echo '## ❌ Submission failed — see the workflow log.' > /tmp/c.md
          fi
          gh issue comment "${{ github.event.issue.number }}" --body-file /tmp/c.md
```

注意事项：

- 该 workflow 用仓库自身 `GITHUB_TOKEN` 开的 PR **不会**再触发
  `validate-pr.yml`（GitHub 限制），且 `validate-pr.yml` 对 same-repo PR 本就跳过
  第三方校验 —— 因此上面的处理器自身完成了同等校验。若希望发布 PR 仍跑
  `validate-pr.yml`，改用一个 bot 账户 PAT（`secrets.BOT_TOKEN`）推分支/开 PR，
  并让 `validate-pr.yml` 对 `publish/*` 分支的 same-repo PR 也执行完整校验。
- Issue 正文是不可信输入：全部经 `env` 传递，杜绝把 issue 内容直接插进
  shell 模板；`.deb` 只用 `dpkg-deb` 解析、绝不执行；源码仓库 `--depth=1`
  克隆后只读取 `app-builder.json` 与图片文件。
- 可选增强：提交成功后给 Issue 加 `accepted` label 并关闭；PR 合并后由
  `update-index.yml` 回帖"已上架"。

### 3. 网站提交页（`cardputerzero.github.io`）

在 Hub 增加 `#/submit` 路由（或独立 `submit.html`），表单只做一件事 ——
拼预填 Issue URL（Issue Forms 支持用字段 `id` 作为 query 参数预填）：

```js
function buildSubmitUrl({ debUrl, sha256, sourceRepo, pkg, version }) {
  const params = new URLSearchParams({
    template: "submit-package.yml",
    title: `[submit] ${pkg} ${version}`,
    "deb-url": debUrl,
    "sha256": sha256,
    "source-repo": sourceRepo,
  });
  return `https://github.com/CardputerZero/packages/issues/new?${params}`;
}
```

页面同时给出三步引导：① 用 AppBuilder 在线 CI 或本地构建 `.deb`；
② 在自己仓库的 Release 页上传 `.deb`（网页操作即可）并复制下载链接与
sha256；③ 填表单跳转 GitHub 确认提交。全程无需安装任何工具。

## 与 czdev CLI 的关系

两条通道并存、产物格式一致（`meta.json` + `*.deb.release.json` + 截图，
同一套审核与上架管线）。CLI 适合重度开发者（一条命令），网页适合轻量
用户与只在浏览器工作的场景。

## 落地所需权限

当前 Cursor agent 的 GitHub App 安装仅覆盖 `m5stack/cardputerzero-appbuilder`，
对以下仓库均无写权限（已实测 push 被拒）：

- `CardputerZero/packages` — 需要放置 Issue 表单与提交处理 workflow
- `CardputerZero/cardputerzero.github.io` — 需要新增提交页面

在 GitHub 的 Cursor App 安装设置（组织 `CardputerZero` → Settings →
GitHub Apps → Cursor → Repository access）中把这两个仓库加进去即可，
之后 agent 可以直接按本文档开分支提 PR。
