# -*- coding: utf-8 -*-
"""断点续跑脚本：为 job 336be03f744a 补跑缺失的 68-81 条（口播文案+高清保存）。
逐条 fork 子进程隔离：单条视频转写/下载崩溃（段错误）不影响后续，超时自动跳过。
不依赖 Flask 主服务，nohup 独立运行，服务重启不影响。"""
import sys, os, json, time
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import app as A

JOB_ID = "336be03f744a"
OUT_PATH = os.path.join(BASE, "jobs", "resume_" + JOB_ID + ".json")

lines = [l.strip() for l in open(os.path.join(BASE, "pages", "61550268035919_all_urls.txt"), encoding="utf-8")
         if l.strip() and "/" in l]
urls = lines[67:81]  # 68-81
print("待补跑:", len(urls), "条", flush=True)

def run_one(no, u):
    ctx = A.multiprocessing.get_context("fork")
    q = ctx.Queue()
    def _w(u, q):
        try:
            rec, _cmts = A._process_one_video(no, 81, u, {"session": "public", "skip_transcribe": False, "skip_download": False},
                                              "small", False)
            rec["_ok"] = True
            q.put(rec)
        except Exception as e:
            q.put({"no": no, "url": u, "status": "error:" + str(e)[:120], "_ok": False})
    p = ctx.Process(target=_w, args=(u, q))
    p.start()
    p.join(timeout=900)  # 单条最长 15 分钟
    if p.is_alive():
        p.terminate()
        return {"no": no, "url": u, "status": "timeout", "_ok": False}
    if q.empty():
        return {"no": no, "url": u, "status": "noresult", "_ok": False}
    return q.get()

results = []
for i, u in enumerate(urls, start=68):
    t0 = time.time()
    rec = run_one(i, u)
    rec["no"] = i
    results.append(rec)
    ok = rec.get("_ok") and rec.get("status") == "成功"
    print(f"#{i} {time.strftime('%H:%M:%S')} 用时{int(time.time()-t0)}s status={rec.get('status')} "
          f"文案={len(rec.get('transcript') or '')}字 file={rec.get('file')}", flush=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"done": i, "results": results}, f, ensure_ascii=False)

print("全部完成，共", len(results), "条", flush=True)
