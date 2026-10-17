#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_engine.py  批量按比例修改技能参数引擎（server.py 与 gs_tool.py 共用）
====================================================================================
作用域 → gs 方法映射（全部经 .symtab 反汇编实证，见 data/CHANGELOG.md）：

  damage    伤害      → data/skill_damage_manifest.json 中该技能 damage_constants
                        里 shared=false 的条目（float/int，乘法）。
  cooldown  冷却      → Skill{ID}Stub::GetCoolingtime 自有立即数（int，毫秒）。
  cast      吟唱准备  → Skill{ID}Stub::GetExecutetime 自有立即数（int，毫秒）。
  release   释放时间  → Skill{ID}Stub::GetAttackspeed 自有立即数（int，攻速=每秒
                        攻击次数；出手间隔 obj+0x264 = floor(攻速*0.8)-1）。

【释放时间实证结论（2026-10-17，基准 gs.sword_to_zhy.20261017_174801.bak）】
  全 .symtab 搜索：GetReleaseTime=0 个符号，GetCastTime=0 个符号；
  技能 Stub 内时序方法族仅有：
    GetCoolingtime x1948（冷却，毫秒）
    GetExecutetime x1948（施放/准备，毫秒；剑刃风暴1728=0 即瞬发）
    State1GetTime x1764 / State2 x1626 / State3 x1626 / State4 x11（状态持续时间，与释放无关）
    GetAttackspeed x1077（攻击速度=每秒攻击次数；剑刃风暴=9，箭阵=30）
  全局仅 PlayerTaskInterface::CastSkill / gpet_policy::AddAutoCastSkill（任务调度，非技能常量）。
  结论：gs 不存在独立于 GetExecutetime 的"释放时间"方法。技能出手/释放节奏由
        GetAttackspeed 决定（出手间隔），故 release 作用域映射到 GetAttackspeed。
        注意：对瞬发技能（GetExecutetime=0），cast 作用域缩放无效果（0*ratio=0）。

数值安全：
  int  → round(old*ratio)，须 >0 且落在 int32(0..0x7fffffff)；
  float/double → old*ratio，须有限（非 NaN/Inf）。
