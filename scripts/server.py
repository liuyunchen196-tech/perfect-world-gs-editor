#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技能修改器 后端（完美世界单机 gs） —— 纯标准库，零第三方依赖，可完全离线运行
==============================================================================
启动：双击 启动.bat（或 python server.py）→ 浏览器打开 http://127.0.0.1:7860
API：
  POST /api/load   请求体=gs 原始字节，解析 ELF，返回技能列表
  GET  /api/skill/<id>  反汇编某技能特性（含伤害公式常数+偏移+类型+当前值）
  POST /api/save   请求体=JSON {edits:[{off,kind,val}]}，备份→打补丁→写回→回读校验
  GET  /           前端页面
当前 gs 存 data/current.gs，备份在 data/backup/。
"""
import os, re, json, struct, time, shutil
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
# 可移植：脚本目录若本身就是工作根（含 data/ 与 assets/），用脚本目录；
# 否则（如放在 skill 的 scripts/ 下）用其上级目录。
if os.path.isdir(os.path.join(_HERE, 'data')) and os.path.exists(os.path.join(_HERE, 'assets', 'skillstr.txt')):
    BASE = _HERE
else:
    BASE = os.path.join(_HERE, '..')
SKILLSTR = os.path.join(BASE, 'assets', 'skillstr.txt')
DATA_DIR = os.path.join(BASE, 'data')
CURRENT_GS = os.path.join(DATA_DIR, 'current.gs')
BACKUP_DIR = os.path.join(DATA_DIR, 'backup')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

try:
    import batch_engine as BE
except Exception as _e:
    BE = None


# ======================= ELF 解析（带符号表 32 位） =======================
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

    def va2off(self, va):
        for s in self.sections:
            if s['addr'] and s['addr'] <= va < s['addr']+s['size']:
                return s['offset'] + (va - s['addr']), s['name']
        return None, None

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


# ======================= 常量提取（字节扫描，无需 capstone） =======================
# 识别的指令字节模式：
#   mov reg, imm32       : 0xB8..0xBF + imm32 (5 字节)
#   fld qword [disp32]   : DD 05 + disp32
#   fld dword [disp32]   : D9 05 + disp32
#   fldz                 : D9 EE
#   fld1                 : D9 E8
#   数组 mov eax,[eax*4+disp32] : 8B 04 85 + disp32

def _count_refs(elf, va):
    t = elf.sec_by_name('.text')
    return elf.data[t['offset']:t['offset']+t['size']].count(struct.pack('<I', va))

def _in_rodata(elf, va):
    r = elf.sec_by_name('.rodata')
    return bool(r and r['addr'] <= va < r['addr']+r['size'])

def extract_method_constants(elf, name, value, size, shndx):
    if shndx == 0: return None
    sec = elf.sections[shndx] if shndx < len(elf.sections) else None
    if sec is None or sec['name'] != '.text': return None
    foff = sec['offset'] + (value - sec['addr'])
    code = elf.data[foff:foff+size]
    res = []
    i = 0
    n = len(code)
    while i < n:
        b = code[i]
        # 1) mov reg, imm32 (B8-BF)
        if 0xB8 <= b <= 0xBF and i+5 <= n:
            imm = struct.unpack_from('<I', code, i+1)[0]
            # 过滤误报：立即数应为小常量，或指向某节的绝对地址；否则多为指令内部字节误判
            ok = imm < 0x200000 or any(s['addr'] and s['addr'] <= imm < s['addr']+s['size'] for s in elf.sections)
            # 立即数为合法 IEEE float 位模式（指数段非全0非全1）→ 按浮点参数处理（如剑刃风暴伤害 mov eax,0x41f00000=30.0）
            exp = (imm & 0x7f800000) >> 23
            is_fbits = exp not in (0, 0xff) and not ok
            if is_fbits:
                fval = struct.unpack('<f', struct.pack('<I', imm))[0]
                # 过滤相邻字节误报：合理技能浮点参数应在 [1e-10,1e15] 量级
                if 1e-10 <= abs(fval) < 1e15:
                    res.append((f"浮点参数(imm) {fval:.8g}", foff+i+1, 4, fval, 'float', False, None))
            elif ok:
                res.append((f"mov立即数 eax+{b-0xB8}, 0x{imm:x}", foff+i+1, 4, imm, 'int', False, None))
            i += 5; continue
        # 2) fld qword [disp32] = DD 05
        if b == 0xDD and i+1 < n and code[i+1] == 0x05 and i+6 <= n:
            disp = struct.unpack_from('<I', code, i+2)[0]
            ro = elf.va2off(disp)
            if ro and ro[1] == '.rodata':
                cur = struct.unpack('<d', elf.data[ro[0]:ro[0]+8])[0]
                res.append((f"浮点double@{disp:#x}", ro[0], 8, cur, 'double', _count_refs(elf, disp) > 1, disp))
            i += 6; continue
        # 3) fld dword [disp32] = D9 05
        if b == 0xD9 and i+1 < n and code[i+1] == 0x05 and i+6 <= n:
            disp = struct.unpack_from('<I', code, i+2)[0]
            ro = elf.va2off(disp)
            if ro and ro[1] == '.rodata':
                cur = struct.unpack('<f', elf.data[ro[0]:ro[0]+4])[0]
                res.append((f"浮点float@{disp:#x}", ro[0], 4, cur, 'float', _count_refs(elf, disp) > 1, disp))
            i += 6; continue
        # 4) fldz / fld1
        if b == 0xD9 and i+1 < n:
            if code[i+1] == 0xEE: res.append((f"浮点0.0", foff+i, 2, 0.0, 'float_const', False, None))
            elif code[i+1] == 0xE8: res.append((f"浮点1.0", foff+i, 2, 1.0, 'float_const', False, None))
        # 5) 数组 mov eax,[eax*4+disp32] = 8B 04 85
        if b == 0x8B and i+3 < n and code[i+1] == 0x04 and code[i+2] == 0x85 and i+7 <= n:
            disp = struct.unpack_from('<I', code, i+3)[0]
            do = elf.va2off(disp)
            if do:
                vals = list(struct.unpack('<10i', elf.data[do[0]:do[0]+40]))
                res.append((f"数组@{disp:#x} {vals}", do[0], 40, vals, 'array', False, disp))
            i += 7; continue
        i += 1
    return res


SKILL_METHODS = [
    ('GetCoolingtime', '冷却时长(毫秒)'), ('GetExecutetime', '施放/准备(毫秒)'),
    ('GetAttackspeed', '攻击速度'), ('GetRadius', '范围'), ('GetAttackdistance', '攻击距离'),
    ('GetEffectdistance', '效果距离'), ('GetPraydistance', '施法距离'),
    ('GetMpcost', '真气消耗'), ('GetGpcost', '金币消耗'), ('GetCpcost', '银两消耗'),
    ('GetRequiredLevel', '要求等级'), ('GetRequiredSp', '要求元神'),
    ('GetRequiredItem', '要求物品'), ('GetRequiredMoney', '要求金钱'),
    ('GetHitrate', '命中率'), ('GetEnmity', '仇恨'), ('GetAngle', '角度'),
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
# 机制：每个技能在构造函数 Skill{ID}Stub::C1Ev 里
#   mov dword [reg+0x32], imm32  = 元气(AP)消耗（运行时 Skill::GetApcost 读 [[Skill+0xc]+0x32]）
#   mov dword [reg+0x2e], imm32  = 元气(AP)回复（Skill::GetApgain  读 [[Skill+0xc]+0x2e]）
# 字节：C7 /0 [reg+disp8/disp32], imm32
AP_COST_DISP = 0x32
AP_GAIN_DISP = 0x2e

def extract_ap_cost(elf, name, value, size, shndx):
    """扫描构造函数字节，返回 [(label, 文件偏移, 4, 当前值, 'int', False, None)]。"""
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
                if mod == 1 and i+7 <= n:      # [reg+disp8]
                    disp = struct.unpack_from('<b', code, i+2)[0]
                    if disp in (AP_COST_DISP, AP_GAIN_DISP):
                        imm = struct.unpack_from('<I', code, i+3)[0]
                        label = '元气(AP)消耗' if disp == AP_COST_DISP else '元气(AP)回复'
                        res.append((label, foff+i+3, 4, imm, 'int', False, None))
                    i += 7; continue
                elif mod == 2 and i+11 <= n:   # [reg+disp32]
                    disp = struct.unpack_from('<i', code, i+2)[0]
                    if disp in (AP_COST_DISP, AP_GAIN_DISP):
                        imm = struct.unpack_from('<I', code, i+6)[0]
                        label = '元气(AP)消耗' if disp == AP_COST_DISP else '元气(AP)回复'
                        res.append((label, foff+i+6, 4, imm, 'int', False, None))
                    i += 11; continue
        i += 1
    return res


# ======================= skillstr =======================
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
            # 描述块：奇数 idx 行起，直到下一个 idx 行止（不含）。
            # ⚠ 不能用"以引号结尾"判断——技能名行本身以"结尾，会吞掉后续名称行（如 1460"狂战士"）。
            j = i + 1
            while j < len(lines):
                if _LINE.match(lines[j]):
                    break
                j += 1
            block = [lines[i][m.end():]] + [lines[k] for k in range(i + 1, j)]
            desc_map[code] = '\n'.join(block).rstrip().rstrip('"')
            i = j - 1
        i += 1
    return name_map, desc_map

def update_desc_in_skillstr(sid, new_desc):
    """把技能 sid 的描述文字写回 skillstr.txt（UTF-16LE，保持颜色标记/CRLF）。
    skillstr.txt 即 configs.pck 里 skillsrt 的明文提取；改完需用完美 pck 工具打包回 configs.pck 才在游戏内生效。"""
    path = SKILLSTR
    if not os.path.exists(path):
        raise FileNotFoundError('skillstr.txt 不存在: %s' % path)
    b = open(path, 'rb').read()
    text = b.decode('utf-16-le')
    if text and text[0] == '\ufeff':
        text = text[1:]
    lines = text.split('\n')
    idx = sid * 10 + 1
    prefix = '%d "' % idx
    i = next((k for k, l in enumerate(lines) if l.startswith(prefix)), None)
    if i is None:
        raise ValueError('skillstr 找不到技能 %d 的描述块(idx=%d)' % (sid, idx))
    j = i
    while j < len(lines):
        if j > i and lines[j].rstrip('\r').endswith('"'):
            break
        j += 1
    nd = (new_desc or '').replace('\r\n', '\n').replace('\r', '\n')
    nl = nd.split('\n')
    if not nl or nl == ['']:
        nl = ['']
    block = ['%d "%s' % (idx, nl[0])]
    if len(nl) > 1:
        block.extend(nl[1:-1])
        block.append(nl[-1] + '"')
    else:
        block[0] = block[0] + '"'
    lines[i:j+1] = block
    out = ('\ufeff' + '\n'.join(lines)).encode('utf-16-le')
    # 先备份原文本
    try:
        bakp = os.path.join(BACKUP_DIR, 'skillstr.%s.bak' % time.strftime('%Y%m%d_%H%M%S'))
        shutil.copy2(path, bakp)
    except Exception:
        bakp = None
    open(path, 'wb').write(out)
    global desc_map
    desc_map[sid] = new_desc
    return bakp

name_map, desc_map = parse_skillstr(SKILLSTR)

# 每级伤害/效果表（由 build_perlevel.py 预计算；build time 用 capstone，运行时仅读 JSON）
PERLEVEL = {}
_PL = os.path.join(BASE, 'assets', 'skill_perlevel.json')
if os.path.exists(_PL):
    try:
        PERLEVEL = json.load(open(_PL, encoding='utf-8'))
    except Exception:
        PERLEVEL = {}

def _weapon(desc):
    if not desc: return '无/通用'
    m = re.search(r'武器限制\s*(\^[0-9a-fA-F]{6})?\s*([^\n^]+)', desc)
    w = m.group(2).strip() if m else ''
    w = w.replace('，', '、').replace(',', '、').strip()
    return w if w and w != '无' else '无/通用'

def _skill_list(elf):
    ids = sorted({int(m.group(1)) for nm in elf.symtab if (m := re.search(r'Skill(\d+)Stub', nm))})
    return [{'id': sid, 'name': name_map.get(sid, ''), 'weapon': _weapon(desc_map.get(sid, ''))} for sid in ids]


_CURRENT_ELF = None
_CURRENT_NAME = ''


# ======================= HTTP 服务（标准库） =======================
# static 与 server.py 同目录（项目根 / skill/scripts 两处皆然），用 _HERE 定位，不随 BASE 漂移。
STATIC_DIR = os.path.join(_HERE, 'static')

def _send_json(handler, obj, status=200):
    body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


# 攻速上限：property_policy::UpdateAttack 内的夹取 0x12c=300
# cmp dword [ebp-0x2c], imm32 = 81 7D D4 imm32  （比较）
# mov dword [ebp-0x2c], imm32 = C7 45 D4 imm32  （赋值）
def locate_attackspeed(elf):
    cand = None
    for nm in elf.symtab:
        if nm.startswith('_ZN15property_policy') and 'UpdateAttack' in nm:
            cand = nm; break
        if 'UpdateAttack' in nm and ('property' in nm or 'util' in nm):
            cand = nm; break
    if not cand: return None
    for v, s, sh in elf.sym(cand):
        sec = elf.sections[sh] if sh < len(elf.sections) else None
        if not sec or sec['name'] != '.text': continue
        foff = sec['offset'] + (v - sec['addr'])
        code = elf.data[foff:foff+s]
        cur = None; patches = []
        for m in re.finditer(re.escape(b'\x81\x7d\xd4'), code):
            imm = struct.unpack_from('<I', code, m.start()+3)[0]
            cur = imm; patches.append(('cmp', foff+m.start()+3, imm))
        for m in re.finditer(re.escape(b'\xc7\x45\xd4'), code):
            imm = struct.unpack_from('<I', code, m.start()+3)[0]
            patches.append(('mov', foff+m.start()+3, imm))
        if cur is None: return None
        cap_offsets = [{'role': r, 'off': o} for r, o, _ in patches if _ == cur]
        return {'func': cand, 'va': v, 'current': cur, 'cap_offsets': cap_offsets}
    return None

def _route(handler, path, method, body):
    global _CURRENT_ELF, _CURRENT_NAME
    if method == 'GET':
        if path == '/':
            try:
                data = open(os.path.join(STATIC_DIR, 'index.html'), 'rb').read()
            except Exception:
                _send_json(handler, {'error': '前端页面缺失'}, 500); return
            handler.send_response(200)
            handler.send_header('Content-Type', 'text/html; charset=utf-8')
            handler.send_header('Content-Length', str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return
        if path == '/attackspeed':
            try:
                data = open(os.path.join(STATIC_DIR, 'attackspeed.html'), 'rb').read()
            except Exception:
                _send_json(handler, {'error': '前端页面缺失'}, 500); return
            handler.send_response(200)
            handler.send_header('Content-Type', 'text/html; charset=utf-8')
            handler.send_header('Content-Length', str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return
        if path == '/batch':
            try:
                data = open(os.path.join(STATIC_DIR, 'batch.html'), 'rb').read()
            except Exception:
                _send_json(handler, {'error': '前端页面缺失'}, 500); return
            handler.send_response(200)
            handler.send_header('Content-Type', 'text/html; charset=utf-8')
            handler.send_header('Content-Length', str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return
        if path == '/api/attackspeed':
            if _CURRENT_ELF is None:
                _send_json(handler, {'error': '尚未读取 gs'}); return
            info = locate_attackspeed(_CURRENT_ELF)
            if not info:
                _send_json(handler, {'error': '未找到攻速限制函数 property_policy::UpdateAttack'}); return
            cur = info['current']
            _send_json(handler, {**info,
                                 'interval': int(cur*0.8)-1,
                                 'interval_fn': f'floor(攻速×0.8)−1 = floor({cur}×0.8)−1 = {int(cur*0.8)-1}',
                                 'note': '攻速值=每秒攻击次数(夹取[5,300])；出手间隔 obj+0x264=floor(攻速×0.8)−1。'})
            return
        m = re.match(r'/api/skill/(\d+)', path)
        if m:
            sid = int(m.group(1))
            if _CURRENT_ELF is None:
                _send_json(handler, {'error': '尚未读取 gs'}); return
            data = load_skill(_CURRENT_ELF, sid)
            groups = {}
            for cat, mlist in data.items():
                items = []
                for nm, va, size, consts in mlist:
                    for c in consts:
                        desc, off, ln, cur, kind, shared, ro = c
                        if kind == 'array':
                            items.append({'off': off, 'len': ln, 'kind': kind,
                                          'cur': ','.join(map(str, cur)), 'vals': list(cur),
                                          'shared': shared, 'desc': desc, 'method': nm.split('Stub')[-1]})
                        else:
                            items.append({'off': off, 'len': ln, 'kind': kind,
                                          'cur': f'{cur:.8g}' if isinstance(cur, float) else str(cur),
                                          'shared': shared, 'desc': desc, 'method': nm.split('Stub')[-1]})
                groups[cat] = items
            # 真/狂伤害标注（来自 manifest；无 manifest 时为 None，不阻塞）
            damage_note = None
            if BE is not None:
                _man = BE.try_load_manifest()
                if _man:
                    _sk = next((s for s in _man.get('skills', []) if s.get('skill_id') == sid), None)
                    if _sk:
                        damage_note = {
                            'variant': _sk.get('variant'), 'base_skill_id': _sk.get('base_skill_id'),
                            'analysis': _sk.get('damage_analysis'), 'note': _sk.get('note', ''),
                            'constants': _sk.get('damage_constants', []),
                        }
            _send_json(handler, {'id': sid, 'name': name_map.get(sid, ''),
                                 'weapon': _weapon(desc_map.get(sid, '')),
                                 'desc': desc_map.get(sid, ''), 'groups': groups,
                                 'perlevel': PERLEVEL.get(str(sid), {}),
                                 'damage_note': damage_note})
            return
    if method == 'POST':
        if path == '/api/load':
            if len(body) < 4 or body[:4] != b'\x7fELF':
                _send_json(handler, {'error': '选择的不是 ELF/gs 文件'}); return
            try:
                elf = ELF(body)
                if not elf.symtab:
                    _send_json(handler, {'error': '该 gs 是剥离版(无符号表)，无法定位技能。请选择带符号表的完整版 gs。'}); return
            except Exception as e:
                _send_json(handler, {'error': f'解析失败: {e}'}); return
            open(CURRENT_GS, 'wb').write(body)
            _CURRENT_ELF = elf
            _CURRENT_NAME = 'gs'
            skills = _skill_list(elf)
            _send_json(handler, {'filename': 'gs', 'count': len(skills), 'skills': skills})
            return
        if path == '/api/desc':
            try:
                payload = json.loads(body.decode('utf-8'))
                sid = int(payload['sid'])
                desc = payload['desc']
            except Exception:
                _send_json(handler, {'error': '请求格式错误，需 JSON {sid, desc}'}, 400); return
            try:
                bakp = update_desc_in_skillstr(sid, desc)
            except Exception as e:
                _send_json(handler, {'error': str(e)}, 400); return
            _send_json(handler, {'ok': True, 'sid': sid, 'name': name_map.get(sid, ''),
                                 'backup': bakp,
                                 'note': '已写回 skillstr.txt（明文）。请用完美 pck 工具把 skillsrt 打包回 configs.pck，游戏内描述即更新。'})
            return
        if path == '/api/save':
            if _CURRENT_ELF is None:
                _send_json(handler, {'error': '尚未读取 gs'}); return
            try:
                payload = json.loads(body.decode('utf-8'))
            except Exception:
                _send_json(handler, {'error': '请求格式错误'}); return
            edits = payload.get('edits', [])
            if not edits:
                _send_json(handler, {'error': '没有要保存的修改'}); return
            data = bytearray(open(CURRENT_GS, 'rb').read())
            ts = time.strftime('%Y%m%d_%H%M%S')
            bak = os.path.join(BACKUP_DIR, f'{_CURRENT_NAME}.{ts}.bak')
            shutil.copy2(CURRENT_GS, bak)
            applied = []
            for e in edits:
                off, kind = int(e['off']), e['kind']
                val = e['val']
                try:
                    if kind == 'int': nb = struct.pack('<I', int(val, 0) & 0xFFFFFFFF)
                    elif kind == 'double': nb = struct.pack('<d', float(val))
                    elif kind == 'float': nb = struct.pack('<f', float(val))
                    elif kind == 'array': nb = struct.pack('<10i', *[int(x, 0) for x in str(val).split(',')])
                    else: continue
                except Exception as ex:
                    _send_json(handler, {'error': f'偏移0x{off:x}: 值无效 ({ex})'}, 400); return
                old = bytes(data[off:off+len(nb)])
                data[off:off+len(nb)] = nb
                applied.append({'off': off, 'old': old.hex(), 'new': nb.hex(), 'kind': kind})
            open(CURRENT_GS, 'wb').write(data)
            _CURRENT_ELF = ELF(open(CURRENT_GS, 'rb').read())
            out = open(CURRENT_GS, 'rb').read()
            verified = all(out[a['off']:a['off']+len(bytes.fromhex(a['new']))] == bytes.fromhex(a['new']) for a in applied)
            _send_json(handler, {'backup': bak, 'applied': len(applied),
                                 'current_gs': CURRENT_GS, 'verified': verified,
                                 'note': '改好的 gs 已保存，可部署到服务端 pwserver\\gamed\\gs（先备份原文件）。'})
            return
        if path == '/api/attackspeed':
            if _CURRENT_ELF is None:
                _send_json(handler, {'error': '尚未读取 gs'}); return
            try:
                payload = json.loads(body.decode('utf-8'))
                val = int(payload.get('value'))
            except Exception:
                _send_json(handler, {'error': '请求格式错误，需 JSON {value:整数}'}, 400); return
            if not (1 <= val <= 100000):
                _send_json(handler, {'error': '攻速值需在 1~100000 之间'}, 400); return
            info = locate_attackspeed(_CURRENT_ELF)
            if not info:
                _send_json(handler, {'error': '未找到攻速限制函数'}, 400); return
            data = bytearray(open(CURRENT_GS, 'rb').read())
            ts = time.strftime('%Y%m%d_%H%M%S')
            bak = os.path.join(BACKUP_DIR, f'{_CURRENT_NAME}.{ts}.bak')
            shutil.copy2(CURRENT_GS, bak)
            nb = struct.pack('<I', val)
            for cap in info['cap_offsets']:
                off = cap['off']
                data[off:off+4] = nb
            open(CURRENT_GS, 'wb').write(data)
            _CURRENT_ELF = ELF(open(CURRENT_GS, 'rb').read())
            out = open(CURRENT_GS, 'rb').read()
            verified = all(out[c['off']:c['off']+4] == nb for c in info['cap_offsets'])
            _send_json(handler, {'backup': bak, 'applied': len(info['cap_offsets']),
                                 'old': info['current'], 'new': val, 'verified': verified,
                                 'current_gs': CURRENT_GS,
                                 'note': '攻速上限已设为 %d 次/秒；改好的 gs 部署到服务端 pwserver\\gamed\\gs（先备份原文件）。' % val})
            return
        if path == '/api/batch-modify':
            if _CURRENT_ELF is None:
                _send_json(handler, {'error': '尚未读取 gs'}); return
            if BE is None:
                _send_json(handler, {'error': '批量引擎 batch_engine 未加载（项目目录缺 batch_engine.py）'}); return
            try:
                payload = json.loads(body.decode('utf-8'))
                scope = payload.get('scope')
                ratio = float(payload.get('ratio'))
                mode = payload.get('mode', 'preview')
                skills_in = payload.get('skills', [])
                set_mode = payload.get('set_mode', 'custom')
            except Exception:
                _send_json(handler, {'error': '请求格式错误，需 JSON {scope,ratio,skills,mode=preview|apply}'}, 400); return
            if scope not in BE.SCOPES:
                _send_json(handler, {'error': 'scope 须为 %s' % '/'.join(BE.SCOPES)}, 400); return
            if ratio <= 0:
                _send_json(handler, {'error': 'ratio 须 >0'}, 400); return
            try:
                explicit = [int(x) for x in skills_in] if isinstance(skills_in, list) else []
                sids = BE.resolve_skill_ids(_CURRENT_ELF, name_map, set_mode, explicit)
                manifest = BE.load_manifest() if scope == 'damage' else None
                rows = BE.plan_batch(_CURRENT_ELF, name_map, scope, ratio, sids, manifest,
                                     include_amounts=bool(payload.get('include_amounts')))
            except BE.BatchError as e:
                _send_json(handler, {'error': str(e)}, 400); return
            except Exception as e:
                _send_json(handler, {'error': '批量计划失败: %s' % e}, 400); return
            res = {'scope': scope, 'scope_label': BE.SCOPE_LABEL.get(scope, ''), 'ratio': ratio,
                   'n_skills': len(sids), 'set_mode': set_mode, 'rows': rows,
                   'n_apply': sum(1 for r in rows if r['status'] == 'apply'),
                   'n_shared': sum(1 for r in rows if r['status'] == 'skip_shared'),
                   'n_no_method': sum(1 for r in rows if r['status'] == 'skip_no_method'),
                   'n_role': sum(1 for r in rows if r['status'] == 'skip_role'),
                   'n_guard': sum(1 for r in rows if r['status'] == 'skip_guard'),
                   'n_error': sum(1 for r in rows if r['status'] == 'skip_error')}
            if mode == 'apply':
                if res['n_apply'] == 0:
                    _send_json(handler, {**res, 'error': '没有可应用的修改（全为跳过项），未写文件。'}); return
                try:
                    out = BE.apply_batch(rows, CURRENT_GS, 'batch_%s' % scope)
                except BE.BatchError as e:
                    _send_json(handler, {'error': str(e)}, 400); return
                _CURRENT_ELF = ELF(open(CURRENT_GS, 'rb').read())
                res.update({'backup': out['bak'], 'applied': out['n_apply'],
                            'verified': out['verified'], 'current_gs': CURRENT_GS,
                            'note': '改好的 gs 部署到服务端 pwserver\\gamed\\gs（先备份原文件）。'})
            _send_json(handler, res)
            return
    _send_json(handler, {'error': '404'}, 404)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        _route(self, urlparse(self.path).path, 'GET', b'')
    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length)
        _route(self, urlparse(self.path).path, 'POST', body)
    def log_message(self, *a):
        pass


if __name__ == '__main__':
    print('技能修改器 后端已启动（纯标准库，离线可用）')
    print('浏览器打开: http://127.0.0.1:7860')
    srv = HTTPServer(('127.0.0.1', 7860), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
