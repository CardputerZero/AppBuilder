# クイックスタート — CardputerZero AppStore にアプリを公開する

[English](QUICKSTART.md) | [中文](QUICKSTART_ZH.md)

純 Python の `czdev` CLI を使えば、数分で `.deb` パッケージを AppStore に公開
できます。Rust / cargo も、ローカルの ARM ツールチェインも不要です — ビルドは
CI 側で行われます。

## 1. 必要なもの

- **Python 3**
- **git**
- **dpkg-deb**（`dpkg` / `dpkg-dev` に含まれます）

```bash
# macOS
brew install dpkg
# Debian / Ubuntu
sudo apt install -y python3 git dpkg-dev
```

## 2. クローンしてログイン

```bash
git clone https://github.com/CardputerZero/CardputerZero-AppBuilder.git
cd CardputerZero-AppBuilder

./czdev --help      # Python 3 があればすぐ動きます
./czdev login       # GitHub デバイスフロー。トークンは ~/.czdev/credentials に保存
```

`czdev login` はコードと URL を表示します — URL を開いてコードを入力し、認可
してください。以降のコマンドはこのトークンを再利用します。

## 3. 新しいプロジェクトを作る（任意）

アプリがまだない場合は、[プロジェクトテンプレート](https://github.com/CardputerZero/Template)
（LVGL + CMake）から雛形を作成できます:

```bash
./czdev new my-app                 # → ./my-app、テンプレート main の最新状態をコピー
# または GitHub 側でコピーを作る:
gh repo create my-app --template CardputerZero/Template --public --clone
```

`czdev new` はテンプレートのプレースホルダーを自動で置き換えます: CMake の
プロジェクト名、バイナリに埋め込まれる `APP_NAME`、ランチャー表示名、そして
アイコンのファイル名です。アイコンは**共有ディレクトリ**
`/usr/share/APPLaunch/share/images/` にインストールされるため、どれも
`template.png` のままだとテンプレート由来のパッケージ同士が dpkg のファイル
衝突を起こします。

`NAME` は公開時のパッケージ名になるので、有効な Debian パッケージ名
（小文字で始まる）である必要があります。

## 4. `.deb` を入手する

ARM バイナリをローカルでビルドする必要はありません。入手方法は 2 つ:

- **オンラインビルド** — GitHub **Actions → Build DEB Package → Run workflow**
  で公開リポジトリの URL を貼り付け、生成された `.deb` アーティファクトを
  ダウンロード。
- **同梱サンプル** — 本リポジトリへの push で `examples/` 配下がすべてビルド
  されます。ビルド済みのものは `dist/` にあります。

CI が発見・ビルドできるよう、プロジェクトには `app-builder.json`
（[APP_BUILDER_JSON.md](APP_BUILDER_JSON.md) 参照）が必要です。

## 5. ストア情報として `store` セクションを追加

`czdev publish` は `app-builder.json` の `store` セクションから AppStore の
掲載情報を読み取ります。最低限、タイトルと 320×170 のスクリーンショットが
1 枚必要です:

```jsonc
{
  "package_name": "my_app",
  "app_name":     "My App",
  "bin_name":     "my_app",
  "version":      "1.0.1",

  "store": {
    "summary":     "一言での説明",
    "description": "詳細ページに表示される長めの説明。",
    "categories":  ["Games"],
    "screenshots": ["screenshots/main.png"],   // 320×170 の PNG
    "icon":        "packaging/icon.png"          // 任意
  }
}
```

## 6. bump と publish

アプリのプロジェクトディレクトリ（`app-builder.json` がある場所）で実行します:

```bash
# その .deb に対応する次のパッチバージョンを表示
./czdev bump    --deb build/my_app_1.0.0_arm64.deb

# 公開（.deb のバージョンは公開済みより新しい必要があります）
./czdev publish --deb build/my_app_1.0.1_arm64.deb
```

`publish` は事前チェック（`.desktop` の有無、バージョン更新、サイズ、そして
**root で動く systemd サービスがないこと**）を行い、`.deb` を GitHub Release に
アップロードし、`packages` リポジトリへメタデータのみの PR を作成します。管理者が
レビュー・マージすると、CI が APT インデックスを再生成し、アプリが公開されます。

`--deb` を省略すると、`czdev` は `./build/*.deb` を探します。

## 7. 公開停止（unpublish）

```bash
./czdev unpublish my_app --version 1.0.1
```

そのバージョンを削除する PR を作成します。

## 補足

- **所有権は GitHub ログインの先着順です。** あるパッケージ名を最初に公開した
  アカウントが所有し、以降はそのアカウント（またはリポジトリ管理者）のみが
  新バージョンの公開・公開停止を行えます。
- **アプリは root で実行してはいけません。** `.deb` が systemd サービスを同梱
  する場合は、非 root ユーザーに固定してください（`[Service]` に
  `User=<non-root>`）。さもないと公開は拒否されます。
- Web からの公開も可能です: **https://dev.cardputer.cc** に `.deb` をドラッグ
  し、ストア情報を入力して送信するだけです。
