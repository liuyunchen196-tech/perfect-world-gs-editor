#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_manifest.py —— 只读：在 zhenkuang_dump.json 基础上逐技能研判伤害/效果常量，
输出固定 schema 的 skill_damage_manifest.json（与批量修改功能对接的唯一契约）。
"""
import json, re, time, os, argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_parser = argparse.ArgumentParser(description='在 zhenkuang_dump.json 基础上生成 skill_damage_manifest.json')
_parser.add_argument('--dump', default=os.path.join(_HERE, 'data', 'zhenkuang_dump.json'),
                     help='输入 dump JSON（dump_zhenkuang.py 输出）')
_parser.add_argument('--out', default=os.path.join(_HERE, 'data', 'skill_damage_manifest.json'),
                     help='输出 manifest JSON 路径')
_args = _parser.parse_args()
DUMP = _args.dump
OUT = _args.out
t0 = time.time()
d = json.load(open(DUMP, encoding='utf-8'))

BASE = d['base_gs']

def setter_name(sym):
    if not sym: return None
    i = sym.find('Set')
    if i < 0: return None
    # 方法名 = 从 'Set' 到第一个大写 'E'（Itanium 名字结束符）之前
    j = i + 3
    while j < len(sym) and sym[j] != 'E':
        j += 1
    return sym[i:j]

ROLE = {
 'SetDecmp':'主伤害','SetDamage':'主伤害','SetPlus':'主伤害','SetAttackpower':'主伤害',
 'SetPower':'主伤害','SetAtt':'主伤害',
 'SetRatio':'系数','SetRate':'系数','SetPercent':'系数',
 'SetAmount':'效果数值','SetValue':'效果数值','SetNumber':'效果数值',
 'SetTime':'持续时间','SetLasttime':'持续时间','SetDuration':'持续时间',
 'SetPray':'标记','SetPerform':'表现','SetCombat':'标记','SetActive':'标记',
 'DrainMana':'耗蓝','DrainHp':'吸血','SetDrainmana':'耗蓝',
 'SetAuracurse2':'debuff','SetDenyattackcmd':'debuff','SetInsertvstate':'debuff',
 'SetFreemove':'debuff','SetShowicon':'表现','SetBypass':'标记','SetAura':'debuff',
}
DAMAGE_SETTERS = {'SetDecmp','SetDamage','SetPlus','SetAttackpower','SetPower'}
HEAL_SETTERS = {'SetHp','RecoverHp','SetMp','RecoverMana','Recover','Heal','SetLife','RecoverLife'}

def is_damage_method(mname):
    return mname == 'StateAttack' or bool(re.search(r'State\d+Calculate', mname))

def build_skill(sk):
    methods = sk['methods']
    dm_methods = [m for m in methods if is_damage_method(m['method'])]
    consts = []
    note_bits = []
    all_setters = set()
    summon = False; heal = False
    for m in dm_methods:
        used = set()
        # 按调用顺序配对：每个带 pending_imm 的 setter 调用 -> 一个常量
        for cl in m['calls']:
            sn = setter_name(cl['sym'])
            if sn: all_setters.add(sn)
            if cl['sym'] and any(k in cl['sym'] for k in ('Summon','CreateMonster','CreateNpc','SummonPet','AddPet','CreateTrap','CreateTrap')):
                summon = True
            if cl['sym'] and any(k in cl['sym'] for k in ('SetHp','RecoverHp','RecoverMana','Heal','RecoverLife','SetLife')):
                heal = True
            sn = setter_name(cl['sym'])
            if not cl['pending_imm'] or not sn:
                continue
            kind, val, raw = cl['pending_imm']
            # 在本方法 consts 里找一个未用、值/类型匹配的常量
            off = None; cref = None
            for idx, c in enumerate(m['consts']):
                if idx in used: continue
                cval = c['value']
                if isinstance(cval, list): continue
                if abs((cval or 0) - val) < 1e-9:
                    off = c['off']; cref = c; used.add(idx); break
            if off is None:
                # 没找到对应常量（例如 push 立即数未单独记录），仍记录
                off = None; cref = {'refs':1,'shared':False,'kind':kind}
            role = ROLE.get(sn, '效果')
            shared = bool(cref.get('shared'))
            consts.append({
                'method': m['method'],
                'file_off': ('0x%x' % off) if off is not None else None,
                'kind': cref.get('kind', kind),
                'value': val,
                'refs': cref.get('refs'),
                'shared': shared,
                'role': ('共享-不可单独改' if shared else role),
                'setter': sn,
            })
        # rodata 浮点常量（被 fld 引用）也纳入
        for c in m['consts']:
            if c['kind'] in ('rodata_double','rodata_int'):
                shared = bool(c.get('shared'))
                consts.append({
                    'method': m['method'],
                    'file_off': '0x%x' % c['off'],
                    'kind': c['kind'],
                    'value': c['value'],
                    'refs': c.get('refs'),
                    'shared': shared,
                    'role': ('共享-不可单独改' if shared else '效果(rodata)'),
                    'setter': None,
                })
        # note 片段
        sc = [(x['setter'], x['value']) for x in consts if x['setter']]
        if sc:
            note_bits.append('%s ' % m['method'] + '/'.join('%s(%s)' % (s,v) for s,v in sc))

    # ---- 分类 ----
    has_damage_setter = bool(all_setters & DAMAGE_SETTERS)
    has_state_attack = any(m['method']=='StateAttack' for m in dm_methods)
    if summon:
        da = 'summon'
    elif heal:
        da = 'heal'
    elif has_damage_setter:
        da = 'setter'
    elif has_state_attack and consts:
        da = 'setter'   # StateAttack 攻击触发的效果块
    elif all_setters and not has_damage_setter:
        da = 'buff'     # 只有 SetTime/SetRatio 等辅助 setter
    elif not consts:
        da = 'no_independent'
    else:
        da = 'unknown'

    # note
    if note_bits:
        note = '; '.join(note_bits)
    else:
        if da == 'no_independent':
            note = 'State*Calculate 无独立立即数常量(方法体过薄/继承默认)，未发现可单独修改的伤害常数'
        elif da == 'unknown':
            note = '已检查 State*Calculate 与 StateAttack，未识别出明确的伤害/效果 setter 配对，需人工复查'
        else:
            note = '%s 型，setters=%s' % (da, sorted(all_setters))
    return {
        'skill_id': sk['skill_id'],
        'name': sk['name'],
        'variant': sk['variant'],
        'base_skill_id': sk['base_skill_id'],
        'damage_analysis': da,
        'note': note,
        'damage_constants': consts,
    }

skills_out = [build_skill(sk) for sk in d['skills']]

# 统计分类分布
from collections import Counter
cnt = Counter(s['damage_analysis'] for s in skills_out)
out = {
  'schema_version': 1,
  'base_gs': BASE,
  'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
  'counts': {
     'total_skills': len(skills_out),
     'zhen': sum(1 for s in skills_out if s['variant']=='zhen'),
     'kuang': sum(1 for s in skills_out if s['variant']=='kuang'),
     'by_analysis': dict(cnt),
     'total_damage_constants': sum(len(s['damage_constants']) for s in skills_out),
  },
  'skills': skills_out,
}
json.dump(out, open(OUT,'w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('写出 %s  skills=%d  耗时 %.1fs' % (OUT, len(skills_out), time.time()-t0))
print('分类分布:', dict(cnt))
print('伤害常量总数:', out['counts']['total_damage_constants'])
