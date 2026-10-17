# 完美世界单机 · gs 技能修改器（skill 版）

离线查看/修改完美世界单机（国服 138/144 端）服务端 `gs` 的技能特性，可回滚。

## 目录结构

```
perfect-world-gs-editor/
├── SKILL.md               # 使用说明（触发+工作流）
├── references/
│   └── README.md          # 本文档（详细手册）
├── scripts/
│   ├── gs_tool.py         # CLI 统一工具（list/view/modify/batch-modify/export-db）
│   ├── server.py          # 本地 GUI 后端（纯标准库 http.server，端口 7860）
│   ├── batch_engine.py    # 批量按比例修改引擎（GUI/CLI 共用）
│   ├── dump_zhenkuang.py  # 真/狂技能 dump（需 capstone，仅构建期）
│   ├── make_manifest.py   # dump → skill_damage_manifest.json
│   ├── update_desc.py     # skillstr.txt 描述伤害数字精确写回
│   ├── build_perlevel.py  # 每级伤害/效果表生成器（需 capstone，仅构建期）
│   ├── start_gui.bat      # Windows 一键启动 GUI
│   └── static/
│       ├── index.html     # 技能修改前端（含仙魔联动/真狂伤害标注）
│       ├── attackspeed.html # 攻速上限/出手间隔独立前端
│       └── batch.html     # 批量按比例修改前端
└── assets/
    ├── skillstr.txt       # 技能名表（UTF-16LE；技能代码 = idx//10；前段=名称行，后段=描述块）
    ├── skillsrt/
    │   └── skillsrt.txt   # 技能名+描述明文部署版（UTF-16LE 带 BOM，与 configs.pck 内 skillsrt 一致）
    ├── skill_damage_manifest.json # 601 真/狂技能伤害/效果常量 manifest
    └── skill_perlevel.json # 每级伤害/效果表（可选）
```

## 启动 GUI

需 Python 3（GUI 运行期零第三方依赖，完全离线）。Windows 可双击 `scripts/start_gui.bat`，
或 `python scripts/server.py`，然后浏览器开 `http://127.0.0.1:7860/`。

GUI 流程：
1. 点「读取 gs」，弹窗选择带符号表的服务端 gs（如 138 端 `pwserver\gamed\gs`）。
2. 左侧搜索/选技能；勾「仙魔联动」则真/狂变体一起改；选技能后顶部红色高亮显示
   **自有伤害常量**（来自 manifest），共享常量灰置不可改。
3. 查看：各级伤害/效果表（L=1..10 按公式逐级）、每级需求/消耗表、伤害公式系数、其它特性。
4. 改值 → 点「保存修改」：自动备份到 skill 根 `data/backup/`，打补丁写回，回读校验。
5. 攻速上限：主页面右上「⚡ 攻速上限修改 →」或直接开 `/attackspeed`。
6. 批量修改：主页面右上「🧮 批量按比例修改 →」或直接开 `/batch`。

## CLI 用法

```
python scripts/gs_tool.py list  横扫千军
python scripts/gs_tool.py view  横扫千军 --gs <gs路径>
python scripts/gs_tool.py modify 横扫千军 --gs <gs路径> --set "范围=12" --set "冷却时长(毫秒)=6000"
python scripts/gs_tool.py modify 凌风 --gs <gs路径> --off 0x914688 --type double --value 0.6
python scripts/gs_tool.py batch-modify --gs <gs路径> --scope damage|cooldown|cast|release \
    --ratio 1.5 --skills 1728,1729 | --zhen | --kuang | --all [--include-amounts] [--dry-run]
python scripts/gs_tool.py export-db --gs <gs路径> --out <目录>
```

- `--set "关键词=新值"`：按特性标签/描述匹配（如"冷却时长(毫秒)""范围""伤害/效果(状态1)"）。
- `--off <偏移> --type int|float|double --value <新值>`：按文件偏移精确改。
- 默认 gs = `D:\完美单机\修改gs工具\gs`，可用 `--gs` 覆盖。
- 每次修改自动备份 `*.bak_时间戳`，写回后回读校验。

## 核心原理

- gs 是带 `.symtab` 的 32 位 ELF；技能 = 类 `GNET::Skill{ID}Stub`，特性在具名方法：
  `GetCoolingtime`(冷却ms)/`GetExecutetime`/`GetAttackspeed`/`GetRadius`/`GetAttackdistance`/
  `GetMpcost`(真气)/`GetRequiredLevel`/`State{1..3}Calculate`(伤害)/`State{1..3}GetTime`(持续)。
