"""AutoClicker 测试 — 判定逻辑 + 配置 + 真实钩子 smoke 测试。

在真实 Windows 环境跑: start() 装 WH_MOUSE_LL 钩子, 验证线程能正常
启停 (不会注入点击, 因为 autoclick 默认关闭且无人按住左键)。
"""
import os
import sys
import tempfile
import time
import ctypes

# ---- mock keyboard_hook 模块 (autoclicker import 它) ----
import types

fake_kh = types.ModuleType("keyboard_hook")
fake_kh.is_chat_paused = lambda: False
sys.modules["keyboard_hook"] = fake_kh

from config import Config
from autoclicker import AutoClicker, _inject_click, _pressed

# ---- 配置 ----
cfg_dir = tempfile.mkdtemp()
cfg = Config(os.path.join(cfg_dir, "config.json"))
cfg.add_app("VRChat.exe")

# 模块级 _config (钩子回调需要)
import autoclicker as ac_mod
ac_mod._config = cfg


class FakeMonitor:
    def __init__(self, allowed=True):
        self._allowed = allowed

    def is_allowed(self):
        return self._allowed

    def get_foreground_name(self):
        return "VRChat.exe"


ok = True


def expect(desc, cond):
    global ok
    ok &= bool(cond)
    print(("PASS" if cond else "FAIL") + f" — {desc}")


# ---- 配置往返 ----
cfg.set_autoclick_enabled(True)
expect("配置: autoclick_enabled 持久化", cfg.is_autoclick_enabled() is True)
cfg.set_autoclick_enabled(False)
expect("配置: autoclick_enabled 关闭", cfg.is_autoclick_enabled() is False)

cfg.set_autoclick_interval(50)
expect("配置: interval 50ms", cfg.get_autoclick_interval() == 50)
cfg.set_autoclick_interval(5)   # clamp 下限
expect("配置: interval clamp 到 20", cfg.get_autoclick_interval() == 20)
cfg.set_autoclick_interval(9999)  # clamp 上限
expect("配置: interval clamp 到 500", cfg.get_autoclick_interval() == 500)
cfg.set_autoclick_interval(100)
expect("配置: interval 恢复 100", cfg.get_autoclick_interval() == 100)

# ---- _should_click 判定逻辑 ----
ac = AutoClicker(cfg, FakeMonitor())

# 未启动 → False
expect("判定: 未启动不连点", ac._should_click() is False)

# 模拟启动状态
ac._running = True
expect("判定: 未按住左键不连点 (_pressed=False)", ac._should_click() is False)

ac._pressed_override = True
import autoclicker
autoclicker._pressed = True
expect("判定: 按住但 autoclick 未开启 → False", ac._should_click() is False)

cfg.set_autoclick_enabled(True)
expect("判定: 按住 + 开启 → True", ac._should_click() is True)

# 主开关关闭 → False
cfg.set_enabled(False)
expect("判定: 主开关关闭 → False", ac._should_click() is False)
cfg.set_enabled(True)
expect("判定: 主开关恢复 → True", ac._should_click() is True)

# 聊天暂停 → False
fake_kh.is_chat_paused = lambda: True
expect("判定: 聊天暂停 → False", ac._should_click() is False)
fake_kh.is_chat_paused = lambda: False
expect("判定: 暂停解除 → True", ac._should_click() is True)

# 白名单外 → False
ac2 = AutoClicker(cfg, FakeMonitor(allowed=False))
ac2._running = True
expect("判定: 白名单外 → False", ac2._should_click() is False)
autoclicker._pressed = False

# ---- 真实钩子 smoke 测试 (真实 Windows) ----
ac3 = AutoClicker(cfg, FakeMonitor())
ac3.start()
time.sleep(0.5)
expect("smoke: 钩子线程存活", ac3._thread is not None and ac3._thread.is_alive())
expect("smoke: 连点线程存活", ac3._click_thread is not None and ac3._click_thread.is_alive())
expect("smoke: 钩子已安装", ac3._hook_handle is not None)
ac3.stop()
time.sleep(0.2)
expect("smoke: stop 后钩子线程退出", not (ac3._thread and ac3._thread.is_alive()))
expect("smoke: stop 后连点线程退出", not (ac3._click_thread and ac3._click_thread.is_alive()))
expect("smoke: stop 后钩子句柄已释放", ac3._hook_handle is None)