共享常量（refs>1 / shared=true）一律跳过并列出；无自有方法（继承默认）的技能跳过。
"""
import os, json, struct, time, shutil

# 可移植：本脚本可能放在项目根（data/ 同级）或 skill 的 scripts/（data/ 在上级）。
# 依次查找：脚本同级 data/ → 上级 data/ → 上级 assets/。
_HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = None
for _cand in (os.path.join(_HERE, 'data', 'skill_damage_manifest.json'),
              os.path.join(_HERE, '..', 'data', 'skill_damage_manifest.json'),
              os.path.join(_HERE, '..', 'assets', 'skill_damage_manifest.json')):
    if os.path.exists(_cand):
        MANIFEST_PATH = _cand
        break
if MANIFEST_PATH is None:
    MANIFEST_PATH = os.path.join(_HERE, 'data', 'skill_damage_manifest.json')

SCOPE_LABEL = {
    'damage': '伤害',
    'cooldown': '冷却',
    'cast': '吟唱/准备',
    'release': '释放/出手间隔',
}
# cooldown/cast/release → 技能 Stub 方法名关键字
SCOPE_METHOD_KEY = {
    'cooldown': 'GetCoolingtime',
    'cast': 'GetExecutetime',
    'release': 'GetAttackspeed',
}
SCOPES = ('damage', 'cooldown', 'cast', 'release')

# 批量"伤害"作用域角色过滤（经 manifest 角色体系实证 + 测试确认）：
#   默认只乘 role == 主伤害（SetDecmp/SetPlus 直接伤害）。
#   --include-amounts 追加 role == 效果数值（SetAmount 伤害量）。
#   永不乘：持续时间(SetTime)/表现(SetPerform/SetShowicon)/标记(SetPray/SetCombat)/
#           debuff/系数(SetRatio)/共享-不可单独改。
# 说明：StateAttack 里的 SetRatio(系数，如 8.0=跳动间隔比) 与 SetAmount(效果数值)
#   属状态参数而非主伤害公式系数，乘比例会破坏技能（改变跳动频率/附加量），故默认不乘。
DAMAGE_ROLE_DEFAULT = {'主伤害'}
DAMAGE_ROLE_OPTIONAL = {'效果数值'}   # 需 --include-amounts
DAMAGE_ROLE_NEVER = {'持续时间', '表现', '标记', 'debuff', '系数', '共享-不可单独改'}


class BatchError(Exception):
    pass


# ----------------------- manifest -----------------------
def load_manifest(path=MANIFEST_PATH):
    if not os.path.exists(path):
        raise BatchError(
            '伤害作用域需要 manifest 文件 %s，但不存在。\n'
            '生成途径：运行伤害剖析并行任务，产出 data/skill_damage_manifest.json '
            '（schema_version=1）。' % path)
    try:
        m = json.load(open(path, encoding='utf-8'))
    except Exception as e:
        raise BatchError('manifest 解析失败: %s' % e)
    if m.get('schema_version') != 1:
        raise BatchError('manifest schema_version=%r，本引擎要求 1。请重新生成。' % m.get('schema_version'))
    if not isinstance(m.get('skills'), list):
        raise BatchError('manifest 缺少 skills 数组，schema 不匹配。')
    return m


def try_load_manifest(path=MANIFEST_PATH):
    """可选加载（用于标注显示）；失败返回 None，不抛错。"""
    try:
        return load_manifest(path)
    except Exception:
        return None


# ----------------------- 字节扫描取方法常量（与 server.py 同算法） -----------------------
def _count_refs(elf, va):
    t = elf.sec_by_name('.text')
    return elf.data[t['offset']:t['offset']+t['size']].count(struct.pack('<I', va))


def extract_method_constants(elf, value, size, shndx):
    if shndx == 0: return []
    sec = elf.sections[shndx] if shndx < len(elf.sections) else None
    if sec is None or sec['name'] != '.text': return []
    foff = sec['offset'] + (value - sec['addr'])
    code = elf.data[foff:foff+size]
    res = []
    i = 0; n = len(code)
    while i < n:
        b = code[i]
        if 0xB8 <= b <= 0xBF and i+5 <= n:
            imm = struct.unpack_from('<I', code, i+1)[0]
            ok = imm < 0x200000 or any(s['addr'] and s['addr'] <= imm < s['addr']+s['size'] for s in elf.sections)
            exp = (imm & 0x7f800000) >> 23
            is_fbits = exp not in (0, 0xff) and not ok
            if is_fbits:
                fval = struct.unpack('<f', struct.pack('<I', imm))[0]
                if 1e-10 <= abs(fval) < 1e15:
                    res.append(('浮点参数(imm)', foff+i+1, 4, fval, 'float', False, None))
            elif ok:
                res.append(('mov立即数', foff+i+1, 4, imm, 'int', False, None))
            i += 5; continue
        if b == 0xDD and i+1 < n and code[i+1] == 0x05 and i+6 <= n:
            disp = struct.unpack_from('<I', code, i+2)[0]
            ro = _va2off(elf, disp)
            if ro and ro[1] == '.rodata':
                cur = struct.unpack('<d', elf.data[ro[0]:ro[0]+8])[0]
                res.append(('浮点double', ro[0], 8, cur, 'double', _count_refs(elf, disp) > 1, disp))
            i += 6; continue
        if b == 0xD9 and i+1 < n and code[i+1] == 0x05 and i+6 <= n:
            disp = struct.unpack_from('<I', code, i+2)[0]
            ro = _va2off(elf, disp)
            if ro and ro[1] == '.rodata':
                cur = struct.unpack('<f', elf.data[ro[0]:ro[0]+4])[0]
                res.append(('浮点float', ro[0], 4, cur, 'float', _count_refs(elf, disp) > 1, disp))
            i += 6; continue
        i += 1
    return res


def _va2off(elf, va):
    for s in elf.sections:
        if s['addr'] and s['addr'] <= va < s['addr']+s['size']:
            return (s['offset'] + (va - s['addr']), s['name'])
    return None


# ----------------------- 缩放 -----------------------
def scale_value(old, kind, ratio):
    if kind in ('float', 'double'):
        v = float(old) * ratio
        if v != v or v in (float('inf'), float('-inf')):
            raise BatchError('缩放后非有限值: %r * %r' % (old, ratio))
        return v
    # int
    v = int(round(float(old) * ratio))
    if v <= 0:
        raise BatchError('缩放后须 >0: %r * %r = %r' % (old, ratio, v))
    if v > 0x7FFFFFFF:
        raise BatchError('超出 int32: %r * %r = %r' % (old, ratio, v))
    return v


# ----------------------- 计划（dry-run 与 apply 共用） -----------------------
def _read_current(elf, off, kind):
    """从目标 gs 当前字节读出值（用于旧值匹配守卫）。返回 (value, ok)。"""
    try:
        if kind == 'float':
            return struct.unpack('<f', elf.data[off:off+4])[0], True
        if kind == 'double':
            return struct.unpack('<d', elf.data[off:off+8])[0], True
        if kind in ('int', 'push_int'):
            return struct.unpack('<I', elf.data[off:off+4])[0], True
    except Exception:
        return None, False
    return None, False


def _values_match(a, b, kind):
    if kind in ('float', 'double'):
        try:
            return abs(float(a) - float(b)) <= max(1e-6, abs(float(b))*1e-6)
        except Exception:
            return False
    return int(a) == int(b)


def plan_batch(elf, name_map, scope, ratio, skill_ids, manifest=None, include_amounts=False):
    """返回 rows: list[dict]。每项:
       sid,name,method,off,kind,old,new,status,note,role
       status: apply / skip_shared / skip_no_method / skip_error / skip_role / skip_guard
    """
    scale_roles = set(DAMAGE_ROLE_DEFAULT)
    if include_amounts:
        scale_roles |= DAMAGE_ROLE_OPTIONAL
    rows = []
    if scope == 'damage':
        if manifest is None:
            manifest = load_manifest()
        mskills = {s['skill_id']: s for s in manifest.get('skills', [])}
        for sid in skill_ids:
            sk = mskills.get(sid)
            if sk is None:
                rows.append(dict(sid=sid, name=name_map.get(sid, ''), method='-', off=-1,
                                 kind='-', old='-', new='-', status='skip_error', role='-',
                                 note='技能不在 damage manifest 中（可能非真/狂伤害技能），按契约跳过'))
                continue
            dcs = sk.get('damage_constants', [])
            if not dcs:
                rows.append(dict(sid=sid, name=sk.get('name', ''), method='-', off=-1,
                                 kind='-', old='-', new='-', status='skip_no_method', role='-',
                                 note='manifest 中无 damage_constants（analysis=%s）' % sk.get('damage_analysis')))
                continue
            for dc in dcs:
                off = dc['file_off']
                off = int(off, 16) if isinstance(off, str) else int(off)
                kind = dc.get('kind', 'float')
                old = dc.get('value')
                role = dc.get('role', '')
                # 共享常量一律跳过
                if dc.get('shared'):
                    rows.append(dict(sid=sid, name=sk.get('name', ''), method=dc.get('method', ''),
                                     off=off, kind=kind, old=old, new='-', status='skip_shared', role=role,
                                     note='共享常量 refs=%s，不可单独改（%s）' % (dc.get('refs'), role)))
                    continue
                # 角色过滤：永不乘 / 不在缩放集
                if role in DAMAGE_ROLE_NEVER:
                    rows.append(dict(sid=sid, name=sk.get('name', ''), method=dc.get('method', ''),
                                     off=off, kind=kind, old=old, new='-', status='skip_role', role=role,
                                     note='role=%s 永不乘（%s），跳过' % (role, dc.get('setter', ''))))
                    continue
                if role not in scale_roles:
                    rows.append(dict(sid=sid, name=sk.get('name', ''), method=dc.get('method', ''),
                                     off=off, kind=kind, old=old, new='-', status='skip_role', role=role,
                                     note='role=%s 不在默认缩放集（--include-amounts 可含效果数值），跳过' % role))
                    continue
                # 旧值匹配守卫：读目标 gs 当前字节，与 manifest 旧值比对
                cur_val, ok = _read_current(elf, off, kind)
                if not ok or not _values_match(cur_val, old, kind):
                    rows.append(dict(sid=sid, name=sk.get('name', ''), method=dc.get('method', ''),
                                     off=off, kind=kind, old=old, new='-', status='skip_guard', role=role,
                                     note='偏移处字节与 manifest 不符（当前=%r, manifest=%r），跳过不写' % (cur_val, old)))
                    continue
                try:
                    new = scale_value(old, kind, ratio)
                except BatchError as e:
                    rows.append(dict(sid=sid, name=sk.get('name', ''), method=dc.get('method', ''),
                                     off=off, kind=kind, old=old, new='-', status='skip_error', role=role, note=str(e)))
                    continue
                rows.append(dict(sid=sid, name=sk.get('name', ''), method=dc.get('method', ''),
                                 off=off, kind=kind, old=old, new=new, status='apply', role=role,
                                 note=dc.get('setter', '')))
        return rows

    # cooldown / cast / release
    key = SCOPE_METHOD_KEY[scope]
    for sid in skill_ids:
        marker = 'Skill%dStub' % sid
        methods = [nm for nm in elf.symtab if marker in nm and key in nm]
        if not methods:
            rows.append(dict(sid=sid, name=name_map.get(sid, ''), method='-', off=-1,
                             kind='-', old='-', new='-', status='skip_no_method', role='-',
                             note='无自有 %s 方法（继承默认），跳过' % key))
            continue
        sid_rows_before = len(rows)
        for nm in methods:
            for value, size, shndx in elf.sym(nm):
                if shndx == 0: continue
                sec = elf.sections[shndx] if shndx < len(elf.sections) else None
                if not sec or sec['name'] != '.text': continue
                consts = extract_method_constants(elf, value, size, shndx)
                for c in consts:
                    desc, off, ln, cur, kind, shared, ro = c
                    if kind in ('float_const', 'array'):
                        continue
                    if shared:
                        rows.append(dict(sid=sid, name=name_map.get(sid, ''), method=nm.split('Stub')[-1],
                                         off=off, kind=kind, old=cur, new='-', status='skip_shared', role='-',
                                         note='共享常量（rodata refs>1），不可单独改'))
                        continue
                    try:
                        new = scale_value(cur, kind, ratio)
                    except BatchError as e:
                        rows.append(dict(sid=sid, name=name_map.get(sid, ''), method=nm.split('Stub')[-1],
                                         off=off, kind=kind, old=cur, new='-', status='skip_error', role='-', note=str(e)))
                        continue
                    rows.append(dict(sid=sid, name=name_map.get(sid, ''), method=nm.split('Stub')[-1],
                                     off=off, kind=kind, old=cur, new=new, status='apply', role='-', note=''))
        # 自有方法存在但未产出任何行（如方法返回 -1/0 哨兵，无可改常量）
        if len(rows) == sid_rows_before:
            rows.append(dict(sid=sid, name=name_map.get(sid, ''), method=methods[0].split('Stub')[-1],
                             off=-1, kind='-', old='-', new='-', status='skip_no_method', role='-',
                             note='自有 %s 方法存在但无可提取常量（多为返回 -1/0 哨兵=用默认值），跳过' % key))
    return rows


# ----------------------- 应用（备份 + 写回 + 回读校验） -----------------------
def encode(kind, new):
    if kind == 'int': return struct.pack('<I', int(new))
    if kind == 'double': return struct.pack('<d', float(new))
    if kind == 'float': return struct.pack('<f', float(new))
    raise BatchError('不可改类型: %s' % kind)


def apply_batch(rows, gs_path, bak_note):
    """备份 gs_path → 写 apply 行 → 回读校验。返回 dict(bak, applied, verified, diffs)。"""
    if not os.path.exists(gs_path):
        raise BatchError('gs 文件不存在: %s' % gs_path)
    apply_rows = [r for r in rows if r['status'] == 'apply']
    data = bytearray(open(gs_path, 'rb').read())
    # 备份到 <gs 所在目录>/backup/gs.<note>.<ts>.bak
    gs_dir = os.path.dirname(gs_path)
    bak_dir = os.path.join(gs_dir, 'backup')
    os.makedirs(bak_dir, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    bak = os.path.join(bak_dir, 'gs.%s.%s.bak' % (bak_note, ts))
    shutil.copy2(gs_path, bak)
    applied = []
    for r in apply_rows:
        nb = encode(r['kind'], r['new'])
        old = bytes(data[r['off']:r['off']+len(nb)])
        data[r['off']:r['off']+len(nb)] = nb
        applied.append(dict(off=r['off'], kind=r['kind'], old=old.hex(), new=nb.hex(),
                            sid=r['sid'], oldv=r['old'], newv=r['new']))
    open(gs_path, 'wb').write(data)
    # 回读校验
    out = open(gs_path, 'rb').read()
    verified = True
    for a in applied:
        want = bytes.fromhex(a['new'])
        if out[a['off']:a['off']+len(want)] != want:
            verified = False
    return dict(bak=bak, applied=applied, verified=verified,
                n_apply=len(apply_rows),
                n_shared=sum(1 for r in rows if r['status'] == 'skip_shared'),
                n_no_method=sum(1 for r in rows if r['status'] == 'skip_no_method'),
                n_role=sum(1 for r in rows if r['status'] == 'skip_role'),
                n_guard=sum(1 for r in rows if r['status'] == 'skip_guard'),
                n_error=sum(1 for r in rows if r['status'] == 'skip_error'))


def resolve_skill_ids(elf, name_map, mode, explicit):
    """mode: 'custom'|'zhen'|'kuang'|'all'。返回 sorted list[int]。"""
    if mode == 'custom':
        return sorted(set(explicit or []))
    if mode == 'all':
        return sorted({int(m.group(1)) for nm in elf.symtab if (m := _re_skill(nm))})
    if mode == 'zhen':
        return sorted(sid for sid, nm in name_map.items() if nm.startswith('真·'))
    if mode == 'kuang':
        return sorted(sid for sid, nm in name_map.items() if nm.startswith('狂·'))
    raise BatchError('未知模式: %s' % mode)


import re as _re
def _re_skill(nm):
    return _re.search(r'Skill(\d+)Stub', nm)
