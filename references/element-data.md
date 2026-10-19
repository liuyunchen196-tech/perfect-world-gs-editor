# element.data（元素/物品数据）结构与自研工具指南

来源：iweb 后台解析代码（element.php/rtype.php/element_config.php）+ 155 端图文教程，已实测 138 国服端文件。

## 文件位置

- 服务端：`pwserver/gamed/config/element.data`
- 客户端：`element/data/elements.data`（国服有多份，如 `138修改文件/`、`纯端/`、`初始/`）
- iweb 后台：`var/www/html/iweb/system/data/elements.data`

## 字段格式定义（权威）

iweb 后台用 **element.cfg** 定义每个类目(list)的字段，位置：
`iweb/system/data/character/{版本}/element.cfg`（138 提供 1.5.1_101 / 1.5.3_145 / 1.5.5_156）。

element.cfg 布局（UTF-8 文本）：
```
231            ← 第1行：类目总数
58             ← 第2行：dialog_list 编号
（每个类目 4 行）
001 - EQUIPMENT_ADDON   ← 类目编号+名称
4                      ← dialog 标志（解析时忽略，可为 AUTO）
ID;Name;num_params;...  ← 字段名（分号分隔）
int32;wstring:64;...    ← 字段类型（分号分隔，与字段名一一对应）
```

字段类型（rtype.php，全小端/机器序）：
- `int16` 2 字节有符号、`int32` 4 字节、`int64` 8 字节、`float` 4 字节
- `string:N` N 字节定长 GB2312（物品名/文件路径等）
- `wstring:N` N 字节定长 UTF-16LE（中文名）

## element.data 二进制结构（实测 138 国服）

```
offset 0  : int16 version(29) + int16 version2(0x2000=加密标志) + int32 timestamp
offset 8  : int32 2060(0x080C, 数据区起点?)、int32 216
其后      ：明文大类目目录，持续数万字节
           每组目录项 = 若干 int32/float(变长) + int32 递增偏移 + UTF-16 名称(A_A11/A_D21/C_A11...)
数据区    ：按类目存储，加密（国服 version2=0x2000）
```

**⚠ 138 国服所有 element.data（客户端+服务端+iweb）头部均加密**（version2=0x2000，头部明文目录 + 数据区加密）。iweb 后台 element.php 的 `get_info` 只读 8 字节头就进数据，**仅适用于未加密版本**，对 138 国服加密文件会错位。

## 关键类目/小类代码（155 教程）

| 用途 | 类目(list) | 说明 |
|---|---|---|
| 装备 | 004 WEAPON_ESSENCE | 武器；require_strength/agility 等 |
| 防具 | 007 ARMOR_ESSENCE | 装备位置/防御/耐久 |
| 饰品/装备小类 | 011 饰品类 | 新建物品用小类：饰品171/234/247、武器1、上衣16、裤子64、头盔2、鞋子128、护腕256、项链4、披风8、腰饰32、戒指512/1024 |
| 时装 | — | 手套65536、头发33554432、武器536870912、上衣8192、鞋子32768、裤子16384、头盔2、喇叭524288 |
| 套装效果 | 086 | 套装 |
| 生产配方 | 065 | 配方表 |
| 称号 | 165 | 名称/颜色/属性(11字体颜色/12位置/13-18属性) |
| 战宠 | 090/162 | |
| 天书 | 108 | |
| 战灵 | 180 | |
| 镌刻 | 140 | |
| 物品奖牌 | 071（新版075） | 任务触发物品 |
| 任务卷轴 | 070（新版075） | 指向任务编辑器任务ID |
| 星盘属性 | 192 | 属性/几率 |

## 装备颜色/描述（configs.pck，文本文件）

- `item_color.txt`：物品ID→掉落颜色（0~8 九种），格式 `ID 颜色码`，追加新物品：`51001 8`
- `item_ext_desc.txt`：物品/武器文字说明，格式 `ID  "说明文字"`（`\r` 空行、`^FF0000` 颜色码）
- `item_ext_prop.txt`：中文属性名
- 颜色码：`^FF0000` 血红、`^FFAC00` 橙等；EL 数值色码 `-65536`、新版 `4294901760`

## 添加物品（新物品流程，教程）

