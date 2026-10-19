#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
element_manager.py — element.data 修改工作流（复用 EL 编辑器，无需破译加密）。

分工：
  - 本脚本(AI/用户可调)：备份原文件、启动 EL 编辑器、检测保存、分发到客户端/服务端、校验。
  - EL 编辑器(用户 GUI)：实际的读取/修改/保存加密 element.data。

命令：
  python element_manager.py open    <element.data路径>                    备份→启动EL编辑器打开
  python element_manager.py backup  <element.data路径>                    仅备份到同级 备份data/
  python element_manager.py sync    <已保存的新文件> --client <path> [--server <path>]
                                                                          备份目标原文件→复制新文件→hash校验
  python element_manager.py check   <element.data路径>                    打印 hash/大小/修改时间(供检测保存)
用法示例：
  python element_manager.py open "D:\\完美单机\\完美世界国服144\\2客户端\\element\\data\\elements.data"
  python element_manager.py sync  "D:\\完美单机\\完美世界国服144\\2客户端\\element\\data\\elements.data" \
        --client "D:\\完美单机\\完美世界国服144\\2客户端\\element\\data\\elements.data" \
        --server "D:\\完美单机\\138端\\...\\gamed\\config\\elements.data"
"""
import argparse, os, sys, subprocess, shutil, hashlib, datetime, json

# 常用路径（可被 --client/--server 覆盖）
CLIENT_144 = r"D:\完美单机\完美世界国服144\2客户端\element\data\elements.data"
SERVER_138 = r"D:\完美单机\138端\20.完美国服138V78端\其他文件-非必要\完美世界国服138V78端 提取文件\root\pwserver\gamed\config\elements.data"
EL_EXE = r"D:\完美单机\国服工具\EL编辑器\el编辑器.exe"

def ts():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

def backup_dir_of(p):
    # 用户要求：备份到 element.data 所在目录下新建的 备份data 文件夹
    return os.path.join(os.path.dirname(p), "备份data")

def do_backup(p, tag="backup"):
    if not os.path.exists(p):
        raise SystemExit(f"[!] 文件不存在: {p}")
    d = backup_dir_of(p)
    os.makedirs(d, exist_ok=True)
    bak = os.path.join(d, f"elements.{tag}.{ts()}.data")
    shutil.copy2(p, bak)
    print(f"[备份] {p}\n    -> {bak}  ({os.path.getsize(bak)} B)")
    return bak

def cmd_open(p):
    if not os.path.exists(p):
        raise SystemExit(f"[!] 文件不存在: {p}")
    if not os.path.exists(EL_EXE):
        raise SystemExit(f"[!] EL编辑器不存在: {EL_EXE}")
    do_backup(p, "before_edit")
    print(f"[启动] EL 编辑器打开: {p}")
    subprocess.Popen([EL_EXE, p])
    print("    请在弹出的「蜜蜂EL编辑器」窗口中修改并保存。保存后运行：")
    print(f"    python element_manager.py sync \"{p}\" --client \"{p}\" [--server <服务端路径>]")

def cmd_sync(args):
    src = args.src
    if not os.path.exists(src):
        raise SystemExit(f"[!] 新文件不存在: {src}")
    targets = []
    if args.client:
        targets.append(("客户端", args.client))
    if args.server:
        targets.append(("服务端", args.server))
    if not targets:
        raise SystemExit("[!] 至少需要 --client 或 --server")
    src_hash, src_size = md5(src), os.path.getsize(src)
    print(f"[源文件] {src}  size={src_size}  md5={src_hash[:12]}…")
    for name, tgt in targets:
        if not os.path.exists(tgt):
            print(f"[!] {name}目标不存在，跳过: {tgt}")
            continue
        do_backup(tgt, "before_sync")
        shutil.copy2(src, tgt)
        th, ts2 = md5(tgt), os.path.getsize(tgt)
        ok = (th == src_hash) and (ts2 == src_size)
        print(f"[同步] {name}: {tgt}")
        print(f"     size={ts2}  md5={th[:12]}…  一致={ok}")
        if not ok:
            print("    [!] hash/大小不一致，请检查！")

def cmd_check(p):
    if not os.path.exists(p):
        raise SystemExit(f"[!] 文件不存在: {p}")
    st = os.stat(p)
    print(json.dumps({"path": p, "size": st.st_size,
                      "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                      "md5": md5(p)}, ensure_ascii=False, indent=2))

def main():
    ap = argparse.ArgumentParser(description="element.data 修改工作流(复用EL编辑器)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_open = sub.add_parser("open"); p_open.add_argument("src")
    sub.add_parser("backup").add_argument("src")
    p_sync = sub.add_parser("sync"); p_sync.add_argument("src")
    p_sync.add_argument("--client", default=None)
    p_sync.add_argument("--server", default=None)
    sub.add_parser("check").add_argument("src")
    a = ap.parse_args()
    if a.cmd == "open": cmd_open(a.src)
    elif a.cmd == "backup": do_backup(a.src, "manual")
    elif a.cmd == "sync": cmd_sync(a)
    elif a.cmd == "check": cmd_check(a.src)

if __name__ == "__main__":
    main()
