# 滑块验证工具使用说明

目标站点：[上海一网通办登录页](https://zwdtuser.sh.gov.cn/uc/login/login.jsp?kind=0&self=self&redirect_uri=https%3A//zwdt.sh.gov.cn/govPortals)

本期范围：**仅滑块拼图验证**（识别缺口 + 拟人拖拽）。不包含完整登录业务。

## 环境

- Python 3.9+
- Google Chrome **或** Microsoft Edge（本仓库默认配置为 Edge）

```bash
cd "d:\工作\破盾工具"
pip install -r requirements.txt
```

## 站点说明

该登录页滑块为自研插件 [`imgVer`](https://zwdtuser.sh.gov.cn/uc/plugin/imgVer/img_ver.js?v=20251224.001)，不是极验：

| 元素 | 选择器 |
|---|---|
| 背景图 | `#scream` |
| 拼图块 | `#puzzleShadow` |
| 拖动手把 | `.slider-btn` |
| 刷新 | `.re-btn a` |
| 容器 | `#Sliding` / `#SlidingValidation` |

点击 `#login-btn` 会调用 `showSlidingValidation()` 弹出验证框。

## 快速演示

手动弹出滑块后自动求解（推荐首次使用）：

```bash
python demos/run_slider_demo.py --wait-only
```

用账号密码触发验证码再求解：

```bash
python demos/run_slider_demo.py --username 你的手机号 --password 你的密码
```

## 在自己的脚本里调用

```python
from pathlib import Path
import sys
sys.path.insert(0, str(Path("src").resolve()))

from browser import create_driver
from verify_flow import load_config, solve_slider

cfg = load_config("config/selectors.yaml")
driver = create_driver(cfg["browser"])
driver.get(cfg["target_url"])
# ... 自行操作到滑块弹出 ...
ok = solve_slider(driver, cfg, log_dir="logs")
```

## 校准

1. 运行后查看 `logs/` 中的 `*_bg.png` / `*_piece.png`
2. 若总是偏左/偏右，改 `config/selectors.yaml` 里 `slider.bias`（常见 ±2～8）
3. 若找不到元素，用 DevTools 确认 class，更新 `captcha.*_selectors`

## 目录

- `src/` 核心模块
- `config/selectors.yaml` 选择器与参数
- `demos/run_slider_demo.py` 演示入口
- `logs/` 失败截图与识别样本
- `技术方案.md` 设计说明