- 常量识别：`mov reg,imm32`(int/float)、`fld/fild [rodata]`(float/double)、逐级数组
  (`mov eax,[eax*4+0x...]`)。每个常量带文件偏移、类型、当前值、是否共享。
- ⚠共享常量（.rodata 浮点被多技能引用）改则全技能生效，工具默认禁用。
- 技能名表：`assets/skillstr.txt`，`<idx> "名"`，**技能代码 = idx // 10**（偶数 idx=名称，奇数 idx=描述）。

## 真/狂技能伤害 manifest（601 技能全量）

- `assets/skill_damage_manifest.json`（schema_version=1，基准 = 干净138 gs）：
  真301+狂300 = **601 技能**，共 5806 条伤害/效果常量（含 66 条共享）。逐技能对
  `State*Calculate` + `StateAttack` 反汇编，按 setter 调用与实参配对提取常量。
- 每条字段：`method` / `file_off`(16进制文件偏移) / `kind`(float|int|push_int|rodata_double) /
  `value` / `refs` / `shared` / `role` / `setter`(SetDecmp/SetPlus/SetRatio/SetAmount/SetTime/
  SetPray/SetPerform/SetCombat 等)。
- **role 含义**：
  - `主伤害`：SetDecmp/SetPlus/SetDamage/SetAttackpower/SetPower/SetAtt 直接伤害数值（可缩放）。
  - `效果数值`：SetAmount/SetValue/SetNumber 效果量（可缩放）。
  - `系数`：SetRatio/SetRate/SetPercent 伤害/效果系数（可缩放）。
  - `持续时间`：SetTime/SetLasttime/SetDuration 状态持续（不随伤害缩放）。
  - `效果`：SetProbability/SetBleeding/SetDizzy 等概率/状态（不随伤害缩放）。
  - `表现`：SetPerform/SetShowicon 表现标志（不随伤害缩放）。
  - `标记`：SetPray/SetCombat/SetActive 标记位（不随伤害缩放）。
  - `debuff`：SetFreemove/SetDenyattackcmd/SetInsertvstate 等（不随伤害缩放）。
  - `耗蓝/吸血`：DrainMana/DrainHp。
  - `效果(rodata)` / `共享-不可单独改`：rodata 常量（共享则禁用）。
- **批量伤害缩放白名单**（与 `batch_engine.py` 常量一致）：默认只乘 `主伤害`；加 `--include-amounts`
  才追加 `效果数值`。**永不乘**：`系数`(SetRatio)、`持续时间`、`效果`(概率/状态)、`表现`、`标记`、
  `debuff`、`共享-不可单独改`。原因：StateAttack 里的 SetRatio（跳动间隔比）与 SetAmount 属状态参数，
  乘比例会破坏技能（改变跳动频率/附加量），并防止 SetPray(1)/SetPerform(1) 标志位被误乘。
- **重建**（换 gs / 重挖，需 capstone）：
  ```
  python scripts/dump_zhenkuang.py --gs <服务端gs> --out <dump.json> [技能ID...]
  python scripts/make_manifest.py --dump <dump.json> --out <manifest.json>
  ```
  ⚠ 中间产物 `zhenkuang_dump.json`（约 28MB）**不入库**，随时可用上面命令再生成；仓库只跟踪最终 manifest。

## 批量按比例修改（batch_engine）

- GUI：`/batch` 页 → 作用域(伤害/冷却/吟唱/释放) → 比例 → 范围(自定义技能ID / 全部真 / 全部狂 / 全部)
  → 预览表（每行含 sid/method/off/kind/old/new/status/note）→ 应用（自动备份+回读校验）。
- CLI：`batch-modify`（见上）。
- **作用域→方法映射（.symtab 实证）**：
  - `damage`：manifest 中 `shared=false` 且 `role` 落在白名单（默认 `主伤害`，`--include-amounts` 追加 `效果数值`；系数/时长/标志位永不乘）的常量（乘法）。
  - `cooldown`：`GetCoolingtime` 自有立即数（int，毫秒）。
  - `cast`（吟唱/准备）：`GetExecutetime` 自有立即数（int，毫秒）。
  - `release`（释放时间）：`GetAttackspeed`（int，攻速=每秒攻击次数）。
