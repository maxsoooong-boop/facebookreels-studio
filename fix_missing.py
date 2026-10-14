# -*- coding: utf-8 -*-
"""补齐 61587595222147 缺失条目：125 条已完成 + 补跑剩余 → 128 条完整存档"""
import sys, os, json, re, time
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import app as A

PAGE = "61587595222147"
job = A._load_job("5b3750b2178a")
done = [v for v in (job.get("videos") or []) if v and isinstance(v, dict)]
done_keys = set()
for v in done:
    m = re.search(r"/(?:reel|videos|watch)/(\d+)", v.get("url") or "")
    if m:
        done_keys.add(("reel" if "reel" in v.get("url", "") else "videos") + "/" + m.group(1))
cache = [l.strip() for l in open(os.path.join(BASE, "pages", PAGE + "_all_urls.txt"), encoding="utf-8") if l.strip() and "/" in l]
missing = []
for u in cache:
    m = re.search(r"/(?:reel|videos|watch)/(\d+)", u)
    k = ("reel" if "reel" in u else "videos") + "/" + m.group(1) if m else u
    if k not in done_keys:
        missing.append(u)
print(f"已完成 {len(done)} 条，缺失 {len(missing)} 条", flush=True)

source = {"mode": "urls", "videos": missing, "skip_transcribe": False, "skip_download": False,
          "session": "smv7c02vlakozxhih"}
results = list(done)
no = len(done)
for u in missing:
    no += 1
    print(f"补 #{no}: {u[:80]}", flush=True)
    try:
        rec, cmts = A._process_one_video(no, len(done) + len(missing), u, source, "small", True)
        if rec:
            results.append(rec)
    except Exception as e:
        print(f"  # {no} 失败: {e}", flush=True)
    time.sleep(0.3)

# 去重（按 url）+ 按 no 排序
seen = {}
for r in results:
    if r and isinstance(r, dict):
        seen.setdefault(r.get("url"), r)
ordered = sorted(seen.values(), key=lambda r: r.get("no", 0))
print(f"最终条数: {len(ordered)} | 成功: {sum(1 for r in ordered if r.get('status')=='成功')}", flush=True)

# 汇总 + 存档
comments_all = []
for r in ordered:
    c = r.get("comments")
    if isinstance(c, list):
        comments_all.extend(c)
summary = A._build_summary(ordered, comments_all)
result = {"videos": ordered, "comments": comments_all, "summary": summary,
          "total": len(ordered), "model": "small", "comments_enabled": True,
          "page": PAGE, "generated_at": A.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
A._save_page_result(PAGE, result, "smv7c02vlakozxhih")
print("已存档:", json.dumps({"条数": len(ordered), "成功": summary["success"], "观看": summary["views_sum"],
                              "评论": summary["comments_sum"], "点赞": summary["likes_sum"]}, ensure_ascii=False), flush=True)
