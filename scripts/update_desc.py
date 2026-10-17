#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_desc.py — 完美世界 skillstr.txt（UTF-16LE）技能描述伤害数字精确写回工具。

设计原则：
- 原样保留编码（UTF-16LE 带/不带 BOM）与行尾（CRLF / LF），不做任何全局重写。
- 只按 idx 定位技能描述块（偶数 idx = 名称行，奇数 idx+1 = 标题行，其后为描述行，
  直到下一个偶数 idx 名称行），块内精确定位伤害数字，禁止正则扫全文件盲改。
- --list  <idx>            : 打印该技能描述块并抽取其中的伤害数字（^96f5ff<NUM>^ffffff的物理伤害）。
- --set-dmg <idx> <新值>   : 仅把该技能块内"上^96f5ff<旧>^ffffff的物理伤害"的 <旧> 换成 <新值>。
                             替换前打印命中上下文 2 行；替换后回读校验并打印 diff。

用法：
  python update_desc.py --file <skillstr.txt路径> --list 17280
  python update_desc.py --file <skillstr.txt路径> --set-dmg 17280 1770
"""
import argparse, os, re, sys, shutil, datetime

IDX_RE   = re.compile(r'^\s*(\d+)\s+"')          # 名称/标题行：<idx>  "名"
DMG_RE   = re.compile(r'上\^96f5ff(\d+)\^ffffff的物理伤害')  # 剑刃风暴风格：上<num>的物理伤害
# 兼容：也抽取 加^96f5ff<num>^ffffff 的伤害（箭阵风格，但箭阵侧多为 %d）
DMG_RE2  = re.compile(r'(?:加|附)\^96f5ff(\d+)\^ffffff')
# 剑刃风暴：造成35%%基础物理攻击（基础攻击百分比，硬编码 -> %d%% 动态）
PCT_RE   = re.compile(r'造成(\d+)%%基础物理攻击')


def detect_encoding(raw: bytes):
    """返回 (use_bom, newline)。本工具只处理 UTF-16LE（带/不带 BOM）。"""
    if raw[:2] == b'\xff\xfe':
        return True
    if raw[:2] == b'\xfe\xff':
        raise SystemExit("检测到 UTF-16BE (FE FF)，本工具按 UTF-16LE 处理，中止。")
    return False


def read_file(path):
    raw = open(path, 'rb').read()
    has_bom = detect_encoding(raw)
    body = raw[2:] if has_bom else raw
    text = body.decode('utf-16-le')
    crlf = text.count('\r\n')
    lf   = text.count('\n')
    newline = '\r\n' if crlf >= (lf - crlf) else '\n'
    lines = text.split(newline)
    lines = [l.rstrip('\r') for l in lines]
    return has_bom, newline, lines


def write_file(path, has_bom, newline, lines):
    out = newline.join(lines)
    data = out.encode('utf-16-le')
    if has_bom:
        data = b'\xff\xfe' + data
    open(path, 'wb').write(data)


def find_name_lines(lines):
    # 返回 {idx: line_index}，记录所有 <idx>  "名" 行（奇偶都记）。
    pos = {}
    for i, l in enumerate(lines):
        m = IDX_RE.match(l)
        if m:
            pos.setdefault(int(m.group(1)), i)
    return pos


def skill_block(lines, pos, target):
    """返回 (start, end)：描述块。锚定奇数标题行 target+1（两种布局都含描述），
    延伸到其后第一个任意编号行（下一个技能的名称/标题行）。
    - 915KB 交错布局：target 偶数名称行紧邻 target+1 奇数标题行；
    - 1.76MB 合并布局：偶数名称在名字索引区，描述在 target+1 奇数标题行起。"""
    title = target + 1
    if title in pos:
        start = pos[title]
    elif target in pos:
        start = pos[target]
    else:
        return None
    nxt = len(lines)
    for i, l in enumerate(lines):
        if i <= start:
            continue
        m = IDX_RE.match(l)
        if m:
            nxt = i
            break
    return start, nxt


def extract_dmg(block_lines):
    """在描述块文本里找伤害数字。返回 [(行号, 旧数字, 整行)]。"""
    hits = []
    for l in block_lines:
        for rx in (DMG_RE, DMG_RE2):
            m = rx.search(l)
            if m:
                hits.append((m.group(1), l))
                break
    return hits


def cmd_list(path, target):
    has_bom, newline, lines = read_file(path)
    pos = find_name_lines(lines)
    blk = skill_block(lines, pos, target)
    if not blk:
        print(f"[!] idx {target} 名称行未找到。文件里相邻 idx 样例：")
        keys = sorted(pos)
        # 找最近的
        near = [k for k in keys if abs(k-target) <= 20]
        for k in near:
            print(f"      {lines[pos[k]]}")
        return None
    start, end = blk
    print(f"=== idx {target} 描述块 (lines {start+1}..{end}, bom={has_bom}, newline={repr(newline)}) ===")
    for j in range(start, end):
        print(f"|{j+1}| {lines[j]}")
    block_lines = lines[start:end]
    hits = extract_dmg(block_lines)
    print(f"--- 抽取到的伤害数字(^96f5ff<NUM>^ffffff) ---")
    if not hits:
        print("   （块内无具体数字；箭阵等技能此处为 %d 占位符，由客户端按等级填充）")
    for num, l in hits:
        print(f"   数字={num!r}   行内容={l.strip()[:80]}")
    return hits


def backup(path):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path + f".bak{ts}"
    shutil.copy2(path, bak)
    return bak


def cmd_set_dmg(path, target, newval, do_backup=True, dry_run=False):
    has_bom, newline, lines = read_file(path)
    pos = find_name_lines(lines)
    blk = skill_block(lines, pos, target)
    if not blk:
        raise SystemExit(f"[!] idx {target} 未找到，中止。")
    start, end = blk
    # 仅在该块内找 "上^96f5ff<old>^ffffff的物理伤害"
    target_line_idx = None
    old_num = None
    for j in range(start, end):
        m = DMG_RE.search(lines[j])
        if m:
            target_line_idx = j
            old_num = m.group(1)
            break
    if target_line_idx is None:
        raise SystemExit(f"[!] idx {target} 块内未找到 '上^96f5ff<NUM>^ffffff的物理伤害'，中止（不盲改）。")
    if old_num == str(newval):
        print(f"[=] idx {target}: 当前已是 {old_num}，无需修改。")
        return None
    # 打印命中上下文 2 行
    print(f"[命中] idx {target} line {target_line_idx+1}: old={old_num} -> new={newval}")
    for j in range(max(start, target_line_idx-2), min(end, target_line_idx+3)):
        mark = ">>" if j == target_line_idx else "  "
        print(f"   {mark}|{j+1}| {lines[j]}")
    if dry_run:
        print("[dry-run] 不写入。")
        return None
    # 精确替换该块内这一处（DMG_RE 只匹配这一种句式）
    old_line = lines[target_line_idx]
    new_line = DMG_RE.sub(f"上^96f5ff{newval}^ffffff的物理伤害", old_line, count=1)
    bak = backup(path) if do_backup else None
    lines[target_line_idx] = new_line
    write_file(path, has_bom, newline, lines)
    # 回读校验
    has_bom2, newline2, lines2 = read_file(path)
    after = lines2[target_line_idx]
    ok = (f"^96f5ff{newval}^ffffff" in after) and (old_num not in after)
    print(f"[写回] backup={bak}")
    print(f"[校验] bom 保留={has_bom2==has_bom} newline 保留={newline2==newline}")
    print(f"[diff] line {target_line_idx+1}:")
    print(f"   - {old_line}")
    print(f"   + {new_line}")
    print(f"[回读] 含新值={f'^96f5ff{newval}^ffffff' in after}  旧值已消失={old_num not in after}  OK={ok}")
    return bak


def cmd_to_placeholder(path, target, do_backup=True, dry_run=False):
    """把该技能块内硬编码伤害数字转成 %d 动态占位符（对齐箭阵句式）：
       1) 上^96f5ff<num>^ffffff的物理伤害 -> 上^96f5ff%d^ffffff的物理伤害
       2) 造成<num>%%基础物理攻击        -> 造成^96f5ff%d%%^ffffff基础物理攻击
       只动这两处，其余文字结构保留。"""
    has_bom, newline, lines = read_file(path)
    pos = find_name_lines(lines)
    blk = skill_block(lines, pos, target)
    if not blk:
        raise SystemExit(f"[!] idx {target} 未找到，中止。")
    start, end = blk
    # 收集两处命中
    edits = []  # (line_idx, old_line, new_line, tag)
    for j in range(start, end):
        m1 = DMG_RE.search(lines[j])
        if m1:
            new_line = DMG_RE.sub("上^96f5ff%d^ffffff的物理伤害", lines[j], count=1)
            edits.append((j, lines[j], new_line, f"flat {m1.group(1)}->%d"))
        m2 = PCT_RE.search(lines[j])
        if m2:
            new_line = PCT_RE.sub(r"造成^96f5ff%d%%^ffffff基础物理攻击", lines[j], count=1)
            edits.append((j, lines[j], new_line, f"pct {m2.group(1)}%%->%d%%"))
    if not edits:
        print(f"[=] idx {target}: 块内未找到可转占位的硬编码伤害数字（可能已是 %d）。")
        return None
    # 打印命中上下文
    print(f"[命中] idx {target}，共 {len(edits)} 处：")
    for li, old_line, new_line, tag in edits:
        print(f"   - line {li+1} [{tag}]")
        for j in range(max(start, li-1), min(end, li+2)):
            mark = ">>" if j == li else "  "
            print(f"     {mark}|{j+1}| {lines[j]}")
        print(f"     diff: -{old_line.strip()}")
        print(f"           +{new_line.strip()}")
    if dry_run:
        print("[dry-run] 不写入。")
        return None
    bak = backup(path) if do_backup else None
    for li, old_line, new_line, tag in edits:
        lines[li] = new_line
    write_file(path, has_bom, newline, lines)
    # 回读校验
    has_bom2, newline2, lines2 = read_file(path)
    allok = True
    for li, old_line, new_line, tag in edits:
        after = lines2[li]
        ok = ("%d" in after) and (after == new_line)
        allok = allok and ok
    print(f"[写回] backup={bak}")
    print(f"[校验] bom 保留={has_bom2==has_bom}  newline 保留={newline2==newline}  全部命中行回读一致={allok}")
    return bak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', required=True)
    ap.add_argument('--list', type=int, dest='list_idx')
    ap.add_argument('--set-dmg', dest='set_dmg', type=int, default=None, metavar='IDX')
    ap.add_argument('newval', nargs='?', default=None, help='与 --set-dmg 配套的新数值')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-backup', action='store_true')
    ap.add_argument('--to-placeholder', dest='to_ph', type=int, default=None, metavar='IDX',
                    help='把该技能块内硬编码伤害数字转成 %%d 动态占位（对齐箭阵）')
    a = ap.parse_args()
    if not os.path.exists(a.file):
        raise SystemExit(f"文件不存在: {a.file}")
    if a.list_idx is not None:
        cmd_list(a.file, a.list_idx)
    elif a.to_ph is not None:
        cmd_to_placeholder(a.file, a.to_ph,
                           do_backup=not a.no_backup, dry_run=a.dry_run)
    elif a.set_dmg is not None:
        if a.newval is None:
            raise SystemExit("--set-dmg 需要提供新值：... --set-dmg <idx> <新值>")
        cmd_set_dmg(a.file, a.set_dmg, a.newval,
                    do_backup=not a.no_backup, dry_run=a.dry_run)
    else:
        ap.print_help()


if __name__ == '__main__':
    main()
