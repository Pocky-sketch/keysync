"""按键同步 卡键回归测试 — mock keyboard 模块验证暂停/关闭时的键释放。

模拟场景:
1. 正常映射 A→B: down 注入 press(B), up 注入 release(B)
2. 卡键场景: A down(注入) → Enter(聊天键, 触发暂停) → A up 必须 release(B)
3. 暂停中新的 A down → 不注入
4. 暂停超时后 A down → 恢复注入
5. toggle 关闭 → 释放所有已注入键
"""
import sys
import time
import types

# ---- mock keyboard 模块 ----
fake_calls = []

fake_keyboard = types.ModuleType("keyboard")
fake_keyboard.KEY_DOWN = "down"
fake_keyboard.KEY_UP = "up"
fake_keyboard.KeyboardEvent = object  # 类型注解需要


def _press(k):
    fake_calls.append(("press", k))


def _release(k):
    fake_calls.append(("release", k))


fake_keyboard.press = _press
fake_keyboard.release = _release
sys.modules["keyboard"] = fake_keyboard

# ---- mock 前台监视器: 始终允许 ----
class FakeMonitor:
    def get_foreground_name(self):
        return "VRChat.exe"

    def is_allowed(self):
        return True


# ---- 临时 config ----
import tempfile
import os

cfg_dir = tempfile.mkdtemp()
cfg_path = os.path.join(cfg_dir, "config.json")

from config import Config

cfg = Config(cfg_path)
cfg.add_app("VRChat.exe")
cfg.add_mapping("a", "b")  # 源键 a → 目标键 b
cfg.set_typing_pause(True)  # 聊天暂停开启

from keyboard_hook import KeyboardHook, _on_key_event, _on_toggle, _chat_paused_until, _keys_down

hook = KeyboardHook(FakeMonitor(), cfg)

import keyboard_hook as kh


def ev(name, etype):
    return types.SimpleNamespace(name=name, event_type=etype)


def reset():
    fake_calls.clear()
    kh._keys_down.clear()
    kh._chat_paused_until = 0.0


def expect(desc, cond):
    print(("PASS" if cond else "FAIL") + f" — {desc}")
    return cond


ok = True

# --- 场景 1: 正常映射 ---
reset()
_on_key_event(ev("a", "down"))
ok &= expect("正常: A down 注入 press(b)", ("press", "b") in fake_calls)
_on_key_event(ev("a", "up"))
ok &= expect("正常: A up 注入 release(b)", ("release", "b") in fake_calls)

# --- 场景 2: 卡键场景 (核心修复点) ---
reset()
_on_key_event(ev("a", "down"))  # 注入 B down
ok &= expect("卡键场景: A down 注入 press(b)", ("press", "b") in fake_calls)
_on_key_event(ev("enter", "down"))  # 聊天键 → 触发暂停
ok &= expect("卡键场景: Enter 触发暂停", kh._chat_paused_until > 0)
_on_key_event(ev("a", "up"))  # 松开 A — 修复前这里会吞掉 KEY_UP!
ok &= expect("卡键场景: 暂停中 A up 仍 release(b) — 修复生效", ("release", "b") in fake_calls)
ok &= expect("卡键场景: _keys_down 已清空", kh._keys_down.get("a", False) is False)

# --- 场景 3: 暂停中不注入 ---
reset()
_on_key_event(ev("enter", "down"))  # 触发暂停
_on_key_event(ev("a", "down"))
ok &= expect("暂停中: A down 不注入", ("press", "b") not in fake_calls)
_on_key_event(ev("a", "up"))
ok &= expect("暂停中: A up 无多余 release", ("release", "b") not in fake_calls)

# --- 场景 4: 暂停超时恢复 ---
reset()
_on_key_event(ev("enter", "down"))
kh._chat_paused_until = time.time() - 1  # 强行让超时过期
_on_key_event(ev("a", "down"))
ok &= expect("超时后: A down 恢复注入", ("press", "b") in fake_calls)
_on_key_event(ev("a", "up"))
ok &= expect("超时后: A up release(b)", ("release", "b") in fake_calls)

