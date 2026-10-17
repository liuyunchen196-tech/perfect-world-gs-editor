# -*- coding: utf-8 -*-
"""
构建 每级伤害/效果表（build time，需 capstone）
读取 gs → 对每个技能的状态伤害方法反汇编 → 用 x87 模拟器在等级1~10求值
→ 生成 assets/skill_perlevel.json
运行时后端/前端只需读 JSON，不依赖 capstone（保持离线零依赖）。
"""
import os, re, json, struct, sys
import capstone

exec(open(os.path.join(os.path.dirname(__file__), 'server.py'), encoding='utf-8').read().split("if __name__")[0])

GS = r'D:\完美单机\修改gs工具\gs'
OUT = os.path.join(BASE, 'assets', 'skill_perlevel.json')
LEVEL_GETTER = 0x8467d82

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.detail = True

def ro_val(elf, va):
    off, name = elf.va2off(va)
    if off is None or name != '.rodata': return None
    if name == '.rodata':
        # 统一按 double 读（这些系数都是 double）
        return struct.unpack('<d', elf.data[off:off+8])[0]
    return None

class Emu:
    def __init__(self, elf, level):
        self.elf = elf; self.level = level
        self.st = []            # 数值栈 st[0]=top
        self.se = []            # 表达式栈
        self.loc = {}           # ebp 偏移 -> (值, 表达式)
        self.eax = self.edx = self.ecx = self.ebx = 0
        self.eax_expr = self.edx_expr = self.ecx_expr = self.ebx_expr = None
        self.esp = []
        self.outputs = []       # 存到 [ebp-4] 的 (值, 表达式)
        self.abort = False
        self.pending_getter = False

    def push(self, v, e): self.st.insert(0, v); self.se.insert(0, e)
    def pop(self):
        if not self.st: return 0.0, '?'
        return self.st.pop(0), self.se.pop(0)

    def handle(self, ins):
        mn = ins.mnemonic
        ops = ins.operands
        # 整数寄存器操作
        if mn in ('mov', 'shl', 'add', 'sub', 'lea'):
            self._intop(mn, ops, ins.op_str)
            return
        if mn == 'push':
            if len(ops) == 1:
                o = ops[0]
                if o.type == capstone.x86.X86_OP_REG:
                    r = self._regval(o.reg)
                    self.esp.append((r, self._regexpr(o.reg)))
            return
        if mn == 'call':
            if ops and ops[0].type == capstone.x86.X86_OP_IMM and ops[0].imm == LEVEL_GETTER:
                self.eax = self.level; self.eax_expr = 'L'
            else:
                self.eax = 0; self.eax_expr = '?'
            return
        if mn == 'fild' and ops and ops[0].type == capstone.x86.X86_OP_MEM:
            m = ops[0].mem
            if m.base == capstone.x86.X86_REG_ESP:
                if self.esp:
                    v, e = self.esp.pop(); self.push(float(v), e)
                else:
                    self.push(float(self.level), 'L')
            return
        if mn == 'fld' and ops and ops[0].type == capstone.x86.X86_OP_MEM:
            m = ops[0].mem
            if m.base == capstone.x86.X86_REG_EBP and m.index == 0:
                off = m.disp
                if off in self.loc: self.push(*self.loc[off])
                else: self.push(0.0, '?')
            elif m.base == 0 and m.index == 0:
                c = ro_val(self.elf, m.disp & 0xFFFFFFFF)
                if c is None: self.abort = True; return
                self.push(c, repr(c))
            return
        if mn == 'fldz': self.push(0.0, '0'); return
        if mn == 'fld1': self.push(1.0, '1'); return
        if mn == 'fstp':
            if not self.st: self.abort = True; return
            v, e = self.pop()
            if len(ops) == 1 and ops[0].type == capstone.x86.X86_OP_MEM:
                m = ops[0].mem
                if m.base == capstone.x86.X86_REG_EBP and m.index == 0:
                    off = m.disp
                    if off == -4:
                        self.outputs.append((v, e))
                    else:
                        self.loc[off] = (v, e)
            return
        # 二元 FP：fNp st(1)
        if mn in ('fmulp','faddp','fsubp','fdivp') and len(self.st) >= 2:
            a, ea = self.pop(); b, eb = self.pop()
            if mn == 'fmulp': r, er = b*a, f'({ea}×{eb})'
            elif mn == 'faddp': r, er = b+a, f'({ea}+{eb})'
            elif mn == 'fsubp': r, er = b-a, f'({ea}-{eb})'
            else: r, er = b/a if a else 0, f'({ea}/{eb})'
            self.push(r, er); return
        if mn in ('fmul','fadd','fsub','fdiv') and len(self.st) >= 2:
            b, eb = self.pop(); a, ea = self.pop()
            if mn == 'fmul': r, er = a*b, f'({ea}×{eb})'
            elif mn == 'fadd': r, er = a+b, f'({ea}+{eb})'
            elif mn == 'fsub': r, er = a-b, f'({ea}-{eb})'
            else: r, er = a/b if b else 0, f'({ea}/{eb})'
            self.push(r, er); return
        if mn == 'fchs' and self.st:
            v, e = self.pop(); self.push(-v, f'(-{e})'); return
        # 忽略无害指令
        if mn in ('push','pop','leave','ret','nop'):
            return
        # 其它未支持 → 中止该方法（不产数值）
        if mn in ('mov','add','sub','lea','shl','and','or','xor','test','cmp','jmp','jz','jnz','je','jne','setne','setz','cdq','imul','sar'):
            # 部分可能带寄存器/立即数，尝试宽松处理
            return
        self.abort = True

    def _regval(self, r):
        return {capstone.x86.X86_REG_EAX:self.eax, capstone.x86.X86_REG_EDX:self.edx,
                capstone.x86.X86_REG_ECX:self.ecx, capstone.x86.X86_REG_EBX:self.ebx}.get(r, 0)
    def _regexpr(self, r):
        return {capstone.x86.X86_REG_EAX:self.eax_expr, capstone.x86.X86_REG_EDX:self.edx_expr,
                capstone.x86.X86_REG_ECX:self.ecx_expr, capstone.x86.X86_REG_EBX:self.ebx_expr}.get(r, None)

    def _intop(self, mn, ops, ost):
        def regidx(o):  # 返回 (reg名, 是否imm)
            if o.type == capstone.x86.X86_OP_REG: return o.reg, False
            if o.type == capstone.x86.X86_OP_IMM: return o.imm, True
            return None, False
        def setr(r, v, e):
            if r == capstone.x86.X86_REG_EAX: self.eax, self.eax_expr = v, e
            elif r == capstone.x86.X86_REG_EDX: self.edx, self.edx_expr = v, e
            elif r == capstone.x86.X86_REG_ECX: self.ecx, self.ecx_expr = v, e
            elif r == capstone.x86.X86_REG_EBX: self.ebx, self.ebx_expr = v, e
        if not ops: return
        dst = ops[0]
        if dst.type != capstone.x86.X86_OP_REG: return
        if mn == 'mov':
            src = ops[1] if len(ops) > 1 else None
            if src is None: return
            if src.type == capstone.x86.X86_OP_REG:
                setr(dst.reg, self._regval(src.reg), self._regexpr(src.reg))
            elif src.type == capstone.x86.X86_OP_IMM:
                setr(dst.reg, src.imm & 0xFFFFFFFF, str(src.imm & 0xFFFFFFFF))
        elif mn in ('shl','add','sub'):
            src = ops[1] if len(ops) > 1 else None
            if src is None: return
            cur = self._regval(dst.reg); ce = self._regexpr(dst.reg)
            if src.type == capstone.x86.X86_OP_IMM:
                k = src.imm
                if mn == 'shl': setr(dst.reg, (cur << k) & 0xFFFFFFFF, f'({ce}<<{k})' if ce else None)
                elif mn == 'add': setr(dst.reg, (cur + k) & 0xFFFFFFFF, f'({ce}+{k})' if ce else None)
                else: setr(dst.reg, (cur - k) & 0xFFFFFFFF, f'({ce}-{k})' if ce else None)
            elif src.type == capstone.x86.X86_OP_REG:
                sv = self._regval(src.reg); se = self._regexpr(src.reg)
                if mn == 'add': setr(dst.reg, (cur + sv) & 0xFFFFFFFF, f'({ce}+{se})' if ce and se else None)
                elif mn == 'sub': setr(dst.reg, (cur - sv) & 0xFFFFFFFF, f'({ce}-{se})' if ce and se else None)
        elif mn == 'lea' and len(ops) > 1:
            m = ops[1].mem
            base = self._regval(m.base) if m.base else 0
            idx = self._regval(m.index) if m.index else 0
            v = base + idx * m.scale + m.disp
            ie = self._regexpr(m.index) if m.index else None
            be = self._regexpr(m.base) if m.base else None
            disp_s = ('+%d' % m.disp) if m.disp else ''
            if m.index and ie:
                e = f'({ie}×{m.scale}{disp_s})'
            elif m.base and be:
                e = f'({be}{disp_s})'
            else:
                e = str(v)
            setr(dst.reg, v & 0xFFFFFFFF, e)


