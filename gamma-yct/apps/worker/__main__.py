"""python -m apps.worker"""

from apps.worker import config


def main() -> None:
    print("gamma-yct worker ready")
    print(f"  AUTOMATION_MODE={config.AUTOMATION_MODE}")
    print(f"  ALLOW_FINAL_SUBMIT={config.ALLOW_FINAL_SUBMIT}")
    print(f"  YCT_PORTAL_URL={config.YCT_PORTAL_URL}")
    print(f"  SKIP_QR_LOGIN={config.SKIP_QR_LOGIN}")
    print(f"  PLAYWRIGHT_USE_CDP={config.PLAYWRIGHT_USE_CDP}")
    print(f"  DESKTOP_RPA_FALLBACK={config.DESKTOP_RPA_FALLBACK}")
    print(f"  CAPTURE_QR={config.CAPTURE_QR}")
    if config.AUTOMATION_MODE in ("playwright", "auto"):
        print("  path: portal_yct -> login -> name declaration DOM fill")
    elif config.AUTOMATION_MODE == "desktop_rpa":
        print("  path: desktop_rpa only")
    if config.ALLOW_FINAL_SUBMIT:
        print("  WARNING: ALLOW_FINAL_SUBMIT is true — 一期不应开启")


if __name__ == "__main__":
    main()
