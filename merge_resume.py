# -*- coding: utf-8 -*-
"""合并断点续跑结果：把原任务 336be03f744a 的 67 条 + 续跑 68-81 条合并成完整 81 条存档。
输出：jobs/336be03f744a 更新（result 完整）+ data/61550268035919_result.json 完整版。"""
import sys, os, json, time
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

JOB_ID = "336be03f744a"
job = json.load(open(os.path.join(BASE, "jobs", JOB_ID + ".json"), encoding="utf-8"))
resume = json.load(open(os.path.join(BASE, "jobs", "resume_" + JOB_ID + ".json"), encoding="utf-8"))

old_vids = [v for v in (job.get("videos") or []) if v]
new_vids = [r for r in resume["results"] if r.get("_ok")]

print("旧条数:", len(old_vids), "新条数:", len(new_vids))

# 用续跑结果覆盖同 no 的旧记录，其余保留
by_no = {v.get("no"): v for v in old_vids}
for r in new_vids:
    if r.get("no") is not None:
        by_no[r["no"]] = r
all_vids = [by_no[n] for n in range(1, 82) if n in by_no]

# 汇总统计
ok = [v for v in all_vids if v.get("status") == "成功"]
with_tr = [v for v in all_vids if v.get("transcript")]
with_file = [v for v in all_vids if v.get("file")]
total_views = sum(v.get("views") or 0 for v in all_vids)
total_comments = sum(v.get("comments_count") or 0 for v in all_vids)
total_likes = sum(v.get("likes") or 0 for v in all_vids)
max_view = max((v.get("views") or 0) for v in all_vids)
max_com = max((v.get("comments_count") or 0) for v in all_vids)
top_view = next((v for v in all_vids if (v.get("views") or 0) == max_view), None)
top_com = next((v for v in all_vids if (v.get("comments_count") or 0) == max_com), None)

summary = {
    "success": len(ok), "total": len(all_vids),
    "with_transcript": len(with_tr), "with_file": len(with_file),
    "total_views": total_views, "total_comments": total_comments, "total_likes": total_likes,
    "max_views": max_view, "max_comments": max_com,
    "top_view": {"no": top_view.get("no"), "views": top_view.get("views"),
                 "title": (top_view.get("caption") or top_view.get("title") or "")[:80]} if top_view else None,
    "top_comment": {"no": top_com.get("no"), "comments_count": top_com.get("comments_count"),
                    "title": (top_com.get("caption") or top_com.get("title") or "")[:80]} if top_com else None,
    "comment_texts": [],
}
result = {
    "page": "61550268035919",
    "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "model": job.get("model", "small"),
    "total": len(all_vids),
    "videos": all_vids,
    "comments": [],
    "comments_enabled": False,
    "summary": summary,
}

# 更新 job
job["status"] = "done"
job["message"] = f"已完成 {len(ok)}/{len(all_vids)} 条，口播文案 {len(with_tr)} 条，高清视频 {len(with_file)} 条"
job["percent"] = 100
job["result"] = result
job["videos"] = all_vids
with open(os.path.join(BASE, "jobs", JOB_ID + ".json"), "w", encoding="utf-8") as f:
    json.dump(job, f, ensure_ascii=False)

# 写存档（public + 用户会话两份）
for prefix in ("", "smv7c02vlakozxhih_"):
    fn = os.path.join(BASE, "data", f"{prefix}61550268035919_result.json")
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)

print("合并完成：", len(all_vids), "条 | 成功", len(ok), "| 文案", len(with_tr), "| 文件", len(with_file))
print("观看合计", total_views, "评论合计", total_comments, "点赞合计", total_likes)