- **释放时间实证**：全 .symtab 无 `GetReleaseTime`/`GetCastTime` 符号；技能时序方法族仅
  GetCoolingtime x1948 / GetExecutetime x1948 / State*GetTime / GetAttackspeed x1077。
  出手间隔 `obj+0x264 = floor(攻速×0.8)−1`，故 release 映射 GetAttackspeed。
- 数值安全：int → round(old×ratio)，须 >0 且 int32 内；float/double → old×ratio 须有限。
- 共享常量(refs>1 / shared=true)跳过；无自有方法(继承默认)跳过；瞬发技能 cast 缩放 0 被拦截。
- **旧值匹配守卫（skip_guard）**：写每个常量前回读目标 gs 当前字节，与 manifest 记录旧值比对；
  不一致即跳过不写。典型：redo 版 gs 上剑刃风暴(1728/1729) 的 `State*Calculate` 已被改造成
  `jmp` 跳转区（跳到箭阵），字节与 manifest 旧值不符，会被自动拦截——**改剑刃实际伤害请改
  箭阵 602/603，不要对剑刃跑 damage 批量**。
- 写前自动备份 gs 到 `data/backup/gs.<说明>.<时间>.bak`，打补丁后回读校验。

## 攻速/出手间隔上限

`property_policy::UpdateAttack` 把攻速值夹到 `[5,300]`，上限常量 `0x12c=300`（cmp+mov 两处
立即数，138 端文件偏移约 0x61b02/0x61b0b；144 同偏移但下限钳位值不同）。出手间隔
`obj+0x264 = floor(攻速×0.8)−1`。GUI 页 `/attackspeed` 可改上限（如 8 次/秒）并显示间隔换算。

## 伤害复刻（把某技能伤害改成另一技能的公式）

思路：把目标技能伤害方法入口改写为 `jmp rel32` 跳到源技能同名方法（State19/29/39 各一），
其余字节 nop。只改伤害方法，范围/冷却/攻速等其它参数不动。改后进游戏实测同等级伤害一致。

参考案例（剑刃风暴→箭阵，已重做并校验，2026-10-17）：
- 真·剑刃风暴(1728) State19/29/39 → 真·箭阵(602) `0x865ec74/0x865ecdc/0x865ed70`
- 狂·剑刃风暴(1729) State19/29/39 → 狂·箭阵(603) `0x865f06e/0x865f0d6/0x865f16a`
- **对比实证**（data/blade_vs_arrow_cmp.txt）：6 处跳转首指令全部 jmp OK；箭阵 State39 实参
  SetDecmp=655.0 / SetPlus=6639.0 / SetRatio=3.0 / SetCombat=1 / SetPerform=0（注意 0x4423c000
  实为 655.0，非 656.0）。跳转后剑刃实际执行箭阵全部效果，直接伤害数字一致；
  剑刃独有 StateAttack(428B) 为攻击触发 debuff 参数块（10000/8/1845/178/88/2），不影响直接伤害。
- 游戏内实测步骤：备份原 gs → 覆盖服务端 gs → 重启 → 同角色同靶子各放 3 次取平均，
  D_剑刃≈D_箭阵（误差<5%）即成功；测完可用备份还原。

## 技能描述文字写回（skillstr.txt）

技能描述（含"施放需要 N 元气"、"上^96f5ff<N>^ffffff的物理伤害"等）在
`assets/skillstr.txt`（UTF-16LE，configs.pck 里 skillsrt 的明文提取）。

**布局（重要）**：该文件前段是全部名称行（偶数 idx：`<idx> "名"`），后段是全部描述块
（奇数 idx 标题行 `<idx> "^颜色名"` + 其后描述行，直到下一个 idx 行）。描述块按"奇数 idx
标题行起、到下一条 idx 行止"定位，不是按名称行紧跟。

方式一（GUI）：选中技能 → "技能描述文字"编辑区 → 保存描述 → skillstr.txt（自动备份）。
方式二（精确写回）：
```
python scripts/update_desc.py --file <skillstr.txt> --list 17281     # 打印描述块+抽取伤害数字
python scripts/update_desc.py --file <skillstr.txt> --set-dmg 17281 1770
python scripts/update_desc.py --file <skillstr.txt> --to-placeholder 17281  # 硬编码数字->%d 动态占位
```
- 说明：--set-dmg 使用偶数 idx（名称行）定位块；块内找"上^96f5ff<num>^ffffff的物理伤害"精确替换。
- `--to-placeholder`：把硬编码伤害/百分比转成 `%d` 动态占位（对齐箭阵句式），改后描述随等级动态显示。
- 替换前打印命中上下文 2 行，替换后回读校验并打印 diff；原样保留编码/行尾，不做全局重写。

