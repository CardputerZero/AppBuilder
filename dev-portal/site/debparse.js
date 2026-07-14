/**
 * Browser-side .deb inspector for the CardputerZero developer portal.
 *
 * Parses a Debian package entirely in the browser (no upload needed):
 *   ar archive -> control.tar.{gz,xz,zst} + data.tar.{gz,xz,zst}
 * and produces control fields, the .desktop entry, the app icon bytes,
 * a full file listing and a preliminary safety report.
 *
 * Decompressors are injected so the same module works in the page (CDN
 * libs) and in Node tests (npm libs):
 *   parseDeb(buffer, { gzip, xz, zstd })  — each: (Uint8Array) => Promise<Uint8Array>
 */

/* ------------------------------ ar archive ------------------------------- */

function parseAr(bytes) {
  const dec = new TextDecoder();
  if (dec.decode(bytes.subarray(0, 8)) !== "!<arch>\n") {
    throw new Error("不是有效的 .deb 文件（缺少 ar 魔数）");
  }
  const entries = [];
  let off = 8;
  while (off + 60 <= bytes.length) {
    const name = dec.decode(bytes.subarray(off, off + 16)).trim().replace(/\/$/, "");
    const size = parseInt(dec.decode(bytes.subarray(off + 48, off + 58)).trim(), 10);
    if (!Number.isFinite(size)) break;
    const start = off + 60;
    entries.push({ name, data: bytes.subarray(start, start + size) });
    off = start + size + (size % 2); // entries are 2-byte aligned
  }
  return entries;
}

/* --------------------------------- tar ----------------------------------- */

const TYPE_NAMES = {
  "0": "file", "\0": "file", "1": "hardlink", "2": "symlink",
  "3": "chardev", "4": "blockdev", "5": "dir", "6": "fifo",
};

function parseTar(bytes) {
  const dec = new TextDecoder();
  const files = [];
  let off = 0;
  let longName = null;
  while (off + 512 <= bytes.length) {
    const block = bytes.subarray(off, off + 512);
    if (block.every((b) => b === 0)) break;
    const rawName = dec.decode(block.subarray(0, 100)).split("\0")[0];
    const mode = parseInt(dec.decode(block.subarray(100, 108)).trim() || "0", 8);
    const size = parseInt(dec.decode(block.subarray(124, 136)).trim() || "0", 8);
    const typeflag = dec.decode(block.subarray(156, 157));
    const linkname = dec.decode(block.subarray(157, 257)).split("\0")[0];
    const prefix = dec.decode(block.subarray(345, 500)).split("\0")[0];
    const dataStart = off + 512;
    const data = bytes.subarray(dataStart, dataStart + size);
    off = dataStart + Math.ceil(size / 512) * 512;

    if (typeflag === "L") { // GNU long name: payload is the real name
      longName = dec.decode(data).split("\0")[0];
      continue;
    }
    let path = longName || (prefix ? `${prefix}/${rawName}` : rawName);
    longName = null;
    path = path.replace(/^\.\//, "").replace(/\/+$/, "");
    if (!path) continue;
    files.push({
      path,
      mode,
      size,
      type: TYPE_NAMES[typeflag] || `type-${typeflag}`,
      linkname: linkname || null,
      data,
    });
  }
  return files;
}

/* ----------------------------- decompression ----------------------------- */

async function extractMember(entries, base, decompressors) {
  for (const [suffix, kind] of [
    [".tar.gz", "gzip"], [".tar.xz", "xz"], [".tar.zst", "zstd"], [".tar", "raw"],
  ]) {
    const entry = entries.find((e) => e.name === base + suffix);
    if (!entry) continue;
    if (kind === "raw") return parseTar(entry.data);
    const fn = decompressors[kind];
    if (!fn) throw new Error(`不支持的压缩格式: ${base}${suffix}`);
    return parseTar(await fn(entry.data));
  }
  throw new Error(`.deb 中缺少 ${base}.tar.* 成员`);
}

/* ------------------------------ field parsing ---------------------------- */

function parseControlFile(text) {
  const fields = {};
  let last = null;
  for (const line of text.split("\n")) {
    if (/^\s/.test(line) && last) {
      fields[last] += "\n" + line.trim();
    } else {
      const idx = line.indexOf(":");
      if (idx > 0) {
        last = line.slice(0, idx).trim();
        fields[last] = line.slice(idx + 1).trim();
      }
    }
  }
  return fields;
}

function parseDesktop(text) {
  const fields = {};
  let inEntry = false;
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (t.startsWith("[")) { inEntry = t === "[Desktop Entry]"; continue; }
    if (!inEntry) continue;
    const idx = t.indexOf("=");
    if (idx > 0) fields[t.slice(0, idx).trim()] = t.slice(idx + 1).trim();
  }
  return fields;
}

export function extractEmail(maintainer) {
  const m = /<([^>]+)>/.exec(maintainer || "");
  return m ? m[1].trim() : (maintainer || "").trim();
}

/* --------------------------- dpkg version compare ------------------------- */