def eval_method(elf, nm, value, size, shndx):
    sec = elf.sections[shndx] if shndx < len(elf.sections) else None
    if not sec or sec['name'] != '.text': return None
    foff = sec['offset'] + (value - sec['addr'])
    code = elf.data[foff:foff+size]
    insns = list(md.disasm(code, value))
    per = {}
    for L in range(1, 11):
        emu = Emu(elf, L)
        for ins in insns:
            emu.handle(ins)
            if emu.abort: return None
        per[L] = [(round(v, 4), e) for v, e in emu.outputs]
    return per

def main():
    data = open(GS, 'rb').read()
    elf = ELF(data)
    result = {}
    # 收集所有有 State Calculate 方法的技能
    import re as _re
    pat = _re.compile(r'Skill(\d+)Stub')
    states_by_skill = {}
    for nm in elf.symtab:
        m = pat.search(nm)
        if not m: continue
        if 'State' in nm and 'Calculate' in nm:
            sid = int(m.group(1))
            sm = _re.search(r'State(\d+)Calculate', nm)
            if not sm: continue
            st = sm.group(1)[0]
            states_by_skill.setdefault(sid, {})
            states_by_skill[sid][st] = nm
    for sid in sorted(states_by_skill):
        for st, nm in states_by_skill[sid].items():
            for value, size, shndx in elf.sym(nm):
                per = eval_method(elf, nm, value, size, shndx)
                if per:
                    formulas = [e for v, e in per[1]]
                    result.setdefault(sid, {})[f'状态{st}'] = {
                        'formulas': formulas,
                        'values': {str(L): [v for v, _ in per[L]] for L in per},
                    }
                break
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False)
    print('skills with perlevel:', len(result))
    print('saved:', OUT)

if __name__ == '__main__':
    if len(sys.argv) > 1:
        GS = sys.argv[1]
    main()
