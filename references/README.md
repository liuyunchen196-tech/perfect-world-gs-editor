# 完美世界单机 · gs 技能修改器（skill 版）

离线查看/修改完美世界单机（国服 138/144 端）服务端 `gs` 的技能特性，可回滚。

## 目录结构

```
perfect-world-gs-editor/
├── SKILL.md               # 使用说明（触发+工作流）
├── references/
│   └── README.md          # 本文档（详细手册）
├── scripts/
│   ├── gs_tool.py         # CLI 统一工具（list/view/modify/export-db）
│   ├── server.py          # 本地 GUI 后端（纯标准库 http.server，端口 7860）
│   ├── build_perlevel.py  # 每级伤害/效果表生成器（需 capstone，仅构建期）
│   ├── start_gui.bat      # Windows 一键启动 GUI
│   └── static/
│       ├── index.html     # 技能修改前端
│       └── attackspeed.html # 攻速上限/出手间隔独立前端
└── assets/
    ├── skillstr.txt       # 技能名表（UTF-16LE；技能代码 = idx//10）
    └── skill_perlevel.json # 每级伤害/效果表（可选）
```

## 启动 GUI

需 Python 3（GUI 运行期零第三方依赖，完全离线）。Windows 可双击 `scripts/start_gui.bat`，
或 `python scripts/server.py`，然后浏览器开 `http://127.0.0.1:7860/`。

GUI 流程：
1. 点「读取 gs」，弹窗选择带符号表的服务端 gs（如 138 端 `pwserver\gamed\gs`）。
2. 左侧搜索/选技能；勾「仙魔联动」则真/狂变体一起改。
3. 查看：各级伤害/效果表（L=1..10 按公式逐级）、每级需求/消耗表、伤害公式系数、其它特性。
4. 改值 → 点「保存修改」：自动备份到 skill 根 `data/backup/`，打补丁写回，回读校验。
5. 攻速上限：主页面右上「⚡ 攻速上限修改 →」或直接开 `/attackspeed`。

## CLI 用法

```
python scripts/gs_tool.py list  横扫千军
python scripts/gs_tool.py view  横扫千军 --gs <gs路径>
python scripts/gs_tool.py modify 横扫千军 --gs <gs路径> --set "范围=12" --set "冷却时长(毫秒)=6000"
python scripts/gs_tool.py modify 凌风 --gs <gs路径> --off 0x914688 --type double --value 0.6
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

## 攻速/出手间隔上限

`property_policy::UpdateAttack` 把攻速值夹到 `[5,300]`，上限常量 `0x12c=300`（cmp+mov 两处
立即数，138 端文件偏移约 0x61b02/0x61b0b；144 同偏移但下限钳位值不同）。出手间隔
`obj+0x264 = floor(攻速×0.8)−1`。GUI 页 `/attackspeed` 可改上限（如 8 次/秒）并显示间隔换算。

## 伤害复刻（把某技能伤害改成另一技能的公式）

思路：把目标技能伤害方法入口改写为 `jmp rel32` 跳到源技能同名方法（State19/29/39 各一），
其余字节 nop。只改伤害方法，范围/冷却/攻速等其它参数不动。改后进游戏实测同等级伤害一致。

参考案例（剑刃风暴→箭阵，当前生效）：
- 真·剑刃风暴(1728) State19/29/39 → 真·箭阵(602) `0x865ec74/0x865ecdc/0x865ed70`
- 狂·剑刃风暴(1729) State19/29/39 → 狂·箭阵(603) `0x865f06e/0x865f0d6/0x865f16a`
- 剑刃风暴原伤害：状态1=30.0(off 0x6acb5c)、状态2=120.0(off 0x6acbe0)，薄封装调共享引擎。

## 每级伤害/效果表

伤害/效果 = gs 内"公式系数 + 等级 L"。例：凌风 状态1 = (L×8.4+23.5)×0.2。
`scripts/build_perlevel.py <gs路径>` 用 capstone 反汇编+浮点模拟预计算，写入
`assets/skill_perlevel.json`（GUI 读取展示 L=1..10）。改系数后需重跑刷新。

## 138 vs 144 差异（已知）

- 技能特性：仅 真·神威天狱(992) 不同（138 状态2 0.02/2 + 冷却30000ms；144 0.2/20 + 15000ms）。
- 攻速下限钳位：138 端下限=5，144 端同偏移下限=2（写回前按目标 gs 确认）。

## 注意事项

- 只支持带 `.symtab` 符号表的完整版 gs；客户端剥离版无法定位技能。
- 数值口径：冷却/施放/持续=毫秒；范围/距离=米；概率基础版=0~1 小数、真/狂版=百分数。
- 改好的 gs 在 skill 根 `data/current.gs`，部署到服务端 `pwserver\gamed\gs`（先备份、重启服务）。
