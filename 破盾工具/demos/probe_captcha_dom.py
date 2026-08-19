"""
探测登录页滑块相关 DOM，便于校准选择器。

用法:
  python demos/probe_captcha_dom.py --wait-only
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from browser import create_driver  # noqa: E402
from verify_flow import load_config  # noqa: E402


PROBE_JS = r"""
const keywords = ['gt_', 'geetest', 'captcha', 'slider', 'slide', 'verify', 'yidun', 'nc-'];
const nodes = Array.from(document.querySelectorAll('*'));
const hit = [];
for (const el of nodes) {
  const cls = (el.className && typeof el.className === 'string') ? el.className : '';
  const id = el.id || '';
  const tag = el.tagName;
  const text = (el.innerText || '').slice(0, 40).replace(/\s+/g, ' ');
  const blob = (cls + ' ' + id).toLowerCase();
  if (!keywords.some(k => blob.includes(k)) && !text.includes('按住左边滑块')) continue;
  const rect = el.getBoundingClientRect();
  if (rect.width < 5 || rect.height < 5) continue;
  hit.push({
    tag,
    id,
    className: cls.slice(0, 120),
    text,
    w: Math.round(rect.width),
    h: Math.round(rect.height),
  });
  if (hit.length >= 80) break;
}
const iframes = Array.from(document.querySelectorAll('iframe')).map(f => ({
  src: f.src,
  id: f.id,
  className: typeof f.className === 'string' ? f.className : '',
}));
return {hit, iframes, title: document.title, url: location.href};
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config" / "selectors.yaml"))
    parser.add_argument("--wait-only", action="store_true", default=True)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    cfg = load_config(args.config)
    driver = create_driver(cfg.get("browser", {}))
    out = ROOT / "logs" / "dom_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        driver.get(cfg["target_url"])
        logging.info("请手动操作至滑块出现，脚本会每 2 秒采样 DOM…")
        deadline = time.time() + args.timeout
        best = None
        while time.time() < deadline:
            data = driver.execute_script(PROBE_JS)
            if data and (data.get("hit") or data.get("iframes")):
                best = data
                logging.info(
                    "采样: hits=%s iframes=%s",
                    len(data.get("hit") or []),
                    len(data.get("iframes") or []),
                )
                # 若出现滑块提示文案则落盘并结束
                if any("按住左边滑块" in (x.get("text") or "") for x in data.get("hit") or []):
                    break
            time.sleep(2)

        if best is None:
            logging.error("未采到相关 DOM")
            return 1
        out.write_text(json.dumps(best, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.info("已写入 %s", out)
        print(json.dumps(best, ensure_ascii=False, indent=2)[:4000])
        return 0
    finally:
        time.sleep(1)
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
