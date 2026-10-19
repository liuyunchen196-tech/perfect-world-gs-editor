---
name: perfect-world-gs-editor
description: 完美世界单机（国服 138/144 端）服务端 gs 技能查看与修改工具。gs 是带 .symtab 符号表的 32 位 ELF，技能特性在 Skill{ID}Stub 的具名方法里。本 skill 反汇编每个技能方法、抽取可改常量（mov imm32 / .rodata 浮点 double/float / 逐级数组），可按名称或偏移修改、自动备份回写校验、导出全技能数据库（SQLite/JSON/CSV），并提供一个纯标准库本地 GUI（读 gs→选技能→仙魔联动→各级伤害表→改值→保存备份）。还支持：① 601 个 真·/狂· 技能伤害全量挖掘（assets/skill_damage_manifest.json，含角色标注/偏移/共享判定）；② 批量按比例修改伤害/冷却/吟唱/释放时间（GUI /batch 页 + CLI batch-modify）；③ 伤害复刻跳转（如剑刃风暴→箭阵）；④ 技能描述文字写回 skillstr.txt；⑤ 攻速上限修改。当用户要求"看/查某技能伤害逻辑、冷却、范围、概率、消耗"、"把某技能改成 XX"、"批量/按比例改技能伤害冷却"、"修改 gs 技能"、"设置攻速/出手间隔上限"、"把某技能伤害复刻成另一技能"、"导出/生成/查看技能数据库"、"更新/写回技能描述"、"element.data/tasks.data 物品/任务数据查看修改"、"游戏内添加物品/背包/仓库/技能（DEBUG 命令）"时使用。完全离线，运行期零第三方依赖（仅重建每级伤害表 build_perlevel.py、真狂 dump 需要 capstone）。⑥ element.data/task.data 物品/装备数据修改：复用 EL 编辑器读改加密数据（scripts/element_manager.py 负责备份原文件→启动 EL 编辑器→保存后分发同步到客户端/服务端→md5 校验），无需破译数据区加密。当用户要求"改 element.data、加物品/装备、改物品/装备数据、同步客户端服务端 element"时使用。
---

# 完美世界单机 gs 技能修改器

修改完美世界单机服务端 `gs` 文件里的技能特性（离线、可回滚）。

## 关键概念

- **gs**：带符号表的 32 位 ELF。每个技能是类 `GNET::Skill{ID}Stub`，特性在具名方法里：
  `GetCoolingtime`(冷却ms)、`GetExecutetime`(施放ms)、`GetAttackspeed`、`GetRadius`(范围)、
  `GetAttackdistance`、`GetMpcost`(真气)、`GetRequiredLevel`、`State{1..3}Calculate`(伤害/效果)、
  `State{1..3}GetTime`(状态持续) 等。
- **技能 ID**：`assets/skillstr.txt` 里 `<idx> "名"`，**技能代码 = idx // 10**（偶数 idx 名称行，奇数 idx 描述块）。
  真/狂变体是独立技能（如 真·剑刃风暴=1728、狂·剑刃风暴=1729、真·箭阵=602、狂·箭阵=603）。
- **元气(AP)消耗/回复**：每个技能在构造函数 `Skill{ID}Stub::C1Ev` 里
  `mov dword [reg+0x32], imm32` = 元气(AP)消耗（运行时通用 `Skill::GetApcost` 读 `[[Skill+0xc]+0x32]`）、
  `mov dword [reg+0x2e], imm32` = 元气(AP)回复（`Skill::GetApgain` 读 `[[Skill+0xc]+0x2e]`）。
  GUI/CLI 会把这两个值作为"元气(AP)消耗 / 元气(AP)回复"展示并可改。注意 skillstr 描述里的
  "施放需要 N 元气"是客户端 configs.pck 里的展示文字，与服务端 gs 的 AP 值可能不同步，改 gs 的
  AP 消耗生效的是实际扣减。
- **常量提取**：反汇编方法，识别 `mov reg,imm32`（int/float 位模式）、`fld`/`fild [rodata]`
  （.rodata 的 float/double）、逐级数组（`mov eax,[eax*4+0x...]`）。每个常量带文件偏移(off)、
  类型、当前值、是否共享。
- **⚠ 共享常量**：某 .rodata 浮点被多个技能引用（refs>1），改它会影响其它技能，工具默认跳过。

## 真/狂技能伤害 manifest（601 技能全量）

