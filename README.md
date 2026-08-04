# 按键同步 KeySync

> 全局按键映射 + 自动连点 工具 —— 为 VRChat / 游戏玩家打造

粉色小工具，运行在 Windows 系统托盘。把按键从一个键同步到另一个键，还能按住左键自动连点。

## ✨ 功能

- **按键映射** — 按下源键时，自动向目标应用注入目标键（例如 `W → Left Shift`）
- **自动连点** — 勾选后，按住鼠标左键即自动快速连点，松开即停（支持半自动武器扳机循环）
- **应用白名单** — 仅当焦点窗口属于列表中的应用时，同步/连点才生效
- **聊天暂停** — 在游戏内打字（按 Enter / T 等聊天键）时自动暂停同步，打字结束自动恢复
- **全局开关** — 主开关一键启停（默认 `Pause` 热键切换）
- **托盘常驻** — 系统托盘图标，后台运行不打扰

## 🚀 使用

### 直接运行（开发/自用）

双击 `按键同步.pyw`（无黑框），或使用桌面快捷方式。

依赖：

```bash
python -m pip install -r requirements.txt
```

### 首次启动

1. 在 **同步应用** 列表添加目标应用（浏览进程 / 手动输入进程名，例如 `VRChat.exe`）
2. 在 **按键映射** 添加源键 → 目标键
3. 勾选 **✧ 启用同步**，进游戏即可

### 界面开关

| 控件 | 作用 |
|------|------|
| ✧ 启用同步 (Pause 键切换) | 全局主开关 |
| ✎ 输入时暂停同步 | 聊天键自动暂停（可对每个应用单独配置聊天键） |
| 🖱 按住左键自动连点 | 自动连点开关（间隔可调，20~500ms） |

### 托盘菜单

- 左键单击：显示/隐藏配置窗口
- 右键：显示配置窗口 / 启用同步 / 退出按键同步

## 🛠 开发

### 项目结构

```
main.py              入口：单实例锁、管理员检查、组件组装
config.py            线程安全 JSON 配置（%APPDATA%\KeySync\config.json）
foreground_monitor.py 前台进程检测（300ms 轮询 GetForegroundWindow）
keyboard_hook.py     全局键盘钩子（source→target 注入、聊天暂停、切换热键）
autoclicker.py       自动连点（WH_MOUSE_LL 鼠标钩子 + SendInput 注入）
gui.py               CustomTkinter 粉色配置界面
tray.py              原生 Shell_NotifyIconW 托盘
process_picker.py    进程选择对话框
```

### 打包发布（功能冻结后才做）

```bash
build.bat        # 或: python -m PyInstaller 按键同步.spec --clean --noconfirm
```

产物：`dist\按键同步\按键同步.exe`

> ⚠️ 开发阶段直接用 `按键同步.pyw` 运行，打包会拖慢迭代且引入 ctypes 打包坑。

### 测试

```bash
python test_keyboard_hook.py   # 卡键回归：12 场景
python test_autoclicker.py     # 连点：27 场景（含真实钩子 smoke）
```

### 注意

- 目标应用以管理员运行时，本工具也需以管理员运行
- 配置路径 `%APPDATA%\KeySync\config.json` 保留英文名（兼容旧版，勿改）
- 单实例：同进程只允许一个实例（mutex: `Global\按键同步_SingleInstance`）

## 📜 更新日志

- **v1.2** 正式更名「按键同步」；自动连点（含半自动武器支持）；托盘退出修复；GUI 图标
- **v1.1** 启用开关三通道统一；聊天暂停（typing_pause）复活；单实例兼容中文 exe 名