function chOrder(c) {
  if (c === "~") return -1;
  if (c >= "0" && c <= "9") return 0;
  if (/[A-Za-z]/.test(c)) return c.charCodeAt(0);
  return c.charCodeAt(0) + 256;
}

function verrevcmp(a, b) {
  let ia = 0, ib = 0;
  const isDigit = (c) => c >= "0" && c <= "9";
  while (ia < a.length || ib < b.length) {
    let firstDiff = 0;
    while ((ia < a.length && !isDigit(a[ia])) || (ib < b.length && !isDigit(b[ib]))) {
      const ac = ia < a.length ? chOrder(a[ia]) : 0;
      const bc = ib < b.length ? chOrder(b[ib]) : 0;
      if (ac !== bc) return ac - bc;
      ia++; ib++;
    }
    while (a[ia] === "0") ia++;
    while (b[ib] === "0") ib++;
    while (ia < a.length && isDigit(a[ia]) && ib < b.length && isDigit(b[ib])) {
      if (!firstDiff) firstDiff = a.charCodeAt(ia) - b.charCodeAt(ib);
      ia++; ib++;
    }
    if (ia < a.length && isDigit(a[ia])) return 1;
    if (ib < b.length && isDigit(b[ib])) return -1;
    if (firstDiff) return firstDiff;
  }
  return 0;
}

/** dpkg --compare-versions 语义：返回 -1 / 0 / 1 */
export function compareDebVersions(va, vb) {
  const split = (v) => {
    v = v.trim();
    let epoch = 0;
    const ci = v.indexOf(":");
    if (ci > 0 && /^\d+$/.test(v.slice(0, ci))) { epoch = parseInt(v.slice(0, ci), 10); v = v.slice(ci + 1); }
    const di = v.lastIndexOf("-");
    return di >= 0 ? [epoch, v.slice(0, di), v.slice(di + 1)] : [epoch, v, ""];
  };
  const [ea, ua, ra] = split(va);
  const [eb, ub, rb] = split(vb);
  if (ea !== eb) return ea > eb ? 1 : -1;
  const rc = verrevcmp(ua, ub) || verrevcmp(ra, rb);
  return rc > 0 ? 1 : rc < 0 ? -1 : 0;
}

/* ------------------------------ safety report ---------------------------- */

function allowedPrefixes(pkgName) {
  return [
    "usr/share/APPLaunch/",
    "lib/systemd/system/",
    "usr/share/doc/",
    ...(pkgName ? [`usr/share/${pkgName}/`, `usr/lib/${pkgName}/`, `opt/${pkgName}/`] : []),
  ];
}

