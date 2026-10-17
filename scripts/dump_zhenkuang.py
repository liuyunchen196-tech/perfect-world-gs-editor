#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dump_zhenkuang.py  —— 只读分析（绝不修改任何 gs 文件）
=================================================================
枚举全部 601 个 真·/狂· 技能（真301 + 狂300），对 GNET::Skill{code}Stub 的每个
.text 具名方法做 capstone 反汇编，提取全部常量并解析 call 目标符号。
输出 data/zhenkuang_dump.json。

基准 gs = 干净138只读参考（gs.sword_to_zhy.20261017_174801.bak）。
"""
import importlib.util, re, json, struct, time, os, sys, argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
# server.py 与本脚本同目录（项目根或 skill/scripts/）
spec = importlib.util.spec_from_file_location('srv', os.path.join(_HERE, 'server.py'))
srv = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)

from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from capstone.x86 import X86_OP_IMM, X86_OP_MEM

_parser = argparse.ArgumentParser(description='枚举全部真·/狂·技能并 dump 常量/调用')
_parser.add_argument('--gs', required=True, help='带符号表的完整版服务端 gs 路径')
_parser.add_argument('--out', default=os.path.join(_HERE, 'data', 'zhenkuang_dump.json'),
                     help='输出 dump JSON 路径')
_parser.add_argument('only', nargs='*', type=int, help='(可选) 只处理指定技能ID')
_args = _parser.parse_args()
BASE_GS = _args.gs
OUT = _args.out
ONLY = set(_args.only) if _args.only else None

t_start = time.time()
elf = srv.ELF(open(BASE_GS, 'rb').read())
md = Cs(CS_ARCH_X86, CS_MODE_32); md.detail = True

# ---------- va -> 符号 精确索引 ----------
va2sym = {}
for nm, lst in elf.symtab.items():
    for v, sz, sh in lst:
        if v and v not in va2sym:
            va2sym[v] = nm

def sym_at(va):
    return va2sym.get(va)

def short_sym(mangled):
    """把 _ZN...Skill602Stub6State19CalculateEPNS_5SkillE 缩短为 State19Calculate。"""
    i = mangled.find('Stub')
    rest = mangled[i+4:] if i >= 0 else mangled
    changed = True
    while changed:
        changed = False
        for suf in ('EPNS_13PlayerWrapperE', 'EPNS_5SkillE', 'E5array', 'Ev', 'E'):
            if rest.endswith(suf):
                rest = rest[:-len(suf)]; changed = True; break
    rest = rest.lstrip('0123456789')
    return rest

# ---------- 提取 真·/狂· 技能 ----------
def extract_zhenkuang():
    path = srv.SKILLSTR
    det = srv._detect_encoding(path)
    text = open(path, encoding=det, errors='replace').read()
    if text and text[0] == '\ufeff': text = text[1:]
    lines = text.split('\n')
    LINE = re.compile(r'^(\d+)\s+"(.*)$')
    out = []
    for ln in lines:
        m = LINE.match(ln)
        if not m: continue
        idx = int(m.group(1))
        if idx % 2 != 0: continue
        name = m.group(2).strip().rstrip('"').strip()
        code = idx // 10
        if name.startswith('真·'):
            out.append((code, name, 'zhen', idx))
        elif name.startswith('狂·'):
            out.append((code, name, 'kuang', idx))
    out.sort(key=lambda x: x[0])
    return out

SKILLS = extract_zhenkuang()

# 普通技能名 -> code（从原始偶数idx行构建，避免 parse_skillstr 吞行）
def extract_plain_names():
    path = srv.SKILLSTR
    det = srv._detect_encoding(path)
    text = open(path, encoding=det, errors='replace').read()
    if text and text[0] == '\ufeff': text = text[1:]
    LINE = re.compile(r'^(\d+)\s+"(.*)$')
    m = {}
    for ln in text.split('\n'):
        mm = LINE.match(ln)
        if not mm: continue
        idx = int(mm.group(1))
        if idx % 2 != 0: continue
        name = mm.group(2).strip().rstrip('"').strip()
        m.setdefault(name, idx // 10)
    return m

_plain_name = extract_plain_names()

def base_of(code, name, variant):
    base_name = name[2:]  # 去掉 真·/狂·
    return _plain_name.get(base_name)


# ---------- 浮点位模式判断 ----------
def is_float_bits(imm):
    exp = (imm & 0x7f800000) >> 23
    if exp in (0, 0xff):
        return None
    fv = struct.unpack('<f', struct.pack('<I', imm))[0]
    if 1e-12 <= abs(fv) < 1e15:
        return fv
    return None

# 需要统计 refs 的 rodata/.data VA 集合（第二遍统一计数）
ref_vas = set()

def method_constants_and_calls(foff, size, va_base):
    """反汇编一个 .text 方法，返回 (consts, calls, instr_count)。"""
    code = elf.data[foff:foff+size]
    consts = []   # {off, kind, value, refs, shared, note}
    calls = []    # {site_va, target_va, target_sym}
    pending_imm = None  # 最近一次 mov reg,imm（用于关联 setter 实参）
    nins = 0
    for ins in md.disasm(code, va_base):
        nins += 1
        foff_ins = foff + (ins.address - va_base)
        # --- mov reg, imm32 ---
        if ins.mnemonic == 'mov' and len(ins.operands) == 2 and ins.operands[1].type == X86_OP_IMM:
            imm = ins.operands[1].imm & 0xFFFFFFFF
            reg = ins.reg_name(ins.operands[0].reg)
            fbits = is_float_bits(imm)
            if fbits is not None:
                consts.append(dict(off=foff_ins+ins.size-4, kind='float',
                                   value=fbits, raw_hex='0x%08x' % imm,
                                   refs=1, shared=False, reg=reg))
                pending_imm = ('float', fbits, imm)
            else:
                # 地址型立即数或小整数
                in_sec = any(s['addr'] and s['addr'] <= imm < s['addr']+s['size'] for s in elf.sections)
                if imm < 0x40000000 or in_sec:
                    consts.append(dict(off=foff_ins+ins.size-4, kind='int',
                                       value=imm, raw_hex='0x%08x' % imm,
                                       refs=1, shared=False, reg=reg))
                    pending_imm = ('int', imm, imm)
        # --- push imm8 / push imm32 (6A / 68) ---
        elif ins.mnemonic == 'push' and len(ins.operands) == 1 and ins.operands[0].type == X86_OP_IMM:
            imm = ins.operands[0].imm
            imm = imm & 0xFFFFFFFF if imm >= 0 else (imm, imm & 0xFFFFFFFF)
            imm_u = imm[1] if isinstance(imm, tuple) else imm
            sval = imm[0] if isinstance(imm, tuple) else imm
            consts.append(dict(off=foff_ins+1, kind='push_int',
                               value=sval, raw_hex='0x%08x' % imm_u,
                               refs=1, shared=False, reg='push'))
            pending_imm = ('int', sval, imm_u)
        # --- fld/fild [mem disp32] ---
        elif ins.mnemonic in ('fld', 'fild') and len(ins.operands) == 1 and ins.operands[0].type == X86_OP_MEM:
            disp = ins.operands[0].mem.disp
            off2, secname = elf.va2off(disp)
            if off2 is not None and secname in ('.rodata', '.data'):
                ref_vas.add(disp)
                if ins.mnemonic == 'fld':
                    # fld qword (8字节 double) vs dword (4字节 float)：按 disp 处长度不确定，两种都试
                    dbl = struct.unpack('<d', elf.data[off2:off2+8])[0]
                    flt = struct.unpack('<f', elf.data[off2:off2+4])[0]
                    consts.append(dict(off=off2, kind='rodata_double', value=dbl,
                                        disp_va='0x%08x' % disp, refs=None, shared=None,
                                        alt_float=flt))
                else:  # fild = 从内存读 int
                    iv = struct.unpack('<i', elf.data[off2:off2+4])[0]
                    consts.append(dict(off=off2, kind='rodata_int', value=iv,
                                       disp_va='0x%08x' % disp, refs=None, shared=None))
        # --- call / jmp 直接跳转 ---
        elif ins.mnemonic in ('call', 'jmp') and len(ins.operands) == 1 and ins.operands[0].type == X86_OP_IMM:
            tgt = ins.operands[0].imm
            sym = sym_at(tgt)
            calls.append(dict(site_va='0x%08x' % ins.address, target_va='0x%08x' % tgt,
                               mn=ins.mnemonic, sym=sym, pending_imm=list(pending_imm) if pending_imm else None))
            # getter 类helper（GetPlayer/GetVictim/GetAttack/GetOwner/GetTarget/GetCaster）不消费实参
            is_helper = bool(sym) and any(h in sym for h in
                        ('GetPlayer','GetVictim','GetAttack','GetOwner','GetTarget','GetCaster','GetSelf'))
            if not is_helper:
                pending_imm = None
        elif ins.mnemonic in ('call', 'jmp'):
            calls.append(dict(site_va='0x%08x' % ins.address, target_va=None,
                               mn=ins.mnemonic, sym=None, pending_imm=None))
    return consts, calls, nins

# ---------- 规范方法集合（用于判断缺失=继承默认） ----------
CANONICAL = [
    'GetCoolingtime','GetExecutetime','GetAttackspeed','GetRadius','GetAttackdistance',
    'GetEffectdistance','GetPraydistance','GetMpcost','GetGpcost','GetCpcost',
    'GetRequiredLevel','GetRequiredSp','GetRequiredItem','GetRequiredMoney',
    'GetHitrate','GetEnmity','GetAngle','TakeEffect',
    'State1GetTime','State1Loop','State1Quit','State1Skip','State1Bypass','State1Cancel',
    'State19Calculate','State19Interrupt',
    'State2GetTime','State2Loop','State2Quit','State2Skip','State2Bypass','State2Cancel',
    'State29Calculate','State29Interrupt',
    'State3GetTime','State3Loop','State3Quit','State3Skip','State3Bypass','State3Cancel',
    'State39Calculate','State39Interrupt',
    'State4GetTime','State4Loop','State4Quit','State4Skip','State4Bypass','State4Cancel',
    'State49Calculate','State49Interrupt',
    'StateAttack',
]

ONLY = ONLY  # 来自 argparse

dump = []
agg_methods = 0; agg_consts = 0; agg_calls = 0
text_sec = elf.sec_by_name('.text')

for code, name, variant, idx in SKILLS:
    if ONLY and code not in ONLY: continue
    marker = 'Skill%dStub' % code
    syms = [nm for nm in elf.symtab if marker in nm]
    owned = []
    methods_out = []
    for nm in sorted(syms):
        for v, sz, sh in elf.sym(nm):
            if sh == 0: continue
            sec = elf.sections[sh] if sh < len(elf.sections) else None
            if not sec or sec['name'] != '.text': continue
            short = short_sym(nm)
            # 跳过构造/析构（AP 单独有，这里只收集；但保留 C1Ev 因为含 AP）
            foff = sec['offset'] + (v - sec['addr'])
            consts, calls, nins = method_constants_and_calls(foff, sz, v)
            methods_out.append(dict(
                sym=nm, method=short, va='0x%08x' % v, size=sz,
                consts=consts, calls=calls, instr=nins))
            owned.append(short)
            agg_methods += 1
            agg_consts += len(consts)
            agg_calls += len(calls)
    owned_set = set(owned)
    missing = [c for c in CANONICAL if c not in owned_set]
    dump.append(dict(
        skill_id=code, skill_idx=idx, name=name, variant=variant,
        base_skill_id=base_of(code, name, variant),
        owned_methods=owned, missing_methods=missing,
        methods=methods_out))

# ---------- 第二遍：对 ref_vas 一次性统计 .text 引用数 ----------
print('收集到待统计 rodata/data VA 数 =', len(ref_vas), ' 耗时 %.1fs' % (time.time()-t_start))
text_bytes = elf.data[text_sec['offset']:text_sec['offset']+text_sec['size']]
ref_count = {}
want = {struct.pack('<I', va): va for va in ref_vas}
# 单遍滑动窗口
n = len(text_bytes)
for i in range(n - 3):
    key = text_bytes[i:i+4]
    va = want.get(key)
    if va is not None:
        ref_count[va] = ref_count.get(va, 0) + 1

# 把 refs/shared 回填到常量
for sk in dump:
    for m in sk['methods']:
        for c in m['consts']:
            if 'disp_va' in c:
                va = int(c['disp_va'], 16)
                rc = ref_count.get(va, 0)
                c['refs'] = rc
                c['shared'] = rc > 1

out = dict(
    base_gs=os.path.basename(BASE_GS),
    generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
    elapsed_sec=round(time.time()-t_start, 1),
    counts=dict(skills=len(dump), methods=agg_methods,
                constants=agg_consts, calls=agg_calls,
                rodata_vas=len(ref_vas)),
    skills=dump)
json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('写出 %s  skills=%d methods=%d consts=%d  耗时 %.1fs' %
      (OUT, len(dump), agg_methods, agg_consts, time.time()-t_start))
