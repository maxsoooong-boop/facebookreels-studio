# -*- coding: utf-8 -*-
"""Facebook 主页深度抓取 worker：无头浏览器打开主页视频 tab，滚动加载全部视频，
提取全部视频 URL 写入 pages/<page>_all_urls.txt，并把进度写入 jobs/<scan_id>.json。
用法: .venv/bin/python deep_scan_worker.py <page> <cookies_file> <scan_id>
"""
import json
import os
import re
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(BASE_DIR, "jobs")
PAGES_DIR = os.path.join(BASE_DIR, "pages")
os.makedirs(JOBS_DIR, exist_ok=True)
os.makedirs(PAGES_DIR, exist_ok=True)


def set_job(scan_id, **kw):
    path = os.path.join(JOBS_DIR, scan_id + ".json")
    try:
        d = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        d.update(kw)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


def netscape_to_playwright(cookies_file):
    """把 cookies.txt（Netscape 格式）转成 Playwright add_cookies 需要的列表。"""
    out = []
    with open(cookies_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, ok, path, secure, expires, name, value = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]
            c = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path or "/",
                "secure": secure == "TRUE",
            }
            try:
                e = int(float(expires))
                if e > 0:
                    c["expires"] = e
            except Exception:
                pass
            out.append(c)
    return out


def main():
    page_id = sys.argv[1]
    cookies_file = sys.argv[2]
    scan_id = sys.argv[3]
    set_job(scan_id, status="running", page=page_id, message="正在启动无头浏览器…", percent=2)

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                viewport={"width": 1440, "height": 900},
                locale="zh-CN",
            )
            try:
                ctx.add_cookies(netscape_to_playwright(cookies_file))
            except Exception as e:
                set_job(scan_id, status="error", message=f"cookies 解析失败: {e}", percent=100)
                return
            page = ctx.new_page()
            url = f"https://www.facebook.com/profile.php?id={page_id}&sk=videos"
            set_job(scan_id, message="正在打开主页视频页…", percent=5)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
            # 若被重定向到登录页，直接报错
            if "login" in page.url or page.url.startswith("https://www.facebook.com/checkpoint"):
                set_job(scan_id, status="error", message="被 Facebook 要求登录或验证（cookies 失效），请重新登录后重试", percent=100)
                browser.close()
                return

            seen = set()
            stable = 0
            total_rounds = 200  # 上限200轮（约10分钟）；连续4轮无新增则提前停止，适配任意大小主页
            for i in range(total_rounds):
                # 滚动到底部触发懒加载
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1800)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight - 900)")
                page.wait_for_timeout(1200)
                urls = page.evaluate("""() => {
                    const out = [];
                    document.querySelectorAll('a[href]').forEach(a => {
                        const h = a.href;
                        if (/\\/reel\\/|\\/videos\\/|watch\\/\\?v=|video_id=|\\/watch\\?/.test(h)) out.push(h);
                    });
                    return out;
                }""")
                before = len(seen)
                for u in urls or []:
                    for pat in (r"/(?:reel|videos|watch)/(\d+)", r"[?&]v=(\d+)", r"video_id=(\d+)"):
                        m = re.search(pat, u)
                        if m:
                            if "reel" in u:
                                seen.add("reel/" + m.group(1))
                            elif "videos" in u or "watch" in u:
                                seen.add("videos/" + m.group(1))
                            else:
                                seen.add("videos/" + m.group(1))
                            break
                set_job(scan_id, message=f"滚动加载中… 已发现 {len(seen)} 条视频", percent=min(5 + int(i / total_rounds * 70), 75), count=len(seen))
                if len(seen) == before:
                    stable += 1
                    if stable >= 4:
                        break
                else:
                    stable = 0
            browser.close()

        # 写 URL 缓存（与后端 pages/<page>_all_urls.txt 同格式）
        lines = []
        for t in sorted(seen):
            typ, vid = t.split("/")
            if typ == "reel":
                lines.append(f"https://www.facebook.com/reel/{vid}")
            else:
                lines.append(f"https://www.facebook.com/videos/{vid}/")
        out_path = os.path.join(PAGES_DIR, page_id + "_all_urls.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        set_job(scan_id, status="done", message=f"深度抓取完成，共 {len(seen)} 条视频", count=len(seen),
                urls_file=out_path, percent=100)
    except Exception as e:
        set_job(scan_id, status="error", message=f"深度抓取失败: {str(e)[:200]}", percent=100)
        try:
            import traceback
            traceback.print_exc()
        except Exception:
            pass


if __name__ == "__main__":
    main()
