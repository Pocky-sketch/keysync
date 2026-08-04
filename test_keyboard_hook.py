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


# 物理键状态 (is_pressed), 默认 w 按着 — 测试可改
_pressed_state = {"w": True, "a": True}


def _is_pressed(k):
    return _pressed_state.get(k, False)


fake_keyboard.press = _press
fake_keyboard.release = _release
fake_keyboard.is_pressed = _is_pressed
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


def ev(name, etype, injected=False):
    return types.SimpleNamespace(name=name, event_type=etype, is_injected=injected)


def reset():
    fake_calls.clear()
    kh._keys_down.clear()
    kh._chat_paused_until = 0.0
    kh._pending_reinject = None
    kh._suppress_key = None
    _pressed_state.update({"w": True, "a": True})


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
_on_toggle()  # 重新开启

# --- 场景 6: Shift+Tab 抑制 (Steam 快捷键泄漏) ---
reset()
cfg.add_mapping("w", "left shift")  # W → Shift
_on_key_event(ev("w", "down"))      # 按住 W → 注入 shift
ok &= expect("抑制: W down 注入 left shift", ("press", "left shift") in fake_calls)
fake_calls.clear()
ret = _on_key_event(ev("tab", "down"))  # 按 Tab
ok &= expect("抑制: Tab down 被吞 (返回 False)", ret is False)
ok &= expect("抑制: 注入的 left shift 已释放", ("release", "left shift") in fake_calls)
ok &= expect("抑制: 重发干净 Tab down", ("press", "tab") in fake_calls)
ok &= expect("抑制: 抑制状态已记录", kh._pending_reinject is not None)
fake_calls.clear()
ret2 = _on_key_event(ev("tab", "up"))  # Tab 松开
ok &= expect("抑制: Tab up 放行", ret2 is None)
ok &= expect("抑制: Tab up 重新注入 left shift (W 仍按着)", ("press", "left shift") in fake_calls)
ok &= expect("抑制: 抑制状态已清除", kh._pending_reinject is None)
ok &= expect("抑制: _keys_down 恢复", kh._keys_down.get("w", False) is True)
fake_calls.clear()
_on_key_event(ev("w", "up"))        # 松开 W
ok &= expect("抑制: W up 释放 left shift", ("release", "left shift") in fake_calls)

# --- 场景 8: W 在 Tab 前松开 → 不错误恢复 Shift (卡键根因) ---
reset()
_on_key_event(ev("w", "down"))      # W down → Shift down
fake_calls.clear()
ret = _on_key_event(ev("tab", "down"))  # Tab down → 抑制
ok &= expect("先松W: Tab down 吞+释放 Shift", ret is False and ("release", "left shift") in fake_calls)
ok &= expect("先松W: pending 含 w", kh._pending_reinject.get("w") == "left shift")
fake_calls.clear()
_pressed_state["w"] = False          # 物理 W 已松开
_on_key_event(ev("w", "up"))         # W up → 清理 pending
ok &= expect("先松W: W up 从 pending 移除", kh._pending_reinject is None or "w" not in kh._pending_reinject)
fake_calls.clear()
_on_key_event(ev("tab", "up"))       # Tab up → 不应恢复 Shift
ok &= expect("先松W: Tab up 不恢复 Shift (无卡键)", ("press", "left shift") not in fake_calls)
ok &= expect("先松W: _keys_down 无 w", kh._keys_down.get("w", False) is False)

# --- 场景 9: 注入的事件 (is_injected) 直接放行 ---
reset()
_on_key_event(ev("w", "down"))
fake_calls.clear()
ret = _on_key_event(ev("tab", "down", injected=True))  # 模拟重发的干净 Tab
ok &= expect("注入事件: 直接放行不吞", ret is None)
ok &= expect("注入事件: 不触发抑制 (无 release/press)", len(fake_calls) == 0)

# --- 场景 10: 抑制后 toggle 关闭清理 ---
reset()
_on_key_event(ev("w", "down"))
_on_key_event(ev("tab", "down"))    # 触发抑制
ok &= expect("抑制+关闭: 抑制状态存在", kh._pending_reinject is not None)
_on_toggle()  # 关闭同步
ok &= expect("抑制+关闭: 抑制状态清空", kh._pending_reinject is None and kh._suppress_key is None)
_on_toggle()  # 重新开启

# 清理: 移除 w 映射 (保持后续测试环境干净)
cfg.remove_mapping(0)  # w→left shift
cfg.add_mapping("a", "b")

print()
print("全部通过" if ok else "存在失败项!")
sys.exit(0 if ok else 1)
