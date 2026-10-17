#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gs_tool.py  完美世界单机 gs 技能查看/修改 统一工具（perfect-world-gs-editor 核心）
====================================================================================
原理：gs 是带 .symtab 符号表的 32 位 ELF。每个技能对应 GNET::Skill{ID}Stub 类，
特性在具名方法里（GetCoolingtime/GetExecutetime/GetRadius/GetMpcost/GetRequiredLevel/
State*::Calculate/State*::GetTime...）。本工具反汇编每个方法，抽取可修改常量
（mov imm32 / .rodata 浮点 double/float / 逐级数组）。

对话用法（由 AI Agent 调用）：
  python gs_tool.py list  横扫千军                          # 按名称搜索技能ID
  python gs_tool.py view  横扫千军  [--gs 文件]             # 查看技能全部特性+偏移
  python gs_tool.py view  56                                # 也支持技能ID
  python gs_tool.py modify 横扫千军 --set "冷却=6000" --set "范围=12.0"
                                                           # 按标签改(自动备份+写回+校验)
  python gs_tool.py modify 凌风 --off 0x914688 --type double --value 0.6
                                                           # 按偏移精确改(昏迷概率等)
  python gs_tool.py export-db [--out 目录]                  # 导出全技能数据库(SQLite/JSON/CSV)

