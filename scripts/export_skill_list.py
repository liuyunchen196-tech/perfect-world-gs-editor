#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 gs ELF .symtab 提取全部技能 Skill{ID}Stub 的 ID，结合 skillstr 名称，生成：
   - skills_all.csv (表，Excel 可开)
   - skills_all.db   (SQLite 数据库)
   每行/每条: (技能ID, 技能名, 默认等级=1, XML条目十六进制)  —— 用于往人物XML加技能。
"""
import struct, os, re, csv, sqlite3, datetime, sys

GS = r'D:\完美单机\技能修改器项目\data\gs'           # 138 服务端 gs
SKILLSTR = r'C:\Users\liuwust\AppData\Local\Doubao\User Data\Default\.doubao\agent_mode\workspace\.user_skills\perfect-world-gs-editor\assets\skillstr.txt'
OUT_CSV = r'D:\完美单机\技能修改器项目\data\skills_all.csv'
OUT_DB  = r'D:\完美单机\技能修改器项目\data\skills_all.db'

# ---------- 1. 解析 gs ELF 符号表，提取全部技能ID ----------
def parse_gs_skills(path):
    d = open(path, 'rb').read()
    if d[:4] != b'\x7fELF':
        raise SystemExit(f'不是 ELF: {path}')
    is32 = d[4] == 1
    if is32:
        e_shoff = struct.unpack_from('<I', d, 0x20)[0]
        e_shentsize = struct.unpack_from('<H', d, 0x2e)[0]
        e_shnum = struct.unpack_from('<H', d, 0x30)[0]
        e_shstrndx = struct.unpack_from('<H', d, 0x32)[0]
    else:
        e_shoff = struct.unpack_from('<Q', d, 0x28)[0]
        e_shentsize = struct.unpack_from('<H', d, 0x3a)[0]
        e_shnum = struct.unpack_from('<H', d, 0x3c)[0]
        e_shstrndx = struct.unpack_from('<H', d, 0x3e)[0]
    # section headers
    sh = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        if is32:
            name, typ, flags, addr, off_, size = struct.unpack_from('<IIIIII', d, off)[:6]
        else:
            name, typ, flags, addr, off_, size = struct.unpack_from('<IIQQQQ', d, off)[:6]
        sh.append((name, typ, addr, off_, size))
    # shstrtab
    shstr_name, shstr_typ, _, shstr_off, shstr_size = sh[e_shstrndx]
    shstr = d[shstr_off:shstr_off + shstr_size]
    def sname(nidx):
        e = shstr.find(b'\x00', nidx)
        return shstr[nidx:e].decode('latin1')
    symtab = strtab = None
    for (name, typ, addr, off_, size) in sh:
        nm = sname(name)
        if nm == '.symtab': symtab = (off_, size)
        if nm == '.strtab': strtab = (off_, size)
    if not symtab or not strtab:
        raise SystemExit('无 .symtab/.strtab')
    entsz = 16 if is32 else 24
    st_off, st_size = symtab
    strs = d[strtab[0]:strtab[0] + strtab[1]]
    skills = []
    for e in range(st_off, st_off + st_size, entsz):
        if is32:
            name, val, size_, info, other, shndx = struct.unpack_from('<IIIBBH', d, e)
        else:
            name, info, other, shndx, val, size_ = struct.unpack_from('<IBBHQQ', d, e)
        if name == 0: continue
        nm_end = strs.find(b'\x00', name)
        nm = strs[name:nm_end].decode('latin1')
        m = re.search(r'Skill(\d+)', nm)
        if m:
            skills.append(int(m.group(1)))
    return sorted(set(skills))

# ---------- 2. 读取 skillstr 名称 ----------
def load_names(path):
    raw = open(path, 'rb').read()
    if raw[:2] == b'\xff\xfe':
        text = raw[2:].decode('utf-16-le')
    else:
        try: text = raw.decode('utf-16-le')
        except Exception: text = raw.decode('utf-8', errors='replace')
    names = {}
    for ln in text.splitlines():
        m = re.match(r'^\s*(\d+)\s+"(.*)"', ln)
        if m:
            idx = int(m.group(1))
            if idx % 10 == 0:            # 偶数 idx = 名称行
                names[idx // 10] = m.group(2)
    return names

skills = parse_gs_skills(GS)
names = load_names(SKILLSTR)
print(f'gs 技能类总数: {len(skills)}')

def xml_entry(sid, level=1):
    # (技能ID, 0, 等级) 小端 32 位 → 十六进制文本
    h = sid.to_bytes(4,'little') + (0).to_bytes(4,'little') + level.to_bytes(4,'little')
    return h.hex()

rows = []
for sid in skills:
    nm = names.get(sid, f'技能{sid}')
    rows.append((sid, nm, 1, xml_entry(sid, 1)))

# ---------- 3. 写 CSV 表 ----------
with open(OUT_CSV, 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.writer(f)
    w.writerow(['技能ID', '技能名', '默认等级', 'XML条目(小端hex)'])
    for r in rows:
        w.writerow(r)
print(f'表已写: {OUT_CSV}  ({len(rows)} 技能)')

# ---------- 4. 写 SQLite 数据库 ----------
if os.path.exists(OUT_DB): os.remove(OUT_DB)
conn = sqlite3.connect(OUT_DB)
cur = conn.cursor()
cur.execute('CREATE TABLE skills (skill_id INTEGER PRIMARY KEY, name TEXT, default_level INTEGER, xml_hex TEXT)')
cur.executemany('INSERT INTO skills VALUES (?,?,?,?)', rows)
conn.commit()
print(f'数据库已写: {OUT_DB}  ({conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]} 条)')
cur.execute('CREATE INDEX IF NOT EXISTS idx_name ON skills(name)')
conn.commit(); conn.close()

# 抽样
print('\n样例(前8):')
for r in rows[:8]:
    print(f'  {r[0]:5}  {r[1]:12}  默认L{r[2]}  hex={r[3]}')


# ---------- --make: 按 "id:level,id:level,..." 生成角色技能块 hex (数量+条目) ----------
if __name__ == '__main__' and '--make' in sys.argv:
    import argparse as _ap
    ap = _ap.ArgumentParser()
    ap.add_argument('--make', help='如 1528:10,240:1 生成 数量+条目 的 hex')
    aa, _ = ap.parse_known_args()
    blk = []
    for part in aa.make.split(','):
        p = part.split(':')
        sid = int(p[0]); lv = int(p[1]) if len(p) > 1 else 1
        blk.append(sid.to_bytes(4, 'little') + (0).to_bytes(4, 'little') + lv.to_bytes(4, 'little'))
    total = (len(blk)).to_bytes(4, 'little') + b''.join(blk)
    print(f'技能数={len(blk)}')
    for part, bb in zip(aa.make.split(','), blk):
        print(f'  {part}: {bb.hex()}')
    print('拼接hex(数量+条目):')
    print(total.hex())
    sys.exit(0)
