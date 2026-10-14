# -*- coding: utf-8 -*-
"""用 Playwright（登录态 cookies）批量抓取主页 Reels 的评论内容。
读取 pages/<pid>_all_urls.txt，逐条打开→点开评论→滚动加载→提取评论（作者+文本）。
进度每 5 条保存一次到 data/comments_progress_<pid>.json，完成后合并进存档并重生成 Excel。"""
import sys, os, json, re, time
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

PAGE = sys.argv[1] if len(sys.argv) > 1 else "61553024038256"
SESSION = sys.argv[2] if len(sys.argv) > 2 else "smv7c02vlakozxhih"
MAX_PER_VIDEO = int(sys.argv[3]) if len(sys.argv) > 3 else 40
SCROLL_ROUNDS = 6
PROG = os.path.join(BASE, "data", f"comments_progress_{PAGE}.json")

from playwright.sync_api import sync_playwright

def load_cookies_netscape(path):
    """Netscape cookies 文件 → Playwright cookie 列表"""
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, inc, cpath, secure, exp, name, value = parts[:7]
        cookie = {"name": name, "value": value, "domain": domain, "path": cpath}
        if secure == "TRUE":
            cookie["secure"] = True
        if exp and exp != "0":
            try:
                cookie["expires"] = int(exp)
            except ValueError:
                pass
        if inc == "TRUE":
            cookie["sameSite"] = "Lax"
        out.append(cookie)
    return out

def extract_comments(page):
    """提取评论（作者+文本）"""
    return page.evaluate("""() => {
      const out = [];
      const seen = new Set();
      // FB 评论容器：每评论通常为 role=article 或含 comment 的 div
      const items = document.querySelectorAll('[role="article"]');
      items.forEach(el => {
        const author = (el.querySelector('a[href*="/user/"], a[href*="profile"]')?.innerText || '').trim();
        const body = el.querySelector('[data-testid="comment-body"], [dir="auto"]');
        const text = (body?.innerText || el.innerText || '').trim();
        if (text && text.length > 1 && !seen.has(text)) {
          seen.add(text);
          out.push({author: author.slice(0, 60), text: text.slice(0, 500)});
        }
      });
      // 备选：aria-label 含 comment 的区块
      if (out.length < 3) {
        document.querySelectorAll('[aria-label*="comment" i], [aria-label*="留言"]').forEach(el => {
          const text = (el.innerText || '').trim();
          if (text && text.length > 3 && !seen.has(text)) {
            seen.add(text);
            out.push({author: '', text: text.slice(0, 500)});
          }
        });
      }
      return out.slice(0, %d);
    }""" % MAX_PER_VIDEO)

def process_one(page, url):
    for attempt in range(2):
        try:
            page.goto(url, timeout=30000, wait_until="load")
            # 等待页面主体渲染完整（body 文本足够长）
            for _ in range(10):
                page.wait_for_timeout(1000)
                blen = page.evaluate("document.body.innerText.length")
                if blen > 300:
                    break
            clicked = page.evaluate("""() => {
              const els = [...document.querySelectorAll('[aria-label], div[role="button"]')];
              const btn = els.find(e => /comment|评论|留言/i.test(e.getAttribute('aria-label') || ''));
              if (btn) { btn.click(); return true; }
              return false;
            }""")
            page.wait_for_timeout(2500)
            for _ in range(SCROLL_ROUNDS):
                page.mouse.wheel(0, 2200)
                page.wait_for_timeout(500)
            cmts = extract_comments(page)
            if cmts or attempt == 1:
                return cmts
        except Exception as e:
            if attempt == 1:
                return [{"author": "", "text": f"__ERROR__{str(e)[:100]}"}]
    return []

def main():
    cache = os.path.join(BASE, "pages", f"{PAGE}_all_urls.txt")
    urls = [l.strip() for l in open(cache, encoding="utf-8") if l.strip().startswith("http")]
    print(f"共 {len(urls)} 条 URL", flush=True)
    prog = {}
    if os.path.exists(PROG):
        prog = json.load(open(PROG, encoding="utf-8"))
    cookies = load_cookies_netscape(os.path.join(BASE, "cookies", f"{SESSION}.txt"))
    print(f"cookies {len(cookies)} 个", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                                  locale="zh-CN", user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36")
        ctx.add_cookies(cookies)
        page = ctx.new_page()
        done = 0
        for i, url in enumerate(urls, 1):
            vid = re.search(r"/(?:reel|videos|watch)/(\d+)", url)
            key = vid.group(1) if vid else str(i)
            if key in prog and prog[key].get("n", 0) >= 3 and "__ERROR__" not in json.dumps(prog[key].get("comments", [])):
                done += 1
                continue
            cmts = process_one(page, url)
            real = [c for c in cmts if not c["text"].startswith("__ERROR__")]
            n = len(real)
            prog[key] = {"url": url, "n": n, "comments": real}
            done += 1
            print(f"[{done}/{len(urls)}] #{key} 评论 {n} 条", flush=True)
            time.sleep(1.5 + (i % 4) * 0.5)  # 随机延迟，降低风控
            if done % 5 == 0:
                json.dump(prog, open(PROG, "w", encoding="utf-8"), ensure_ascii=False)
                print("  进度已保存", flush=True)
        browser.close()
    json.dump(prog, open(PROG, "w", encoding="utf-8"), ensure_ascii=False)
    total = sum(v["n"] for v in prog.values())
    print(f"完成：{len(prog)} 条视频，评论内容共 {total} 条", flush=True)

if __name__ == "__main__":
    main()
