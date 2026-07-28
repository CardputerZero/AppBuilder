# 快速上手 — 发布应用到 CardputerZero AppStore

[English](QUICKSTART.md) | [日本語](QUICKSTART_JA.md)

用纯 Python 的 `czdev` CLI，几分钟内把一个 `.deb` 包发布到 AppStore。不需要
Rust / cargo，也不需要本地 ARM 工具链——编译都在 CI 里完成。

## 1. 前置依赖

- **Python 3**
- **git**
- **dpkg-deb**（来自 `dpkg` / `dpkg-dev`）

```bash
# macOS
brew install dpkg
# Debian / Ubuntu
sudo apt install -y python3 git dpkg-dev
```

## 2. 克隆并登录

```bash
git clone https://github.com/CardputerZero/CardputerZero-AppBuilder.git
cd CardputerZero-AppBuilder

./czdev --help      # 有 Python 3 即可直接运行
./czdev login       # GitHub 设备码登录；token 存到 ~/.czdev/credentials
```

`czdev login` 会打印一个验证码和一个网址——打开网址、输入验证码并授权即可。
后续命令会复用这个 token。

## 3. 新建项目（可选）

如果还没有应用，可以从[项目模板](https://github.com/CardputerZero/Template)
（LVGL + CMake）脚手架一个：

```bash
./czdev new my-app                 # → ./my-app，取模板 main 分支的最新状态
# 或者让 GitHub 直接复制一份：
gh repo create my-app --template CardputerZero/Template --public --clone
```

`czdev new` 会顺带把模板里的占位符改成你的应用名：CMake 项目名、编译进二进制的
`APP_NAME`、启动器显示名，以及图标文件名。图标这一项很关键——模板的图标会装到
**共享目录** `/usr/share/APPLaunch/share/images/`，若都叫 `template.png`，两个基于
模板的包装到同一台设备上会产生 dpkg 文件冲突。

`NAME` 必须是合法的 Debian 包名（小写字母开头），因为它就是将来发布用的包名。

## 4. 拿到 `.deb`

你不需要在本地编译 ARM 二进制。两种拿包方式：

- **在线构建**——GitHub **Actions → Build DEB Package → Run workflow**，
  粘贴你的公开仓库 URL，下载生成的 `.deb` 制品。
- **内置示例**——推送到本仓库会构建 `examples/` 下所有应用；预构建的包在
  `dist/`。

你的项目里要有 `app-builder.json`（见
[APP_BUILDER_JSON.md](APP_BUILDER_JSON.md)），CI 才能发现并构建它。

## 5. 补上 `store` 段作为商店信息

`czdev publish` 会从 `app-builder.json` 的 `store` 段读取 AppStore 展示信息。
至少需要一个标题和一张 320×170 的截图：

```jsonc
{
  "package_name": "my_app",
  "app_name":     "My App",
  "bin_name":     "my_app",
  "version":      "1.0.1",

  "store": {
    "summary":     "一句话简介",
    "description": "详情页展示的较长描述。",
    "categories":  ["Games"],
    "screenshots": ["screenshots/main.png"],   // 320×170 的 PNG
    "icon":        "packaging/icon.png"          // 可选
  }
}
```

## 6. bump 与 publish

在你应用的项目目录（含 `app-builder.json` 的那个）里运行：

```bash
# 查看该 .deb 对应的下一个补丁版本号
./czdev bump    --deb build/my_app_1.0.0_arm64.deb

# 发布（.deb 里的版本必须高于线上已发布的版本）
./czdev publish --deb build/my_app_1.0.1_arm64.deb
```

`publish` 会做发布前检查（存在 `.desktop`、版本已提升、体积、以及
**没有以 root 运行的 systemd 服务**），把 `.deb` 上传到 GitHub Release，并向
`packages` 仓库发一个只含元数据的 PR。管理员审核合并后，CI 重建 APT 索引，
你的应用就上线了。

不带 `--deb` 时，`czdev` 会在 `./build/*.deb` 里查找。

## 7. 下架

```bash
./czdev unpublish my_app --version 1.0.1
```

这会发一个移除该版本的 PR。

## 说明

- **归属按 GitHub 账号先到先得。** 谁先发布某个包名，就归属于其账号；之后只有
  该账号（或仓库管理员）能发布新版本或下架。
- **应用不允许以 root 运行。** 如果你的 `.deb` 带 systemd 服务，请把它固定到
  非 root 用户（`[Service]` 段里写 `User=<non-root>`），否则发布会被拒。
- 你也可以在网页端 **https://dev.cardputer.cc** 发布——拖入 `.deb`、填写商店
  信息、提交即可。
