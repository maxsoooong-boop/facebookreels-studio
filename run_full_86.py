# -*- coding: utf-8 -*-
"""鹭岛闽南（61553024038256）86 条全量任务：口播文案 + 高清视频保存。
独立进程运行（不依赖 Flask 主服务），复用 app._run_batch_job（3 并发线程池 + 逐条异常捕获）。
完成自动存档到 data/<session>_61553024038256_result.json。"""
import sys, os, json, uuid
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import app as A

urls = json.load(open("/tmp/luyu_urls.json", encoding="utf-8"))
print("待处理:", len(urls), "条", flush=True)

source = {"mode": "urls", "videos": urls, "skip_transcribe": False, "skip_download": False,
          "session": "smv7c02vlakozxhih"}
job_id = uuid.uuid4().hex[:12]
job = {"id": job_id, "type": "batch", "status": "queued", "message": "已加入队列",
       "percent": 0, "result": None, "error": None, "model": "small",
       "log": [], "videos": [], "total": None, "current": None, "session": source["session"],
       "created": A.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
A._save_job(job)
print("job_id:", job_id, flush=True)

try:
    A._run_batch_job(job_id, source, "small", False)
    print("任务完成", flush=True)
except Exception as e:
    print("任务异常:", str(e)[:200], flush=True)
