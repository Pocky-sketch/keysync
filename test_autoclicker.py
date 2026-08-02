"""AutoClicker 测试 — 判定逻辑 + 配置 + 真实钩子 smoke 测试。

在真实 Windows 环境跑: start() 装 WH_MOUSE_LL 钩子, 验证线程能正常
启停 (不会注入点击, 因为 autoclick 默认关闭且无人按住左键)。
"""
import os
import sys
import tempfile
import time

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
_inject_click()
expect("注入: _inject_click 不抛异常", True)

print()
print("全部通过" if ok else "存在失败项!")
sys.exit(0 if ok else 1)