**部署版 skillsrt.txt**：改完 `skillstr.txt` 后，把技能名+描述整体另存为明文部署版
`assets/skillsrt/skillsrt.txt`（UTF-16LE 带 BOM，与 configs.pck 内 skillsrt 一致），随 skill
一起同步/部署。⚠ 游戏内实际显示需把 skillsrt.txt 用完美 pck 工具（如 PckTools）打包回
configs.pck（该 pck 整文件加密，非纯文本）。注意：部分技能（如箭阵）描述是 `%d` 占位符、
无具体数字，没有可对齐目标时按需人工定夺。
**已拍板状态（勿回退）**：17280/17290（真/狂剑刃风暴）描述已用 `--to-placeholder` 改为 `%d`
动态占位，随实际伤害联动；assets/skillstr.txt 与项目副本 MD5=FD02CF9C，不要再把硬编码数字写回去。

## 每级伤害/效果表

伤害/效果 = gs 内"公式系数 + 等级 L"。例：凌风 状态1 = (L×8.4+23.5)×0.2。
`scripts/build_perlevel.py <gs路径>` 用 capstone 反汇编+浮点模拟预计算，写入
`assets/skill_perlevel.json`（GUI 读取展示 L=1..10）。改系数后需重跑刷新。
真/狂技能多为 setter 型（无每级公式），其伤害以 manifest 常量展示，不走 perlevel 表。

## 138 vs 144 差异（已知）

- 技能特性：仅 真·神威天狱(992) 不同（138 状态2 0.02/2 + 冷却30000ms；144 0.2/20 + 15000ms）。
- 攻速下限钳位：138 端下限=5，144 端同偏移下限=2（写回前按目标 gs 确认）。

## 注意事项

- 只支持带 `.symtab` 符号表的完整版 gs；客户端剥离版无法定位技能。
- 数值口径：冷却/施放/持续=毫秒；范围/距离=米；概率基础版=0~1 小数、真/狂版=百分数。
- 改好的 gs 在 skill 根 `data/current.gs`，部署到服务端 `pwserver\gamed\gs`（先备份、重启服务）。
- 全真/狂批量（--all / 全部）会命中大量技能，务必先 `--dry-run` 预览再应用。
- **仓库跟踪范围**：.gitignore 忽略 `data/` 整目录（运行期 current.gs/backup 及本地镜像的
  manifest/CHANGELOG/blade_vs_arrow_cmp/batch_test_report 不提交）。伤害 manifest 的**入库副本**在
  `assets/skill_damage_manifest.json`；`zhenkuang_dump.json` 任何位置都不入库。

## 变更记录

### 2026-10-18 修复：技能名全量进数据库（两个算法/判断缺陷）
- **缺陷1** `parse_skillstr` 描述块结束判断吞掉名称行（server.py + gs_tool.py）：旧逻辑用
  `endswith('"')` 判断描述块结束，但技能名行本身以 `"` 结尾（如 `1460  "狂战士"`），前一个描述块
  错误吞掉后续名称行 → name_map 只有 2165，约 1156 个技能名丢失。修复为**结束于下一个 idx 行**
  （`_LINE` 匹配，无论奇偶）。验证：name_map=3321、desc_map=3326；146 狂战士 / 513 狂·兽王鼓舞 /
  1728 / 602 全部命中；gs 1948 技能类缺失=0。
- **缺陷2** `gs_tool.py` SKILLSTR 路径多一层 `..`：脚本在项目根运行时指向
  `D:\完美单机\assets\skillstr.txt`（错误）→ `list 狂战士` 0 匹配。修复为自适应定位（本目录有
  data+assets 用本目录，否则回退上级），与 server.py 一致。
- **验证**：`list 狂战士`=146、`list 兽王`=82 兽王鼓舞 + 513 狂·兽王鼓舞 等全部；`list`（空）=
  3321 全量。项目与 skill 两处均已同步修复。