const SCRIPT_PATTERNS = [
  [/rm\s+(-\w+\s+)*(\/|\$\{?HOME)/, "danger", "maintainer 脚本包含对根目录/家目录的删除操作"],
  [/(curl|wget)[^\n]*\|\s*(ba)?sh/, "danger", "maintainer 脚本从网络下载并直接执行代码"],
  [/base64\s+(-d|--decode)/, "warn", "maintainer 脚本包含 base64 解码（可能隐藏内容）"],
  [/\b(nc|ncat|netcat)\b/, "warn", "maintainer 脚本调用 netcat"],
  [/dd\s+[^\n]*of=\/dev\//, "danger", "maintainer 脚本直接写入设备文件"],
  [/mkfs|fdisk|parted/, "danger", "maintainer 脚本包含磁盘分区/格式化命令"],
  [/\/etc\/(passwd|shadow|sudoers)/, "danger", "maintainer 脚本操作系统认证文件"],
  [/chmod\s+[0-7]*[4267][0-7]{2,}\s/, "warn", "maintainer 脚本修改敏感权限位"],
  [/crontab|\/etc\/cron/, "warn", "maintainer 脚本安装计划任务"],
  [/systemctl\s+(enable|start)/, "info", "maintainer 脚本启用 systemd 服务（APPLaunch 应用常见）"],
];

function analyzeFiles(dataFiles, report, pkgName) {
  const prefixes = allowedPrefixes(pkgName);
  let elfCount = 0, wrongArchElf = 0, totalSize = 0;
  for (const f of dataFiles) {
    totalSize += f.size;
    if (f.path.includes("..")) {
      report.push(["danger", `路径穿越: ${f.path}`]);
    }
    if (f.type === "chardev" || f.type === "blockdev") {
      report.push(["danger", `包含设备文件: ${f.path}`]);
    }
    if (f.type === "file") {
      if (f.mode & 0o4000) report.push(["danger", `setuid 可执行文件: ${f.path}`]);
      if (f.mode & 0o2000) report.push(["warn", `setgid 文件: ${f.path}`]);
      if (f.mode & 0o002) report.push(["warn", `全局可写文件: ${f.path}`]);
      if (f.size > 50 * 1024 * 1024) {
        report.push(["warn", `超大文件 (${(f.size / 1048576).toFixed(0)} MB): ${f.path}`]);
      }
      if (f.size >= 20 && f.data[0] === 0x7f && f.data[1] === 0x45 && f.data[2] === 0x4c && f.data[3] === 0x46) {
        elfCount++;
        const machine = f.data[18] | (f.data[19] << 8);
        if (machine !== 183) { // EM_AARCH64
          wrongArchElf++;
          report.push(["warn", `非 arm64 的 ELF 二进制 (e_machine=${machine}): ${f.path}`]);
        }
      }
    }
    if (f.type === "symlink" && f.linkname && f.linkname.startsWith("/") &&
        !f.linkname.startsWith("/usr/share/APPLaunch")) {
      report.push(["warn", `符号链接指向包外绝对路径: ${f.path} -> ${f.linkname}`]);
    }
    if (f.type !== "dir") {
      const ok = prefixes.some((p) => f.path.startsWith(p));
      if (!ok) report.push(["warn", `非常规安装路径: ${f.path}`]);
    }
  }
  return { elfCount, wrongArchElf, totalSize };
}

/* --------------------------------- main ---------------------------------- */

/**
 * @param {ArrayBuffer|Uint8Array} buffer  the .deb bytes
 * @param {{gzip:Function, xz:Function, zstd:Function}} decompressors
 * @returns {Promise<object>} 解析与检查结果
 */
export async function parseDeb(buffer, decompressors) {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  const report = []; // [level, message]; level: pass | info | warn | danger

  const entries = parseAr(bytes);
  const controlFiles = await extractMember(entries, "control", decompressors);
  const dataFiles = await extractMember(entries, "data", decompressors);
  const dec = new TextDecoder();

  // control fields
  const controlEntry = controlFiles.find((f) => f.path === "control");
  if (!controlEntry) throw new Error(".deb 中缺少 control 文件");
  const control = parseControlFile(dec.decode(controlEntry.data));
  for (const field of ["Package", "Version", "Architecture", "Maintainer"]) {
    if (!control[field]) report.push(["danger", `control 缺少 ${field} 字段`]);
  }
  if (control.Package && !/^[a-z0-9][a-z0-9.+-]+$/.test(control.Package)) {
    report.push(["danger", `包名不合法: ${control.Package}`]);
  }
  if (control.Architecture && !["arm64", "all"].includes(control.Architecture)) {
    report.push(["danger", `架构必须是 arm64 或 all，当前: ${control.Architecture}`]);
  }

  // maintainer scripts
  const scripts = {};
  for (const name of ["preinst", "postinst", "prerm", "postrm"]) {
    const f = controlFiles.find((e) => e.path === name);
    if (!f) continue;
    const text = dec.decode(f.data);
    scripts[name] = text;
    for (const [re, level, msg] of SCRIPT_PATTERNS) {
      if (re.test(text)) report.push([level, `${msg}（${name}）`]);
    }
  }

  // .desktop
  const desktopFile = dataFiles.find(
    (f) => f.type === "file" && /^usr\/share\/APPLaunch\/applications\/[^/]+\.desktop$/.test(f.path),
  );
  let desktop = null;
  if (desktopFile) {
    desktop = { path: desktopFile.path, ...parseDesktop(dec.decode(desktopFile.data)) };
    report.push(["pass", `.desktop: ${desktopFile.path}`]);
    if (!desktop.Name) report.push(["warn", ".desktop 缺少 Name"]);
    if (!desktop.Exec) report.push(["danger", ".desktop 缺少 Exec"]);
  } else {
    report.push(["danger", "缺少 usr/share/APPLaunch/applications/*.desktop（商店应用必须提供）"]);
  }

  // icon
  let icon = null;
  if (desktop && desktop.Icon) {
    const rel = desktop.Icon.replace(/^\//, "");
    const candidates = [rel, `usr/share/APPLaunch/${rel}`];
    const f = dataFiles.find((e) => e.type === "file" && candidates.includes(e.path));
    if (f) {
      const isPng = f.data.length > 8 && f.data[0] === 0x89 && f.data[1] === 0x50;
      icon = { path: f.path, bytes: f.data.slice(), isPng };
      report.push([isPng ? "pass" : "warn", isPng ? `图标: ${f.path}` : `图标不是 PNG: ${f.path}`]);
    } else {
      report.push(["warn", `.desktop 声明的图标未打进包里: ${desktop.Icon}`]);
    }
  }

  const stats = analyzeFiles(dataFiles, report, control.Package);
  if (stats.elfCount > 0) {
    report.push(["info", `包含 ${stats.elfCount} 个 ELF 二进制${stats.wrongArchElf ? `（其中 ${stats.wrongArchElf} 个架构可疑）` : "（架构均为 arm64）"}`]);
  }
  if (Object.keys(scripts).length) {
    report.push(["info", `包含 maintainer 脚本: ${Object.keys(scripts).join(", ")}`]);
  }

  const danger = report.filter(([l]) => l === "danger").length;
  const warn = report.filter(([l]) => l === "warn").length;

  return {
    control,
    email: extractEmail(control.Maintainer),
    desktop,
    icon,
    scripts,
    files: dataFiles.map(({ path, size, mode, type, linkname }) => ({ path, size, mode, type, linkname })),
    totalInstalledSize: stats.totalSize,
    report,
    verdict: danger ? "danger" : warn ? "warn" : "pass",
  };
}
