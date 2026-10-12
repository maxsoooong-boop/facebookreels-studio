# -*- coding: utf-8 -*-
"""Facebook 登录 worker：弹出真实浏览器窗口让用户登录，登录完成后导出 cookies。
用法: python login_worker.py <cookies_file> <state_file>
state_file 写入 {"status": "pending|logged_in|error|closed", "message": "..."}"""
import json
import os
import sys
import time


def set_state(path, status, message):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"status": status, "message": message}, f, ensure_ascii=False)


def main():
    cookies_file = sys.argv[1] if len(sys.argv) > 1 else "cookies.txt"
    state_file = sys.argv[2] if len(sys.argv) > 2 else "pw_login_state.json"
    set_state(state_file, "pending", "正在打开浏览器登录窗口…")
    browser = None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
            set_state(state_file, "pending", "已在弹出窗口打开 Facebook 登录页，请完成登录（验证码/二次验证请照常完成）")
            deadline = time.time() + 600
            logged = False
            while time.time() < deadline:
                time.sleep(2.5)
                try:
                    url = page.url or ""
                    if "login" not in url and "facebook.com" in url:
                        page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
                        if "login" not in (page.url or ""):
                            logged = True
                            break
                except Exception:
                    pass
            if not logged:
                set_state(state_file, "error", "登录未完成或超时（10 分钟），请重新点击登录")
                try:
                    browser.close()
                except Exception:
                    pass
                return
            cookies = ctx.cookies()
            lines = ["# Netscape HTTP Cookie File", "# 由 Facebook 登录助手自动生成", ""]
            for c in cookies:
                domain = c.get("domain") or ""
                secure = "TRUE" if c.get("secure") else "FALSE"
                path = c.get("path") or "/"
                exp = c.get("expires")
                exp = int(exp) if exp and exp > 0 else 0
                lines.append(f"{domain}\tTRUE\t{path}\t{secure}\t{exp}\t{c['name']}\t{c['value']}")
            with open(cookies_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            set_state(state_file, "logged_in", "登录成功，cookies 已自动更新，可直接开始分析")
            try:
                browser.close()
            except Exception:
                pass
    except Exception as e:
        set_state(state_file, "error", f"登录失败：{str(e)[:120]}")
        try:
            if browser:
                browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