默认 gs = 运行目录下 ./gs 或 D:/完美单机/修改gs工具/gs；默认 skillstr = 本脚本同级 assets/skillstr.txt。
"""
import os, sys, struct, shutil, time, re, json, sqlite3, argparse, csv
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

SKILLSTR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'assets', 'skillstr.txt')
GS_DEFAULT = r'D:\完美单机\修改gs工具\gs'
OUT_DEFAULT = r'D:\完美单机\技能数据库查看器'


# ======================= ELF 解析 =======================
class ELF:
    def __init__(self, data):
        self.data = data
        assert data[:4] == b'\x7fELF', "不是 ELF 文件"
        assert data[4] == 1, "不是 32 位 ELF"
        e_shoff = struct.unpack_from('<I', data, 0x20)[0]
        e_shentsize = struct.unpack_from('<H', data, 0x2E)[0]
        e_shnum = struct.unpack_from('<H', data, 0x30)[0]
        e_shstrndx = struct.unpack_from('<H', data, 0x32)[0]
        sections = []
        for i in range(e_shnum):
            off = e_shoff + i*e_shentsize
            n, t, f, a, o, s = struct.unpack_from('<6I', data, off)
            sections.append(dict(name='', type=t, flags=f, addr=a, offset=o, size=s))
        shstr = sections[e_shstrndx]
        shd = data[shstr['offset']:shstr['offset']+shstr['size']]
        self.sections = []
        for i in range(e_shnum):
            off = e_shoff + i*e_shentsize
            n, t, f, a, o, s = struct.unpack_from('<6I', data, off)
            e = shd.find(b'\x00', n)
            self.sections.append(dict(name=shd[n:e].decode('latin1'), type=t, flags=f, addr=a, offset=o, size=s))
        self.symtab = {}
        self.symtab_off = self.symtab_size = 0
        self.strtab_off = 0
        for sec in self.sections:
            if sec['name'] == '.symtab': self.symtab_off, self.symtab_size = sec['offset'], sec['size']
            if sec['name'] == '.strtab': self.strtab_off = sec['offset']
        self._parse_symtab()

    def sec_by_name(self, name):
        for s in self.sections:
            if s['name'] == name: return s
        return None

    def _parse_symtab(self):
        strtab = self.data[self.strtab_off:]
        n = self.symtab_size // 16
        for i in range(n):
            off = self.symtab_off + i*16
            st_name, st_value, st_size, st_info, st_other, st_shndx = struct.unpack_from('<IIIBBH', self.data, off)
            if st_name == 0: continue
            end = strtab.find(b'\x00', st_name)
            nm = strtab[st_name:end].decode('latin1', 'replace')
            self.symtab.setdefault(nm, []).append((st_value, st_size, st_shndx))

    def sym(self, nm):
        return self.symtab.get(nm, [])


# ======================= 常量提取 =======================
MD = Cs(CS_ARCH_X86, CS_MODE_32); MD.detail = True

def _count_refs(elf, ro_va):
    t = elf.sec_by_name('.text')
    text = elf.data[t['offset']:t['offset']+t['size']]
    return text.count(struct.pack('<I', ro_va))

def extract_method_constants(elf, name, value, size, shndx):
    if shndx == 0: return None
    sec = elf.sections[shndx] if shndx < len(elf.sections) else None
    if sec is None or sec['name'] != '.text': return None
    foff = sec['offset'] + (value - sec['addr'])
    code = elf.data[foff:foff+size]
    res = []
    for ins in MD.disasm(code, value):
        op, m = ins.op_str, ins.mnemonic
        if m == 'mov' and ins.bytes and (ins.bytes[0] in range(0xB8, 0xC0)) and ins.operands and len(ins.operands) > 1 and ins.operands[1].type == 2:
            imm = ins.operands[1].imm & 0xFFFFFFFF
            res.append((f"mov立即数 {op}", foff + (ins.address - value) + 1, 4, imm, 'int', False, None)); continue
        if m in ('fld', 'fild') and ins.operands and ins.operands[0].type == 3 and ins.operands[0].mem.base == 0 and ins.operands[0].mem.index == 0:
            disp = ins.operands[0].mem.disp & 0xFFFFFFFF
            ro_off = None
            for s in elf.sections:
                if s['addr'] and s['addr'] <= disp < s['addr']+s['size']:
                    ro_off = s['offset'] + (disp - s['addr'])
            if ro_off is not None:
                if 'qword' in op:
                    cur, plen, kind = struct.unpack('<d', elf.data[ro_off:ro_off+8])[0], 8, 'double'
                else:
                    cur, plen, kind = struct.unpack('<f', elf.data[ro_off:ro_off+4])[0], 4, 'float'
                shared = _count_refs(elf, disp) > 1
                res.append((f"浮点{kind}@{disp:#x} {op}", ro_off, plen, cur, kind, shared, disp)); continue
        if m == 'fldz': res.append((f"浮点0.0 {op}", foff+(ins.address-value), 2, 0.0, 'float_const', False, None))
        if m == 'fld1': res.append((f"浮点1.0 {op}", foff+(ins.address-value), 2, 1.0, 'float_const', False, None))
        if m == 'mov' and 'eax*4' in op and '+ 0x' in op:
            try:
                disp = int(op.split('+ 0x')[1].split(']')[0], 16)
                doff = None
                for s in elf.sections:
                    if s['addr'] and s['addr'] <= disp < s['addr']+s['size']:
                        doff = s['offset'] + (disp - s['addr'])
                vals = list(struct.unpack('<10i', elf.data[doff:doff+40])) if doff is not None else []
                res.append((f"数组@{disp:#x} {vals}", doff, 40, vals, 'array', False, disp))
            except Exception:
                pass
    return res


SKILL_METHODS = [
    ('GetCoolingtime', '冷却时长(毫秒)'), ('GetExecutetime', '施放/准备(毫秒)'),
    ('GetAttackspeed', '攻击速度'), ('GetRadius', '范围'), ('GetAttackdistance', '攻击距离'),
    ('GetEffectdistance', '效果距离'), ('GetPraydistance', '施法距离'),
    ('GetMpcost', '真气消耗'), ('GetGpcost', '金币消耗'), ('GetCpcost', '银两消耗'),
    ('GetRequiredLevel', '要求等级'), ('GetRequiredSp', '要求元神'),
    ('GetRequiredItem', '要求物品'), ('GetRequiredMoney', '要求金钱'),
    ('GetHitrate', '命中率'), ('GetEnmity', '仇恨'), ('GetAngle', '角度'),
    ('State1::Calculate', '伤害/效果(状态1)'), ('State2::Calculate', '伤害/效果(状态2)'),
    ('State3::Calculate', '伤害/效果(状态3)'),
]
def categorize(mname):
    m = re.search(r'State(\d+)Calculate', mname)
    if m:
        st = m.group(1)[0]
        if st in '123': return f'伤害/效果(状态{st})'
        return '伤害/效果'
    for key, label in SKILL_METHODS:
        if key in mname: return label
    m = re.search(r'State(\d+)GetTime', mname)
    if m:
        st = m.group(1)[0]
        if st in '123': return f'状态持续时间(状态{st})'
        return '状态持续时间'
    if 'Loop' in mname and 'State' in mname: return '状态持续效果'
    if 'StateAttack' in mname: return '攻击状态'
    return '其它'

def load_skill(elf, skill_id):
    marker = f'Skill{skill_id}Stub'
    methods = [nm for nm in elf.symtab if marker in nm]
    out = {}
    for nm in methods:
        if any(t in nm for t in ('D0Ev', 'D1Ev')): continue
        if nm.endswith('E') and 'StubE' in nm: continue
        if '5array' in nm: continue
        for value, size, shndx in elf.sym(nm):
            if shndx == 0: continue
            sec = elf.sections[shndx] if shndx < len(elf.sections) else None
            if sec and sec['name'] == '.text':
                if 'C1Ev' in nm:   # 构造函数：专门提取元气(AP)消耗/回复
                    ap = extract_ap_cost(elf, nm, value, size, shndx)
                    if ap:
                        for label, off, ln, cur, kind, shared, ro in ap:
                            out.setdefault(label, []).append((nm, value, size, [(label, off, ln, cur, kind, shared, ro)]))
                    continue
                cat = categorize(nm)
                consts = extract_method_constants(elf, nm, value, size, shndx)
                if consts: out.setdefault(cat, []).append((nm, value, size, consts))
    return out


# ======================= 元气(AP)消耗/回复 提取 =======================
# 每个技能在构造函数 Skill{ID}Stub::C1Ev 里：
#   mov dword [reg+0x32], imm32 = 元气(AP)消耗（运行时 Skill::GetApcost 读 [[Skill+0xc]+0x32]）
#   mov dword [reg+0x2e], imm32 = 元气(AP)回复（Skill::GetApgain 读 [[Skill+0xc]+0x2e]）
AP_COST_DISP = 0x32
AP_GAIN_DISP = 0x2e

def extract_ap_cost(elf, name, value, size, shndx):
    if shndx == 0: return None
    sec = elf.sections[shndx] if shndx < len(elf.sections) else None
    if sec is None or sec['name'] != '.text': return None
    foff = sec['offset'] + (value - sec['addr'])
    code = elf.data[foff:foff+size]
    res = []
    i = 0; n = len(code)
    while i < n:
        if code[i] == 0xC7 and i+1 < n:
            modrm = code[i+1]
            mod = (modrm >> 6) & 3
            reg = (modrm >> 3) & 7
            if reg == 0 and mod in (1, 2):
                if mod == 1 and i+7 <= n:
                    disp = struct.unpack_from('<b', code, i+2)[0]
                    if disp in (AP_COST_DISP, AP_GAIN_DISP):
                        imm = struct.unpack_from('<I', code, i+3)[0]
                        label = '元气(AP)消耗' if disp == AP_COST_DISP else '元气(AP)回复'
                        res.append((f"{label} {imm}", foff+i+3, 4, imm, 'int', False, None))
                    i += 7; continue
                elif mod == 2 and i+11 <= n:
                    disp = struct.unpack_from('<i', code, i+2)[0]
                    if disp in (AP_COST_DISP, AP_GAIN_DISP):
                        imm = struct.unpack_from('<I', code, i+6)[0]
                        label = '元气(AP)消耗' if disp == AP_COST_DISP else '元气(AP)回复'
                        res.append((f"{label} {imm}", foff+i+6, 4, imm, 'int', False, None))
                    i += 11; continue
        i += 1
    return res


# ======================= skillstr（名+描述） =======================
_LINE = re.compile(r'^(\d+)\s+"')
def _detect_encoding(path):
    with open(path, 'rb') as f: b = f.read(4)
    return 'utf-16' if b[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig' if b[:3] == b'\xef\xbb\xbf' else 'utf-8'

def parse_skillstr(path):
    name_map, desc_map = {}, {}
    if not path or not os.path.exists(path): return name_map, desc_map
    lines = open(path, encoding=_detect_encoding(path), errors='replace').read().splitlines()
    i = 0
    while i < len(lines):
        m = _LINE.match(lines[i])
        if not m: i += 1; continue
        idx = int(m.group(1)); code = idx // 10
        if idx % 2 == 0:
            name_map[code] = lines[i][m.end():].strip().rstrip('"')
        else:
            block = [lines[i]]; j = i + 1
            while j < len(lines):
                block.append(lines[j])
                if lines[j].rstrip().endswith('"'): break
                j += 1
            block[0] = lines[i][m.end():]
            desc_map[code] = '\n'.join(block).rstrip().rstrip('"')
            i = j
        i += 1
    return name_map, desc_map

def resolve_sid(key, name_map):
    """key 为数字ID 或 技能名；返回 sid。名字做子串匹配（含 真·/狂· 前缀也按原名匹配）。"""
    s = str(key).strip()
    if s.isdigit():
        return int(s), None
    hits = [sid for sid, nm in name_map.items() if nm == s]
    if hits: return hits[0], name_map.get(hits[0])
    hits = [sid for sid, nm in name_map.items() if s in nm]
    if len(hits) == 1: return hits[0], name_map.get(hits[0])
    if len(hits) > 1:
        raise SystemExit(f'名称"{key}"匹配多个技能 {hits}，请用技能ID精确指定。')
    raise SystemExit(f'未找到技能"{key}"。可先用 list 搜索。')


# ======================= 查看 =======================
def view_skill(elf, sid, name_map):
    data = load_skill(elf, sid)
    print(f'技能 {sid}  {name_map.get(sid, "")}  可编辑特性：')
    if not data:
        print('  (无 Skill{id}Stub 的可编辑方法，或均为数组/复杂计算)')
        return data
    for cat in sorted(data):
        print(f'\n【{cat}】')
        for nm, va, size, consts in data[cat]:
            print(f'  方法 {nm.split("Stub")[-1]}  VA={va:#x}')
            for c in consts:
                desc, off, ln, cur, kind, shared, ro = c
                cv = f'{cur:.8g}' if kind in ('float', 'double') else str(cur)
                sh = '  ⚠共享(影响其他技能)' if shared else ''
                print(f'      {desc} | off={off:#x} len={ln} kind={kind} cur={cv}{sh}')
    return data


# ======================= 修改 =======================
def _encode(kind, val):
    if kind == 'int': return struct.pack('<I', int(val, 0) & 0xFFFFFFFF)
    if kind == 'double': return struct.pack('<d', float(val))
    if kind == 'float': return struct.pack('<f', float(val))
    if kind == 'array': return struct.pack('<10i', *[int(x, 0) for x in str(val).split(',')])
    raise ValueError(f'不可直接改类型: {kind}')

def apply_mods(elf, sid, sets, off_edits, gs_path, out_path):
    """sets: [(kw,val)] 按标签/常量值匹配；off_edits: [(off,type,val)] 按偏移精确改。"""
    data = load_skill(elf, sid)
    edits = []  # (off, len, newbytes, desc)
    # 收集全部常量
    allc = []
    for cat, mlist in data.items():
        for nm, va, size, consts in mlist:
            allc.extend(consts)
    for kw, val in sets:
        kw_l = kw.lower()
        matches = []
        for cat, mlist in data.items():
            for nm, va, size, consts in mlist:
                for c in consts:
                    desc, off, ln, cur, kind, shared, ro = c
                    if kind in ('float_const',): continue
                    hit = kw_l in desc.lower() or kw_l in cat.lower()
                    val_hit = (isinstance(cur, float) and f'{cur:.8g}' == kw_l) or str(cur) == kw_l
                    if hit or val_hit:
                        matches.append((cat, c))
        if not matches:
            print(f'  ⚠ 关键词"{kw}"未匹配到可改常量，跳过。')
            continue
        for cat, c in matches:
            desc, off, ln, cur, kind, shared, ro = c
            if shared:
                print(f'  ⚠ {desc} 是共享常量(影响其他技能)，已跳过。'); continue
            try:
                nb = _encode(kind, val)
            except Exception as e:
                print(f'  ⚠ {desc}: {e}，跳过。'); continue
            edits.append((off, ln, nb, f'[{cat}] {desc}  {cur}->{val}'))
            print(f'  ✓ 将改: [{cat}] {desc}  cur={cur} -> {val}  @0x{off:x}')
    for off, typ, val in off_edits:
        if isinstance(off, str): off = int(off, 16)
        # 按偏移找现有常量类型；无则用 --type
        kind = typ
        cur = None
        for c in allc:
            if c[1] == off: kind, cur = c[4], c[3]; break
        if kind not in ('int', 'double', 'float'):
            print(f'  ⚠ 偏移0x{off:x} 类型不可直接改(kind={kind})，跳过。'); continue
        try:
            nb = _encode(kind, val)
        except Exception as e:
            print(f'  ⚠ 0x{off:x}: {e}，跳过。'); continue
        ln = 8 if kind == 'double' else 4
        edits.append((off, ln, nb, f'偏移0x{off:x} {kind}  {cur}->{val}'))
        print(f'  ✓ 将改: 偏移0x{off:x} {kind}  cur={cur} -> {val}')
    if not edits:
        print('没有任何可应用的修改。'); return None
    # 备份 + 打补丁 + 写回
    data_bytes = bytearray(open(gs_path, 'rb').read())
    bak = gs_path + '.bak_' + time.strftime('%Y%m%d_%H%M%S')
    shutil.copy2(gs_path, bak)
    for off, ln, nb, desc in edits:
        data_bytes[off:off+ln] = nb
    open(out_path, 'wb').write(data_bytes)
    print(f'已备份原文件 -> {bak}')
    print(f'已写回 -> {out_path}')
    # 回读校验
    out = open(out_path, 'rb').read()
    ok_all = True
    for off, ln, nb, desc in edits:
        got = out[off:off+ln]
        ok = got == nb
        if not ok: ok_all = False
        print(f'  回读校验 0x{off:x}: {"OK" if ok else "FAIL " + got.hex()}')
    return bak if ok_all else None


# ======================= 导出数据库 =======================
FIELDS = ['id', 'name', 'weapon', 'cooling_ms', 'execute_ms', 'attack_speed',
          'radius', 'attack_distance', 'effect_distance', 'pray_distance',
          'mp_cost', 'gp_cost', 'cp_cost', 'required_level', 'required_sp',
          'required_item', 'required_money', 'hitrate', 'enmity', 'angle',
          'damage1', 'damage2', 'damage3', 'state_time_ms', 'desc']

def _consts(data, cat):
    out = []
    for _nm, _va, _sz, cs in data.get(cat, []): out.extend(cs)
    return out

def _val(consts):
    arr = [c[3] for c in consts if c[4] == 'array']
    if arr: return ','.join(str(x) for x in arr[0])
    fl = [c[3] for c in consts if c[4] in ('float', 'double')]
    if fl: return ','.join(f'{x:.6g}' for x in fl)
    it = [c[3] for c in consts if c[4] == 'int']
    if it: return str(it[0])
    return None

SINGLE = {
    '冷却时长(毫秒)': 'cooling_ms', '施放/准备(毫秒)': 'execute_ms',
    '攻击速度': 'attack_speed', '状态持续时间': 'state_time_ms', '命中率': 'hitrate',
    '仇恨': 'enmity', '角度': 'angle', '范围': 'radius', '攻击距离': 'attack_distance',
    '效果距离': 'effect_distance', '施法距离': 'pray_distance', '真气消耗': 'mp_cost',
    '金币消耗': 'gp_cost', '银两消耗': 'cp_cost', '要求等级': 'required_level',
    '要求元神': 'required_sp', '要求物品': 'required_item', '要求金钱': 'required_money',
}
def _weapon(desc):
    if not desc: return '无/通用'
    m = re.search(r'武器限制\s*(\^[0-9a-fA-F]{6})?\s*([^\n^]+)', desc)
    w = m.group(2).strip() if m else ''
    w = w.replace('，', '、').replace(',', '、').strip()
    return w if w and w != '无' else '无/通用'

def export_db(elf, name_map, desc_map, outdir):
    os.makedirs(outdir, exist_ok=True)
    ids = sorted({int(m.group(1)) for nm in elf.symtab if (m := re.search(r'Skill(\d+)Stub', nm))})
    rows = []
    for sid in ids:
        data = load_skill(elf, sid)
        row = {'id': sid, 'name': name_map.get(sid, ''), 'weapon': _weapon(desc_map.get(sid, ''))}
        for cat, col in SINGLE.items():
            cs = _consts(data, cat)
            row[col] = _val(cs) if cs else None
        for n in (1, 2, 3):
            key = f'伤害/效果(状态{n})'
            if key not in data: key = '伤害/效果'
            cs = _consts(data, key)
            row[f'damage{n}'] = ','.join(f'{c[3]:.6g}' for c in cs if c[4] in ('float', 'double', 'int')) if cs else None
        row['desc'] = desc_map.get(sid, '')
        rows.append(row)
    jp = os.path.join(outdir, 'skills_viewer.json')
    json.dump(rows, open(jp, 'w', encoding='utf-8'), ensure_ascii=False)
    dbp = os.path.join(outdir, 'skill_viewer.sqlite')
    conn = sqlite3.connect(dbp)
    conn.execute('DROP TABLE IF EXISTS skills')
    cols = ', '.join(f'"{c}" TEXT' for c in FIELDS)
    conn.execute(f'CREATE TABLE skills ({cols})')
    ph = ', '.join('?' for _ in FIELDS)
    for r in rows:
        conn.execute(f'INSERT INTO skills VALUES ({ph})', [r.get(c, '') for c in FIELDS])
    conn.commit(); conn.close()
    with open(os.path.join(outdir, 'skills_viewer.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
        w.writeheader(); w.writerows(rows)
    print(f'导出 {len(rows)} 条 -> {outdir}')


def main():
    ap = argparse.ArgumentParser(description='完美世界单机 gs 技能查看/修改工具')
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--gs', default=GS_DEFAULT, help='gs 文件路径')
    common.add_argument('--skillstr', default=SKILLSTR, help='skillstr.txt 路径')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p_list = sub.add_parser('list', parents=[common]); p_list.add_argument('kw', nargs='?', default='')
    p_view = sub.add_parser('view', parents=[common]); p_view.add_argument('skill')
    p_mod = sub.add_parser('modify', parents=[common]); p_mod.add_argument('skill')
    p_mod.add_argument('--set', action='append', metavar='关键词=新值')
    p_mod.add_argument('--off', action='append', metavar='偏移(hex)')
    p_mod.add_argument('--type', action='append', choices=['int', 'float', 'double'], default=[])
    p_mod.add_argument('--value', action='append', metavar='新值')
    p_mod.add_argument('--out', default=None)
    p_exp = sub.add_parser('export-db', parents=[common]); p_exp.add_argument('--out', default=OUT_DEFAULT)
    a = ap.parse_args()

    if not os.path.exists(a.gs):
        print(f'gs 文件不存在: {a.gs}'); sys.exit(1)
    elf = ELF(open(a.gs, 'rb').read())
    name_map, desc_map = parse_skillstr(a.skillstr)

    if a.cmd == 'list':
        kw = (a.kw or '').strip()
        hits = [(sid, nm) for sid, nm in sorted(name_map.items()) if not kw or kw in nm]
        for sid, nm in hits[:200]:
            print(f'{sid:5d}  {nm}')
        print(f'共 {len(hits)} 个匹配' + ('' if len(hits) <= 200 else '（仅显示前200）'))
    elif a.cmd == 'view':
        sid, _ = resolve_sid(a.skill, name_map)
        view_skill(elf, sid, name_map)
    elif a.cmd == 'modify':
        sid, _ = resolve_sid(a.skill, name_map)
        sets = [tuple(x.split('=', 1)) for x in (a.set or []) if '=' in x]
        offs = [int(x, 16) if isinstance(x, str) else x for x in (a.off or [])]
        off_edits = list(zip(offs, a.type, a.value)) if a.value else []
        # 校验 --off/--type/--value 数量
        if a.off and len(a.off) != len(a.value or []):
            print('--off/--type/--value 数量不一致'); sys.exit(1)
        out = a.out or a.gs
        apply_mods(elf, sid, sets, off_edits, a.gs, out)
    elif a.cmd == 'export-db':
        export_db(elf, name_map, desc_map, a.out)

if __name__ == '__main__':
    main()