# 重复 start 幂等
ac3.start()
ac3.start()  # 不应炸
time.sleep(0.3)
ac3.stop()
expect("smoke: 重复 start/stop 无异常", True)

# _inject_click 至少不抛异常 (SendInput 调用)
_inject_click(0.0)
expect("注入: _inject_click(0.0) 不抛异常", True)

# ---- 注入时序: down/up 分开调用 (半自动武器修复) ----
import autoclicker
calls = []


def fake_sendinput(n, ptr, size):
    # 解码 dwFlags 判断 down/up
    ev = ctypes.cast(ptr, ctypes.POINTER(autoclicker.INPUT)).contents
    calls.append(ev.u.mi.dwFlags)
    return n


orig_si = autoclicker._user32.SendInput
autoclicker._user32.SendInput = fake_sendinput
try:
    autoclicker._inject_click(0.0)
finally:
    autoclicker._user32.SendInput = orig_si
expect("注入: SendInput 调用 2 次 (down+up 分离)",
       len(calls) == 2)
expect("注入: 先 down 后 up",
       calls[0] == autoclicker.MOUSEEVENTF_LEFTDOWN
       and calls[1] == autoclicker.MOUSEEVENTF_LEFTUP)

# hold 时间推导 (半周期: down→hold→up, 按住=间隔一半)
interval, hold = autoclicker._timing(50)
expect("注入: 间隔推导 (50ms → 0.05s)", abs(interval - 0.05) < 0.001)
expect("注入: hold 推导 (50ms 间隔 → 25ms)", abs(hold - 0.025) < 0.001)

# _timing 边界
i, h = autoclicker._timing(5)      # clamp 下限 20ms
expect("注入: interval clamp 20ms", abs(i - 0.02) < 0.001)
i, h = autoclicker._timing(9999)   # clamp 上限 500ms
expect("注入: interval clamp 500ms", abs(i - 0.5) < 0.001)
expect("注入: hold 上限 40ms", h <= 0.040)
i, h = autoclicker._timing(20)
expect("注入: 快速间隔 hold 保底 8ms", h >= 0.008)
i, h = autoclicker._timing(500)
expect("注入: 慢速间隔 hold 40ms", abs(h - 0.040) < 0.001)

# ---- hold 模式 (全自动武器) ----
cfg.set_autoclick_mode("hold")
expect("配置: mode 往返 hold", cfg.get_autoclick_mode() == "hold")
cfg.set_autoclick_mode("weird")
expect("配置: 非法 mode 回退 click", cfg.get_autoclick_mode() == "click")
cfg.set_autoclick_mode("hold")

ac4 = AutoClicker(cfg, FakeMonitor())
ac4._running = True
calls2 = []


def fake_si2(n, ptr, size):
    ev = ctypes.cast(ptr, ctypes.POINTER(autoclicker.INPUT)).contents
    calls2.append(ev.u.mi.dwFlags)
    return n


autoclicker._user32.SendInput = fake_si2
try:
    # 物理按住 + 全部开关开启 → 注入 down 一次并保持
    autoclicker._pressed = True
    ac4._hold_cycle()
    ac4._hold_cycle()  # 再跑一次不应重复注入
    expect("hold: 按住注入 LEFT DOWN 一次", calls2.count(autoclicker.MOUSEEVENTF_LEFTDOWN) == 1)
    expect("hold: 保持状态已记录", autoclicker._injected_down is True)

    # 松开 → 注入 up
    autoclicker._pressed = False
    ac4._hold_cycle()
    expect("hold: 松开注入 LEFT UP", calls2.count(autoclicker.MOUSEEVENTF_LEFTUP) == 1)
    expect("hold: 保持状态清除", autoclicker._injected_down is False)

    # 再按 → 再次注入 down
    autoclicker._pressed = True
    ac4._hold_cycle()
    expect("hold: 再次按住注入 down", calls2.count(autoclicker.MOUSEEVENTF_LEFTDOWN) == 2)

    # stop 释放
    ac4._running = False
    ac4.stop()
    expect("hold: stop 释放 LEFT UP", calls2.count(autoclicker.MOUSEEVENTF_LEFTUP) == 2)
finally:
    autoclicker._user32.SendInput = orig_si
    autoclicker._pressed = False
    autoclicker._injected_down = False

cfg.set_autoclick_mode("click")

