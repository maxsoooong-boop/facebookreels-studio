# -*- coding: utf-8 -*-
"""独立进程续跑 61587595222147 剩余条目：从 e1c5c289607b（已完成120条）继续。
复用 app._run_batch_job，独立 nohup 进程运行，完成自动存档到用户会话。"""
import sys, os, json, uuid, re
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import app as A

PAGE = "61587595222147"
job = A._load_job("e1c5c289607b")
done = {}
for v in (job.get("videos") or []):
    m = re.search(r"/(?:reel|videos|watch)/(\d+)", v.get("url") or "")
    if m and v.get("no") is not None:
        done[("reel" if "reel" in v.get("url", "") else "videos") + "/" + m.group(1)] = v
lines = [l.strip() for l in open(os.path.join(BASE, "pages", PAGE + "_all_urls.txt"), encoding="utf-8") if l.strip() and "/" in l]
remain = []
for u in lines:
    m = re.search(r"/(?:reel|videos|watch)/(\d+)", u)
    k = ("reel" if "reel" in u else "videos") + "/" + m.group(1) if m else u
    if k not in done:
        remain.append(u)
prefill = [{"no": v["no"], "rec": v, "comments": []} for v in done.values()]
print(f"已完成 {len(prefill)} 条，剩余 {len(remain)} 条", flush=True)
if not remain:
    print("全部完成，无需续跑", flush=True)
    sys.exit(0)

source = {"mode": "urls", "videos": remain, "page": PAGE,
          "skip_transcribe": False, "skip_download": False,
          "session": "smv7c02vlakozxhih", "prefill": prefill}
new_id = uuid.uuid4().hex[:12]
A.JOBS[new_id] = {"id": new_id, "type": "batch", "status": "queued",
                  "message": f"独立进程续跑：剩余 {len(remain)} 条", "percent": 5,
                  "result": None, "error": None, "model": "small",
                  "log": [f"{A.datetime.now().strftime('%H:%M:%S')} 独立进程续跑 {len(remain)} 条"],
                  "videos": list(done.values()), "total": len(prefill) + len(remain),
                  "current": None, "session": source["session"],
                  "created": A.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
A._save_job(A.JOBS[new_id])
print("job_id:", new_id, flush=True)
A._run_batch_job(new_id, source, "small", False)
print("任务完成", flush=True)