- `assets/skill_damage_manifest.json`（schema_version=1，基于干净138 gs 生成）：枚举全部 **601 个
  真·/狂·技能**（真301+狂300），逐技能对 `State*Calculate` 与 `StateAttack` 反汇编，按 setter 调用
  配对提取伤害/效果常量。每条含：method / file_off（文件偏移）/ kind（float|int|push_int|rodata_double）/
  value / refs / shared / role / setter。
- **role 分类**（GUI/CLI 标注与批量缩放依据）：`主伤害`(SetDecmp/SetPlus/SetDamage/SetPower)·
  `效果数值`(SetAmount/SetValue/SetNumber)·`系数`(SetRatio/SetRate/SetPercent)·
  `持续时间`(SetTime/SetLasttime)·`效果`(概率/出血/昏迷等)·`表现`(SetPerform/SetShowicon)·
  `标记`(SetPray/SetCombat/SetActive)·`debuff`·`耗蓝/吸血`·`效果(rodata)`·`共享-不可单独改`。
- **⚠ 批量伤害缩放的角色白名单**（与 `batch_engine.py` 常量一致）：
  - 默认只乘 `主伤害`（SetDecmp/SetPlus 直接伤害）；加 `--include-amounts` 才追加 `效果数值`（SetAmount 伤害量）。
  - **永不乘**：`持续时间`(SetTime)、`表现`(SetPerform/SetShowicon)、`标记`(SetPray/SetCombat)、
    `debuff`、`系数`(SetRatio)、`共享-不可单独改`。
  - 原因：StateAttack 里的 SetRatio（如跳动间隔比）与 SetAmount 属状态参数而非主伤害公式系数，
    乘比例会破坏技能（改变跳动频率/附加量）。这样防止把 SetPray(1)/SetPerform(1) 这类标志位误乘。
- **重建管道**（换了 gs 或想重挖时，需 capstone）：
  ```
  python scripts/dump_zhenkuang.py --gs <服务端gs> --out <dump.json> [技能ID...]
  python scripts/make_manifest.py --dump <dump.json> --out <manifest.json>
  ```
  产物覆盖 `assets/skill_damage_manifest.json` 即可被 GUI/CLI/批量引擎识别。
  ⚠ 中间产物 `zhenkuang_dump.json`（约 28MB）**不入库**（可由上面命令随时再生成）；
    仓库只跟踪最终的 manifest。本地重建命令：`python scripts/dump_zhenkuang.py --gs <gs>`。

## 快速开始

两个入口：**CLI**（`scripts/gs_tool.py`）和 **GUI**（`scripts/server.py`，本地网页）。

### GUI（推荐给用户手动操作）
```
python scripts/server.py          # 启动，浏览器开 http://127.0.0.1:7860/
```
（Windows 也可双击 `scripts/start_gui.bat`）
GUI 流程：读取 gs（弹窗选文件）→ 选技能 → 仙魔联动勾选框（默认勾选，真/狂一起改）
→ 查看各级伤害/效果表、每级需求表、伤害公式常数、**真/狂自有伤害常量（红色高亮，共享灰置）**、
其它特性 → 改值 → 保存（自动备份到 skill 根/data/backup/ 并回读校验）。
攻速上限修改在独立页 `/attackspeed`；批量修改在独立页 `/batch`（右上入口）。

