---
name: perfect-world-gs-editor
description: 完美世界单机（国服 138/144 端）服务端 gs 技能查看与修改工具。gs 是带 .symtab 符号表的 32 位 ELF，技能特性在 Skill{ID}Stub 的具名方法里。本 skill 反汇编每个技能方法、抽取可改常量（mov imm32 / .rodata 浮点 double/float / 逐级数组），可按名称或偏移修改、自动备份回写校验、导出全技能数据库（SQLite/JSON/CSV），并提供一个纯标准库本地 GUI（读 gs→选技能→仙魔联动→各级伤害表→改值→保存备份）。当用户要求"看/查某技能伤害逻辑、冷却、范围、概率、消耗"、"把某技能改成 XX"（改冷却/范围/概率/伤害/施放/消耗/等级要求/武器）、"修改 gs 技能"、"设置攻速/出手间隔上限"、"把某技能伤害复刻成另一技能"、"导出/生成/查看技能数据库"时使用。完全离线，运行期零第三方依赖（仅重建每级伤害表 build_perlevel.py 需要 capstone）。
---

# 完美世界单机 gs 技能修改器

修改完美世界单机服务端 `gs` 文件里的技能特性（离线、可回滚）。

## 关键概念

- **gs**：带符号表的 32 位 ELF。每个技能是类 `GNET::Skill{ID}Stub`，特性在具名方法里：
  `GetCoolingtime`(冷却ms)、`GetExecutetime`(施放ms)、`GetAttackspeed`、`GetRadius`(范围)、
  `GetAttackdistance`、`GetMpcost`(真气)、`GetRequiredLevel`、`State{1..3}Calculate`(伤害/效果)、
  `State{1..3}GetTime`(状态持续) 等。
- **技能 ID**：`assets/skillstr.txt` 里 `<idx> "名"`，**技能代码 = idx // 10**（偶数 idx 名称行，奇数 idx 描述块）。
  真/狂变体是独立技能（如 真·剑刃风暴=1728、狂·剑刃风暴=1729）。
- **常量提取**：反汇编方法，识别 `mov reg,imm32`（int/float 位模式）、`fld`/`fild [rodata]`
  （.rodata 的 float/double）、逐级数组（`mov eax,[eax*4+0x...]`）。每个常量带文件偏移(off)、
  类型、当前值、是否共享。
- **⚠ 共享常量**：某 .rodata 浮点被多个技能引用（refs>1），改它会影响其它技能，工具默认跳过。

## 快速开始

两个入口：**CLI**（`scripts/gs_tool.py`）和 **GUI**（`scripts/server.py`，本地网页）。

### GUI（推荐给用户手动操作）
```
python scripts/server.py          # 启动，浏览器开 http://127.0.0.1:7860/
```
（Windows 也可双击 `scripts/start_gui.bat`）
GUI 流程：读取 gs（弹窗选文件）→ 选职业/技能 → 仙魔联动勾选框（默认勾选，真/狂一起改）
→ 查看各级伤害/效果表、每级需求表、伤害公式常数、其它特性 → 改值 → 保存（自动备份到
skill 根/data/backup/ 并回读校验）。攻速上限修改在独立页 `/attackspeed`。

### CLI
```
python scripts/gs_tool.py list  横扫千军                      # 搜索技能ID
python scripts/gs_tool.py view  横扫千军 --gs <gs路径>        # 查看全部特性+偏移
python scripts/gs_tool.py modify 凌风 --gs <gs路径> --set "昏迷概率=0.6"
python scripts/gs_tool.py modify 横扫千军 --gs <gs路径> --set "范围=12" --set "冷却时长(毫秒)=6000"
python scripts/gs_tool.py modify 横扫千军 --gs <gs路径> --off 0x914688 --type double --value 12
python scripts/gs_tool.py export-db --gs <gs路径> --out <目录>  # 导出 SQLite/JSON/CSV 数据库
```
`--set "关键词=新值"` 按特性名/描述匹配；`--off` 按文件偏移精确改（`--type`+`--value`）。

## 通用工作流（AI 或用户操作）

1. **定位 gs**：让用户选择/提供 gs 路径（默认 `D:\完美单机\修改gs工具\gs`）。
2. **找技能**：`gs_tool.py list <关键词>` 或用 skillstr.txt 查技能名 → 技能ID。
3. **查看**：`view <技能ID>` 列出每个特性的描述、off、类型、当前值、是否共享。
4. **修改**：`modify` 按标签或偏移改。**凡是 `⚠共享` 的常量不单独改**（影响所有技能）。
   - 普通数值（冷却/范围/真气/等级/概率）：直接 `--set "标签=新值"`。
   - 特殊需求（改伤害公式常数、把技能伤害复刻成另一技能）用偏移精确改或脚本跳转复刻。
5. **写回与备份**：工具自动备份原 gs（`*.bak_时间戳`）→ 打补丁 → 写回 → 回读校验。
6. **部署**：把改好的 gs 覆盖服务端 `pwserver\gamed\gs`（先备份），重启服务，进游戏实测。

## 常见高级操作

- **攻速/出手间隔上限**：`property_policy::UpdateAttack` 把攻速值夹到 `[5,300]`，上限常量
  `0x12c=300`（cmp+mov 两处立即数；138 端文件偏移约 0x61b02/0x61b0b，144 同偏移）。出手间隔
  `obj+0x264 = floor(攻速×0.8)−1`。GUI 页 `/attackspeed` 可改上限并显示间隔换算。
- **伤害复刻**（如"把剑刃风暴伤害改成箭阵"）：把目标技能伤害方法入口改写为 `jmp rel32` 跳到
  源技能同名方法（State19/29/39 各一），其余 nop；只改伤害方法，其它参数不动。参考案例：
  剑刃风暴(1728真/1729狂)→箭阵(602真/603狂)；箭阵伤害方法为 State19/29 薄封装 + State39 真伤害。
  跳转后进游戏实测同等级伤害是否一致。
- **每级伤害表**：`scripts/build_perlevel.py <gs路径>` 用 capstone 重建（仅构建期），结果写入
  `assets/skill_perlevel.json`，GUI 会读取展示各级伤害。

## 详细文档

完整使用手册、数据结构、138/144 差异、剑刃风暴/箭阵剖析见 `references/README.md`。

## 文件

- `scripts/gs_tool.py`：CLI 统一工具（list/view/modify/export-db）。
- `scripts/server.py`：本地 GUI 后端（纯标准库 http.server，端口 7860；运行期不依赖 capstone）。
- `scripts/static/*.html`：GUI 前端（index.html 技能修改 + attackspeed.html 攻速上限）。
- `scripts/build_perlevel.py`：每级伤害表生成器（需 capstone）。
- `scripts/start_gui.bat`：Windows 一键启动 GUI。
- `assets/skillstr.txt`：技能名表（UTF-16LE，`<idx> "名"`，技能代码=idx//10）。
- `assets/skill_perlevel.json`：预计算的每级伤害/效果表（可选）。
- `references/README.md`：详细文档。