# ---- 快捷键配置往返 (同 Pause 键开关同步的模式) ----
cfg.set_autoclick_hotkey("mouse4")
expect("热键: mouse4 往返", cfg.get_autoclick_hotkey() == "mouse4")
cfg.set_autoclick_hotkey("f9")
expect("热键: f9 往返", cfg.get_autoclick_hotkey() == "f9")
cfg.set_autoclick_hotkey("")
expect("热键: 清空", cfg.get_autoclick_hotkey() == "")
cfg.set_autoclick_hotkey("mouse5")

# ---- toggle_autoclick ----
cfg.set_autoclick_enabled(False)
expect("toggle: 从关到开", cfg.toggle_autoclick() is True)
expect("toggle: 从开到关", cfg.toggle_autoclick() is False)
expect("toggle: 配置持久化", cfg.is_autoclick_enabled() is False)

# ---- 侧键触发 toggle (mock SendInput 防真注入) ----
cfg.set_autoclick_enabled(False)
cfg.set_autoclick_hotkey("mouse4")
autoclicker._user32.SendInput = fake_si2
try:
    ac_mod._on_side_button(autoclicker.XBUTTON1)  # 物理侧键4按下
    expect("侧键: mouse4 触发 toggle 开", cfg.is_autoclick_enabled() is True)

    # hold 模式下关闭时释放注入的 down
    autoclicker._injected_down = True
    calls2.clear()
    ac_mod._on_side_button(autoclicker.XBUTTON1)  # 再按 → 关闭
    expect("侧键: 关闭时释放注入 down", autoclicker._injected_down is False)
    expect("侧键: 释放调用了 LEFT UP",
           autoclicker.MOUSEEVENTF_LEFTUP in calls2)

    # 未配置的侧键不触发
    cfg.set_autoclick_hotkey("mouse5")
    cfg.set_autoclick_enabled(False)
    ac_mod._on_side_button(autoclicker.XBUTTON1)  # mouse4 按下, 但配置是 mouse5
    expect("侧键: 未配置的侧键不触发", cfg.is_autoclick_enabled() is False)

    # 键盘热键走 toggle_from_hotkey (Pause 键开关同步的同一模式)
    cfg.set_autoclick_hotkey("f9")
    ac_mod.toggle_from_hotkey()
    expect("热键: f9 触发 toggle 开", cfg.is_autoclick_enabled() is True)
    ac_mod.toggle_from_hotkey()
    expect("热键: f9 触发 toggle 关", cfg.is_autoclick_enabled() is False)

    # toggle 后触发 _config_change_cb (GUI 同步通知)
    cb_calls = []
    ac_mod._config_change_cb = lambda: cb_calls.append(1)
    ac_mod.toggle_from_hotkey()
    expect("热键: toggle 触发 config_change_cb", len(cb_calls) == 1)
    ac_mod._config_change_cb = None
finally:
    autoclicker._user32.SendInput = orig_si
    autoclicker._injected_down = False
    cfg.set_autoclick_hotkey("")

# ---- 注入失败检测 / 告警 (SendInput 返回 0 = 被拦截) ----
def fake_si_fail(n, ptr, size):
    return 0  # 模拟 UIPI 拦截 (游戏管理员 + 本进程非管理员)

autoclicker._failure_count = 0
autoclicker._failure_reported = False
cb_log = []
autoclicker._failure_cb = lambda blocked: cb_log.append(blocked)
autoclicker._user32.SendInput = fake_si_fail
try:
    for _ in range(19):
        autoclicker._note_injection_result(False)
    expect("失败: 19 次未达阈值不告警", cb_log == [])
    autoclicker._note_injection_result(False)
    expect("失败: 第 20 次触发告警", cb_log == [True])
    autoclicker._note_injection_result(False)
    expect("失败: 连续失败只告警一次", cb_log == [True])
    autoclicker._note_injection_result(True)
    expect("失败: 恢复后回调清除告警", cb_log == [True, False])
    autoclicker._note_injection_result(False)
    autoclicker._note_injection_result(False)
    expect("失败: 恢复后重新累计", cb_log == [True, False])
    # _inject_click 失败短路 (down 失败不再发 up)
    expect("失败: _inject_click 返回 False", autoclicker._inject_click(0.0) is False)
finally:
    autoclicker._user32.SendInput = orig_si
    autoclicker._failure_count = 0
    autoclicker._failure_reported = False
    autoclicker._failure_cb = None

print()
print("全部通过" if ok else "存在失败项!")
sys.exit(0 if ok else 1)
