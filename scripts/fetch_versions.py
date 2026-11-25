#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minecraft 版本监测与拆包脚本（供 GitHub Actions 定期运行，也可本地手动执行）

职责：
1. 拉取 Mojang 官方公开版本清单 (version_manifest_v2)，全量列出所有版本元数据；
2. 识别"新版本"（正式版 / 快照 / 愚人节快照）；
3. 对需要追踪的版本下载 client jar 并"拆包"——仅记录内部文件清单
   （路径、大小、CRC32、时间戳），绝不转发/存储游戏本体文件，遵循合理使用原则。

产出：
  data/index.json           全部版本元数据（列表页数据源）
  data/versions/<id>.json.gz  单个版本的 jar 内文件清单（gzip 压缩）
  data/last_run.json        本次运行结果（新版本列表，用于提交信息）
  data/state.json           状态文件（已知版本、已拆包版本）

环境变量：
  TRACK_RELEASES   追踪最近 N 个正式版（默认 99999 = 全部）
  TRACK_SNAPSHOTS  追踪最近 N 个快照（默认 99999 = 全部）
  BACKFILL_VERSION 手动补拆的版本 ID（可逗号分隔多个）
  EXTRACT_CONTENTS 是否提取可读文本内容（默认 1）
  CONTENTS_ALL     1 时对所有处理的版本提取内容（默认仅新版本/最新版/愚人节版）
  FORCE_CONTENTS   1 时为"已拆包但缺内容"的版本补充内容（需重新下载 jar）
  FORCE_REPACK     1 时强制重新下载 BACKFILL_VERSION 中的版本（重新生成清单+内容）