### CLI
```
python scripts/gs_tool.py list  横扫千军                      # 搜索技能ID
python scripts/gs_tool.py view  横扫千军 --gs <gs路径>        # 查看全部特性+偏移（真/狂含伤害标注）
python scripts/gs_tool.py modify 凌风 --gs <gs路径> --set "昏迷概率=0.6"
python scripts/gs_tool.py modify 横扫千军 --gs <gs路径> --set "范围=12" --set "冷却时长(毫秒)=6000"
python scripts/gs_tool.py modify 横扫千军 --gs <gs路径> --off 0x914688 --type double --value 12
python scripts/gs_tool.py batch-modify --gs <gs路径> --scope damage|cooldown|cast|release \
    --ratio 1.5 --skills 1728,1729  | --zhen | --kuang | --all [--include-amounts] [--dry-run]
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

- **批量按比例修改**（伤害/冷却/吟唱/释放）：
  - GUI：`/batch` 页选作用域、填比例、选范围（自定义 / 全部真 / 全部狂 / 全部）→ 预览表 → 应用
    （自动备份+回读校验）。
  - 作用域映射（.symtab 实证）：damage→manifest 非共享伤害常量（role 白名单）；
    cooldown→`GetCoolingtime`(ms)；cast(吟唱/准备)→`GetExecutetime`(ms)；
    release(释放时间)→`GetAttackspeed`（gs 无独立 GetReleaseTime/GetCastTime，出手间隔
    `obj+0x264=floor(攻速×0.8)−1`）。瞬发技能 GetExecutetime=0 时 cast 缩放被"须>0"规则跳过。
  - CLI 例：`batch-modify --scope damage --ratio 1.5 --zhen --dry-run` 先预览。
  - **旧值匹配守卫**：写每个常量前先回读目标 gs 当前字节，与 manifest 记录的旧值比对；不一致则
    `status=skip_guard` 跳过不写（防止在已被改过的 gs 上误写）。典型场景：redo 版 gs 上剑刃风暴
    (1728/1729) 的 `State*Calculate` 已被改造成跳转区（jmp 到箭阵），其字节与 manifest 旧值不符，
    会被自动拦截——**要改剑刃的实际伤害请改箭阵 602/603，而非对剑刃跑 damage 批量**。
  - 写前自动备份 gs 到 `data/backup/gs.<说明>.<时间>.bak`，打补丁后回读校验。
- **攻速/出手间隔上限**：`property_policy::UpdateAttack` 把攻速值夹到 `[5,300]`，上限常量
  `0x12c=300`（cmp+mov 两处立即数；138 端文件偏移约 0x61b02/0x61b0b，144 同偏移）。出手间隔
  `obj+0x264 = floor(攻速×0.8)−1`。GUI 页 `/attackspeed` 可改上限并显示间隔换算。
- **伤害复刻**（如"把剑刃风暴伤害改成箭阵"）：把目标技能伤害方法入口改写为 `jmp rel32` 跳到
  源技能同名方法（State19/29/39 各一），其余 nop；只改伤害方法，其它参数不动。参考案例：
  剑刃风暴(1728真/1729狂)→箭阵(602真/603狂)。**实证结论**（data/blade_vs_arrow_cmp.txt）：
  跳转后剑刃三方法实际执行箭阵全部效果参数（SetDecmp=655/SetPlus=6639/SetRatio=3.0 等），
  直接伤害数字与箭阵一致；剑刃独有 StateAttack(428B) 是攻击触发的 debuff 参数块，不影响直接伤害。
  跳转后进游戏实测同等级伤害是否一致（对比同一靶子 3 次伤害取平均，误差<5% 即成功）。
- **每级伤害表**：`scripts/build_perlevel.py <gs路径>` 用 capstone 重建（仅构建期），结果写入
  `assets/skill_perlevel.json`，GUI 会读取展示各级伤害。真/狂技能多为 setter 型（无每级公式），
  其伤害以 manifest 的常量形式展示，不走 perlevel 表。
- **技能描述文字修改（写回 skillstr.txt）**：技能描述（含"施放需要 N 元气"等）在
  `assets/skillstr.txt`（UTF-16LE，即 configs.pck 里 skillsrt 的明文提取）。两种方式：
  ① GUI 选中技能后"技能描述文字"编辑区改数字/文字 → "保存描述 → skillstr.txt"；
  ② 精确写回工具 `scripts/update_desc.py`：
  ```
  python scripts/update_desc.py --file <skillstr.txt> --list 17280          # 查看+抽取伤害数字
  python scripts/update_desc.py --file <skillstr.txt> --set-dmg 17280 1770  # 改"上^96f5ff<N>^ffffff的物理伤害"
  python scripts/update_desc.py --file <skillstr.txt> --to-placeholder 17280  # 把硬编码数字改成 %d 动态占位
  ```
  （均支持 `--dry-run` 预览、`--no-backup` 跳过自动备份。）
  ⚠ 游戏内实际显示需把改好的 skillstr.txt 用**完美 pck 工具**（如 PckTools）打包回 configs.pck
  （该 pck 整文件加密，非纯文本）。
  **已拍板状态（勿回退）**：17280/17290（真/狂剑刃风暴）描述已由 `--to-placeholder` 改为 `%d`
  动态占位，随实际伤害联动；assets/skillstr.txt 与项目资产副本 MD5=FD02CF9C，不要再把数字写回去。

## element.data 物品/装备数据修改（复用 EL 编辑器，推荐）

数据区加密无需破译。分工：**EL 编辑器读/改/写**（GUI，用户操作），**`scripts/element_manager.py` 管备份/启动/分发**。

```
python scripts/element_manager.py open   <element.data路径>             # 备份→启动EL编辑器打开
python scripts/element_manager.py backup <element.data路径>             # 备份到同级 备份data/
python scripts/element_manager.py sync   <已保存文件> --client <p> [--server <p>]  # 备份目标→复制→校验
python scripts/element_manager.py check  <element.data路径>             # size/mtime/md5
```
工作流：`open` 把原文件备份到其所在目录 `备份data/` → `Start-Process` 启动 EL 编辑器（**支持命令行带文件参数直接打开**）→ 用户在「蜜蜂EL编辑器」改并保存 → `sync` 分发客户端/服务端 + md5 校验。
⚠ EL 编辑器是 Delphi 原生程序，未注册为 Windows 应用，**computer_use GUI 自动化无法启动/操作它**，只能由用户自己在桌面操作窗口，脚本仅负责启动进程。客户端 144 与服务端 138 的 element.data 是不同版本，分发务必匹配版本。详见 `references/element-data.md`。

## 详细文档

完整使用手册、数据结构、138/144 差异、剑刃风暴/箭阵剖析、批量作用域实证见 `references/README.md`。
**155 端教程知识（物品/任务/游戏内添加）**：
- `references/element-data.md`：element.data 路径、字段格式（element.cfg + rtype 字段类型）、
  头部明文目录结构、**加密现状**、类目/小类代码、物品添加与颜色/描述文本（item_color/item_ext_desc）。
- `references/tasks-data.md`：tasks.data 位置、任务编辑器用法、与 element.data 关联、格式逆向现状。
- `references/debug-commands.md`：游戏内 DEBUG 命令大全（Shift+~ 控制台）——添加物品/背包/仓库/
  扩充背包/元气上限/状态/刷怪/传送等（物品 ID 从 element.data 查）。
- 技能属性修改与 gs 工具方法（IDA+010 Editor）教程与现有工具一致：apgain=元气(0x2E)、
  GetExecutetime=施放、GetCoolingtime=冷却、State*GetTime=吟唱、GetPraydistance=距离、
  GetRadius=范围、GetMpcost=真气；技能描述在 configs.pck 的 skillsrt（即 skillstr.txt）。
- ⚠ element.data/tasks.data 国服文件**加密**（version2=0x2000），头部明文目录已定位、数据区加密算法
  待逆向；游戏内添加优先用 DEBUG 命令，不依赖改写 data 文件。

## 文件

- `scripts/gs_tool.py`：CLI 统一工具（list/view/modify/batch-modify/export-db）。
- `scripts/server.py`：本地 GUI 后端（纯标准库 http.server，端口 7860；运行期不依赖 capstone）。
- `scripts/batch_engine.py`：批量按比例修改引擎（server.py 与 gs_tool.py 共用；manifest 路径自动适配）。
- `scripts/static/*.html`：GUI 前端（index.html 技能修改 + attackspeed.html 攻速上限 + batch.html 批量）。
- `scripts/dump_zhenkuang.py` + `scripts/make_manifest.py`：真/狂技能伤害 dump → manifest 重建管道（需 capstone）。
- `scripts/update_desc.py`：skillstr.txt 描述伤害数字精确写回（--list / --set-dmg）。
- `scripts/build_perlevel.py`：每级伤害表生成器（需 capstone）。
- `scripts/start_gui.bat`：Windows 一键启动 GUI。
- `assets/skillstr.txt`：技能名表（UTF-16LE，`<idx> "名"`，技能代码=idx//10）。
- `assets/skill_damage_manifest.json`：601 真/狂技能伤害/效果常量 manifest（批量 damage 的契约）。
- `assets/skill_perlevel.json`：预计算的每级伤害/效果表（可选）。
- `references/README.md`：详细文档。

## 仓库跟踪范围（与 .gitignore 对应）

- 入库：`scripts/`（含 `static/`）、`assets/skillstr.txt`、`assets/skill_damage_manifest.json`、
  `assets/skill_perlevel.json`、`references/`、`SKILL.md`、`.gitignore`。
- 忽略：`data/` 整目录（GUI 运行期生成 current.gs/backup，以及本地副本的
  `data/skill_damage_manifest.json`、`data/CHANGELOG.md`、`data/blade_vs_arrow_cmp.txt`、
  `data/batch_test_report.txt`）、`__pycache__/`、`*.pyc`。
  注意：伤害 manifest 的**入库副本**在 `assets/` 下；`data/` 那份是运行期镜像，不提交。
  `zhenkuang_dump.json`（28MB）任何位置都不入库。
