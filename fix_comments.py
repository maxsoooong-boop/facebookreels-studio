# -*- coding: utf-8 -*-
"""补抓 61587595222147 全部 128 条的评论内容，写入存档（评论数已在）。"""
import sys, os, json
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import app as A

PAGE = "61587595222147"
p = f"{BASE}/data/smv7c02vlakozxhih_{PAGE}_result.json"
d = json.load(open(p, encoding="utf-8"))
videos = d.get("videos") or []
cf = os.path.join(BASE, "cookies", "smv7c02vlakozxhih.txt")
if not os.path.exists(cf):
    cf = None
all_cmts = []
done = 0
for i, v in enumerate(videos, 1):
    if isinstance(v.get("comments"), list) and v["comments"]:
        all_cmts.extend(v["comments"])
        done += 1
        continue
    try:
        cdata = A._fetch_comments_best_effort(v.get("url") or "", cookies_file=cf)
        cmts = cdata.get("comments") or []
        v["comments"] = cmts
        all_cmts.extend(cmts)
        if v.get("comments_count") is None and cdata.get("count") is not None:
            v["comments_count"] = cdata["count"]
        done += 1
        if i % 10 == 0:
            print(f"进度 {i}/{len(videos)}，评论内容累计 {len(all_cmts)}", flush=True)
        A._save_page_result(PAGE, d, "smv7c02vlakozxhih")  # 每10条存一次防中断
    except Exception as e:
        print(f"  #{i} 评论抓取失败: {e}", flush=True)

d["comments"] = all_cmts
d["summary"]["comment_texts"] = [c["text"] for c in all_cmts if isinstance(c, dict)]
A._save_page_result(PAGE, d, "smv7c02vlakozxhih")
print(f"完成：128 条，评论内容共 {len(all_cmts)} 条", flush=True)