"""

import gzip
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import zipfile
from io import BytesIO

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
VERSIONS_DIR = os.path.join(DATA_DIR, "versions")
CONTENTS_DIR = os.path.join(DATA_DIR, "contents")

TRACK_RELEASES = int(os.environ.get("TRACK_RELEASES", "99999"))
TRACK_SNAPSHOTS = int(os.environ.get("TRACK_SNAPSHOTS", "99999"))
BACKFILL = [v.strip() for v in os.environ.get("BACKFILL_VERSION", "").split(",") if v.strip()]
EXTRACT_CONTENTS = os.environ.get("EXTRACT_CONTENTS", "1") != "0"
CONTENTS_ALL = os.environ.get("CONTENTS_ALL", "0") == "1"
FORCE_CONTENTS = os.environ.get("FORCE_CONTENTS", "0") == "1"
FORCE_REPACK = os.environ.get("FORCE_REPACK", "0") == "1"

# 可读文本类扩展名：这些文件的内容会被提取供页面查看（遵循合理使用，仅限小型文本）
TEXT_EXTS = {
    ".json", ".mcmeta", ".txt", ".lang", ".properties", ".csv", ".cfg",
    ".toml", ".yml", ".yaml", ".md", ".proto", ".fsh", ".vsh", ".glsl",
    ".motd", ".version", ".license", ".log", ".accesswidener",
}
# 单文件与单版本总量上限（原始字节），避免仓库膨胀
MAX_FILE_BYTES = 4 * 1024 * 1024      # 单个文本文件 ≤ 4MB（如语言文件 en_us.json）
MAX_TOTAL_BYTES = 20 * 1024 * 1024    # 每版本提取总量 ≤ 20MB

# 愚人节版本 ID（均在官方清单中以 snapshot 类型出现）
APRIL_FOOLS_IDS = {
    "2.0",                    # 2013 Minecraft 2.0（未入清单，保险起见保留）
    "15w14a",                 # 2015 Love and Hugs
    "1.RV-Pre1",              # 2016 Trendy Update
    "3D Shareware v1.34",     # 2019
    "20w14infinite",          # 2020 20w14∞
    "22w13oneblockatatime",   # 2022
    "23w13a_or_b",            # 2023 Vote Update
    "24w14potato",            # 2024 Poisonous Potato
    "25w14craftmine",         # 2025 Craft Mine
}
# 兜底：4 月 1 日前后 1 天发布、且命名非常规的快照也视为愚人节版本
APRIL_DATE_RE = re.compile(r"^\d{4}-04-0[12]T")

TYPE_LABELS = {
    "release": "release",
    "snapshot": "snapshot",
    "old_beta": "old_beta",
    "old_alpha": "old_alpha",
}


def http_get(url: str, timeout: int = 120, retries: int = 5) -> bytes:
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "mc-version-tracker/1.0 (public metadata only)"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            wait = min(2 ** attempt * 3, 30)
            print(f"  请求失败（第 {attempt}/{retries} 次）：{e}，{wait}s 后重试 {url[:80]}")
            time.sleep(wait)
    raise RuntimeError(f"重试 {retries} 次后仍失败: {last_err}")


def load_json(path: str, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: str, obj, compress: bool = False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if compress:
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def classify(v: dict) -> str:
    """返回归一化类型：release / snapshot / april_fools / old_beta / old_alpha"""
    t = v.get("type", "")
    vid = v.get("id", "")
    if t in ("old_alpha", "old_beta"):
        return t
    if vid in APRIL_FOOLS_IDS:
        return "april_fools"
    # 非常规命名 + 4 月初发布 → 愚人节快照
    if t == "snapshot" and APRIL_DATE_RE.match(v.get("releaseTime", "")) \
            and not re.match(r"^\d+w\d+[a-z](-)?\w*$", vid) \
            and "-pre" not in vid and "-rc" not in vid:
        return "april_fools"
    return TYPE_LABELS.get(t, t)


def main():
    os.makedirs(VERSIONS_DIR, exist_ok=True)

    print("拉取官方版本清单 ...")
    manifest = json.loads(http_get(MANIFEST_URL).decode("utf-8"))
    all_versions = manifest["versions"]
    print(f"清单共 {len(all_versions)} 个版本，latest={manifest['latest']}")

    state = load_json(os.path.join(DATA_DIR, "state.json"), {
        "known_ids": [], "unpacked": {}, "contents": {},
        "unpacked_server": {}, "contents_server": {}})
    known_ids = set(state.get("known_ids", []))
    unpacked = state.get("unpacked", {})
    contents = state.get("contents", {})
    unpacked_server = state.get("unpacked_server", {})
    contents_server = state.get("contents_server", {})

    # ---------- 1. 全量元数据索引 ----------
    index = []
    for v in all_versions:
        vid = v["id"]
        index.append({
            "id": vid,
            "type": classify(v),
            "releaseTime": v.get("releaseTime", ""),
            "metaUrl": v.get("url", ""),
        })

    new_ids = [v["id"] for v in all_versions if v["id"] not in known_ids]
    new_notable = []
    for v in all_versions:
        if v["id"] in new_ids and classify(v) in ("release", "snapshot", "april_fools"):
            new_notable.append(v["id"])
    print(f"本次新发现版本 {len(new_ids)} 个，其中值得关注的：{new_notable or '无'}")

    # ---------- 2. 确定需要处理的版本集合 ----------
    by_id = {v["id"]: v for v in all_versions}
    ordered = list(all_versions)  # 清单本身按时间倒序

    def latest_n(ftype, n):
        return [v["id"] for v in ordered if classify(v) == ftype][:n]

    wanted = set(latest_n("release", TRACK_RELEASES))
    wanted |= set(latest_n("snapshot", TRACK_SNAPSHOTS))
    wanted |= {v["id"] for v in all_versions if classify(v) == "april_fools"}
    wanted |= {v["id"] for v in all_versions if classify(v) in ("old_alpha", "old_beta")}
    wanted |= set(BACKFILL)
    wanted &= set(by_id)

    # 客户端 todo：未拆包的 + 强制重拆的
    todo = set(w for w in wanted if w not in unpacked)
    if FORCE_REPACK:
        todo |= {b for b in BACKFILL if b in by_id}
    # FORCE_CONTENTS=1：为"已拆包但缺内容"的版本补充内容（重新下载 jar）
    if FORCE_CONTENTS and EXTRACT_CONTENTS:
        todo |= {w for w in wanted if w not in contents}
    todo = sorted(todo)
    print(f"需处理客户端 {len(todo)} 个")

    # 服务端 todo：所有 wanted 中缺服务端清单的
    server_todo = sorted(w for w in wanted if w not in unpacked_server)
    print(f"需处理服务端 {len(server_todo)} 个")

    # 内容提取范围：最新正式版 / 最新快照 / 全部愚人节版 / BACKFILL 指定版本 /
    # 以及本次新处理的版本（今后每个新版本都会带内容）。
    # CONTENTS_ALL=1（全量回填时）则对所有处理版本提取内容。
    unpacked_before = set(unpacked)
    unpacked_server_before = set(unpacked_server)
    latest_r, latest_s = manifest["latest"].get("release"), manifest["latest"].get("snapshot")
    content_ids = {latest_r, latest_s} | {v["id"] for v in all_versions if classify(v) == "april_fools"} | set(BACKFILL)

    meta_cache = {}

    def get_meta(vid):
        if vid not in meta_cache:
            meta_cache[vid] = json.loads(http_get(by_id[vid]["url"]).decode("utf-8"))
        return meta_cache[vid]

    # ---------- 3. 逐个下载 & 拆包 ----------
    def unpack_side(vid, side):
        """side: 'client' 或 'server'。返回 True 表示处理成功。"""
        meta = get_meta(vid)
        dl = meta.get("downloads", {}).get(side, {})
        other = meta.get("downloads", {}).get("server" if side == "client" else "client", {})
        if not dl.get("url"):
            print(f"  跳过 {vid} [{side}]：无下载地址")
            return False
        suffix = "" if side == "client" else ".server"
        need_content = EXTRACT_CONTENTS and (
            CONTENTS_ALL or (vid not in (unpacked_before if side == "client" else unpacked_server_before))
            or vid in content_ids)

        jar_bytes = http_get(dl["url"])
        sha1 = dl.get("sha1") or __import__("hashlib").sha1(jar_bytes).hexdigest()
        if len(jar_bytes) != dl.get("size", len(jar_bytes)):
            print(f"  警告 {vid} [{side}]：大小不符（{len(jar_bytes)} != {dl.get('size')}）")

        zf = zipfile.ZipFile(BytesIO(jar_bytes))
        entries = []
        total_uncompressed = 0
        text_contents = {}
        truncated = False
        text_total = 0
        for zi in zf.infolist():
            if zi.is_dir():
                continue
            total_uncompressed += zi.file_size
            entry = {
                "p": zi.filename,                # path
                "s": zi.file_size,               # size (bytes)
                "c": f"{zi.CRC:08x}",            # crc32
                "t": list(zi.date_time),         # timestamp
            }
            # 提取小型可读文本内容（遵循合理使用：仅限文本类、有大小上限）
            if need_content and text_total < MAX_TOTAL_BYTES \
                    and os.path.splitext(zi.filename)[1].lower() in TEXT_EXTS \
                    and 0 < zi.file_size <= MAX_FILE_BYTES:
                try:
                    text = zf.read(zi.filename).decode("utf-8", errors="replace")
                    text_contents[zi.filename] = text
                    text_total += zi.file_size
                    entry["x"] = 1               # 标记：内容已收录
                except Exception:
                    pass
            elif need_content and os.path.splitext(zi.filename)[1].lower() in TEXT_EXTS \
                    and zi.file_size > MAX_FILE_BYTES:
                truncated = True
            entries.append(entry)
        zf.close()

        listing = {
            "id": vid,
            "type": classify(by_id[vid]),
            "releaseTime": by_id[vid].get("releaseTime", ""),
            "side": side,
            "client": {"size": dl.get("size", len(jar_bytes)), "sha1": sha1},  # "client" 字段统一表示本端 jar 信息
            "other": ({"size": other.get("size"), "sha1": other.get("sha1")}
                      if other.get("url") else None),
            "fileCount": len(entries),
            "totalUncompressed": total_uncompressed,
            "hasContents": bool(text_contents),
            "files": entries,
        }
        safe_name = vid.replace("/", "_")
        save_json(os.path.join(VERSIONS_DIR, f"{safe_name}{suffix}.json.gz"), listing, compress=True)
        if side == "client":
            unpacked[vid] = sha1
        else:
            unpacked_server[vid] = sha1

        # 写出文本内容文件（懒加载给页面用）
        if need_content and text_contents:
            save_json(os.path.join(CONTENTS_DIR, f"{safe_name}{suffix}.json.gz"), {
                "id": vid, "side": side, "truncated": truncated,
                "files": text_contents,
            }, compress=True)
            if side == "client":
                contents[vid] = sha1
            else:
                contents_server[vid] = sha1
            print(f"  {vid} [{side}] 内容: {len(text_contents)} 个文本文件, "
                  f"{os.path.getsize(os.path.join(CONTENTS_DIR, safe_name + suffix + '.json.gz'))/1024:.0f} KB")

        print(f"  完成 {vid} [{side}]: {len(entries)} 个文件, jar {dl.get('size', len(jar_bytes))/1e6:.1f} MB, "
              f"清单 {os.path.getsize(os.path.join(VERSIONS_DIR, safe_name + suffix + '.json.gz'))/1024:.0f} KB")
        return True

    for vid in todo:
        try:
            if unpack_side(vid, "client"):
                state["unpacked"] = unpacked
                state["contents"] = contents
                save_json(os.path.join(DATA_DIR, "state.json"), state)
        except Exception as e:  # 单个版本失败不阻塞整体
            print(f"  失败 {vid} [client]: {e}", file=sys.stderr)

    for vid in server_todo:
        try:
            if unpack_side(vid, "server"):
                state["unpacked_server"] = unpacked_server
                state["contents_server"] = contents_server
                save_json(os.path.join(DATA_DIR, "state.json"), state)
        except Exception as e:
            print(f"  失败 {vid} [server]: {e}", file=sys.stderr)

    # ---------- 4. 给索引补充拆包信息并写出 ----------
    for item in index:
        item["tracked"] = item["id"] in unpacked
        item["contents"] = item["id"] in contents
        item["server"] = item["id"] in unpacked_server
    idx_path = os.path.join(DATA_DIR, "index.json")
    save_json(idx_path, {
        "generatedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "latest": manifest["latest"],
        "count": len(index),
        "versions": index,
    })
    state["known_ids"] = [v["id"] for v in all_versions]
    state["lastRun"] = __import__("datetime").datetime.utcnow().isoformat() + "Z"
    save_json(os.path.join(DATA_DIR, "state.json"), state)

    save_json(os.path.join(DATA_DIR, "last_run.json"), {
        "newVersions": new_notable,
        "newUnpacked": todo,
        "trackedCount": len(unpacked),
        "trackedServerCount": len(unpacked_server),
    })

    print(f"索引已写出：{len(index)} 个版本，已拆包 {len(unpacked)}（客户端）/ {len(unpacked_server)}（服务端），"
          f"含内容 {len(contents)} / {len(contents_server)}")
    if new_notable:
        print(f"NEW_VERSIONS={' '.join(new_notable)}")


if __name__ == "__main__":
    main()