# --- 场景 5: toggle 关闭释放所有注入键 ---
reset()
_on_key_event(ev("a", "down"))
fake_calls.clear()
_on_toggle()  # 关闭同步
ok &= expect("toggle 关闭: release(b) 被调用", ("release", "b") in fake_calls)
ok &= expect("toggle 关闭: _keys_down 清空", len(kh._keys_down) == 0)

# toggle 后触发 _config_change_cb (GUI 同步通知)
cb_calls = []
kh._config_change_cb = lambda: cb_calls.append(1)
_on_toggle()  # 再 toggle (重新开启)
ok &= expect("toggle: 触发 config_change_cb", len(cb_calls) == 1)
kh._config_change_cb = None
_on_toggle()  # 恢复关闭状态? 不 — 上面 toggle 后是开, 再 toggle 回关
ok &= expect("toggle 恢复: 关闭同步", kh._config.is_enabled() is False)
_on_toggle()  # 重新开启, 保持测试环境一致

# --- 场景 6: 映射源 W→Shift 正常注入与释放 (无抑制逻辑) ---
reset()
cfg.add_mapping("w", "left shift")  # W → Shift
_on_key_event(ev("w", "down"))      # 按住 W → 注入 shift
ok &= expect("映射: W down 注入 left shift", ("press", "left shift") in fake_calls)
fake_calls.clear()
ret = _on_key_event(ev("tab", "down"))  # Tab 不触发任何抑制
ok &= expect("映射: Tab down 不受影响 (无抑制逻辑)", ("release", "left shift") not in fake_calls)
ok &= expect("映射: Tab down 不吞键 (键盘安全)", ret is not False)
ok &= expect("映射: Tab down 无重发", ("press", "tab") not in fake_calls)
fake_calls.clear()
_on_key_event(ev("tab", "up"))
ok &= expect("映射: Tab up 无副作用", len(fake_calls) == 0)
fake_calls.clear()
_on_key_event(ev("w", "up"))        # 松开 W
ok &= expect("映射: W up 释放 left shift", ("release", "left shift") in fake_calls)
ok &= expect("映射: 无卡键 (_keys_down 清空)", kh._keys_down.get("w", False) is False)

# 清理: 移除 w 映射 (保持后续测试环境干净)
cfg.remove_mapping(0)  # w→left shift
cfg.add_mapping("a", "b")

# --- 场景 7: 热键匹配 (add_hotkey 本机失效 → 从原始钩子流匹配) ---
import autoclicker as ac_mod
ac_mod._config = cfg  # toggle_from_hotkey 需要模块级 _config

reset()
cfg.set_enabled(True)
cfg.set_toggle_hotkey("pause")
cfg.set_autoclick_hotkey("f9")
cfg.set_autoclick_enabled(False)

_on_key_event(ev("pause", "down"))
ok &= expect("热键: Pause down 触发 toggle 关闭", kh._config.is_enabled() is False)
_on_key_event(ev("pause", "up"))
ok &= expect("热键: Pause up 不重复 toggle", kh._config.is_enabled() is False)
_on_key_event(ev("pause", "down"))
ok &= expect("热键: Pause 再次触发 toggle 开启", kh._config.is_enabled() is True)

_on_key_event(ev("f9", "down"))
ok &= expect("热键: f9 down 触发连点开启", cfg.is_autoclick_enabled() is True)
_on_key_event(ev("f9", "down"))
ok &= expect("热键: f9 再次触发连点关闭", cfg.is_autoclick_enabled() is False)

cfg.set_autoclick_hotkey("mouse4")
_on_key_event(ev("mouse4", "down"))
ok &= expect("热键: mouse 热键不匹配键盘钩子", cfg.is_autoclick_enabled() is False)

# 热键匹配在 enabled 门控之前 → 关闭状态下热键仍可切换
cfg.set_autoclick_hotkey("f9")
cfg.set_enabled(False)
_on_key_event(ev("f9", "down"))
ok &= expect("热键: 同步关闭时 f9 仍可切换连点", cfg.is_autoclick_enabled() is True)

cfg.set_autoclick_hotkey("")
cfg.set_autoclick_enabled(False)
ac_mod._config = None  # 清理模块级引用

print()
print("全部通过" if ok else "存在失败项!")
sys.exit(0 if ok else 1)