1. EL 类目【装备】饰品小类新建项：名称加色码 `^FF0000名`；小类代码（如饰品 131072）；ID 唯一（如 51001，别重复，否则刷不出）。
2. item_color.txt 加 `51001 8`；item_ext_desc.txt 加 `51001  "文字说明"`。
3. 可选：改属性值（item_ext_prop.txt）；星盘垃圾属性改几率 0。
4. 服务端 gamed/config + 客户端 element/data 需用 WinScp 同步（路径：root/pwserver/gamed/config、客户端 element/data）。
5. 商城同步（gshopsev.data）否则乾坤袋 51000+ 无法启动。

## 自研解析器参考（iweb 后台源码）

- `iweb/system/libs/element/rtype.php`：字段读取函数（int16/int32/int64/float/string GB2312/wstring UTF-16）。
- `iweb/system/libs/element/element_config.php`：读 element.cfg → 每 4 行一个类目，字段名+类型。
- `iweb/system/libs/element/element.php`：读头部(get_info)+逐类目 int32 数量+逐记录；list 20/100 有额外 head(4)+count(4)+body+till(4)。
- 生成：`iweb/system/data/items.json`（后台解析后的 物品ID→名称/图标/类目/颜色）。

## 复用 EL 编辑器修改 element.data（推荐方案，无需破译加密）

数据区加密逆向困难，改用**复用现成 EL 编辑器**方案：EL 编辑器负责读/改/写加密 element.data，
我们（`element_manager.py`）负责启动、备份、分发同步。

分工：
- EL 编辑器（用户 GUI）：打开 element.data → 在界面里修改 → 保存。
- `element_manager.py`（脚本，AI 或用户可调）：备份原文件 → 启动 EL 编辑器 → 检测保存 → 分发客户端/服务端 → 校验。

`scripts/element_manager.py` 命令：
```
python element_manager.py open    <element.data路径>                     备份→启动EL编辑器打开
python element_manager.py backup  <element.data路径>                     仅备份到同级 备份data/
python element_manager.py sync    <已保存文件> --client <path> [--server <path>]   备份目标→复制→hash校验
python element_manager.py check   <element.data路径>                    打印 size/mtime/md5
```

工作流：
1. `open` 自动把原 element.data 备份到其所在目录下新建的 `备份data/`（`elements.before_edit.<时间>.data`），
   再 `Start-Process` 启动 EL 编辑器打开该文件（**EL 编辑器支持命令行带文件路径参数直接打开**，实测可用）。
2. 用户在「蜜蜂EL编辑器」窗口里修改并保存（直接覆盖该文件，仍为加密格式，但内容已改）。
3. `sync` 把保存后的文件复制到目标位置（先备份目标原文件到 `备份data/`），并 md5+size 校验一致性。

常用路径：
- 客户端 144：`D:\完美单机\完美世界国服144\2客户端\element\data\elements.data`（19411213 B, v2=0x3000）
- 服务端 138：`...\root\pwserver\gamed\config\elements.data`（29662697 B, v2=0x2000）
- EL 编辑器：`D:\完美单机\国服工具\EL编辑器\el编辑器.exe`（Delphi 原生；未注册为 Windows 应用，
  computer_use 的 GUI 自动化无法直接启动/操作它，只能由用户自己在桌面操作窗口，脚本仅负责启动进程）。

⚠ 注意：客户端 144 与服务端 138 的 element.data 是**不同版本**（头部 version2、大小、类目字段都不同），
分发时务必匹配版本（改 144 客户端就分发到 144 客户端/对应服务端），不要把 144 的文件直接覆盖 138 服务端。

## 加密逆向现状（改用复用方案，原逆向暂停）

- 头部明文目录已定位（类目项：值组 + 递增偏移 + UTF-16 名称）。
- **数据区加密算法未逆向**（138 v2=0x2000 / 144 v2=0x3000；已试 3 版本 element.cfg 精确解析、138/144
  两加密版本对比、反汇编 el编辑器.exe（Delphi 原生/无符号/延迟导入/魔数无明文命中）均未突破）。
- **已改用复用 EL 编辑器方案**（见上），不再需要破译加密。若日后仍需自研解析器，
  突破口：用户用 EL 编辑器导出明文版 element.data 作明文-密文对比。
