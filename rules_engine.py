import yaml
from pathlib import Path

def _clamp(v):
    try:
        return max(0, min(100, round(float(v))))
    except:
        return 0

def load_rules(path: str | Path = "skill_eval_rules.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def level_of(score: int, thresholds: dict, resolver: dict) -> str:
    # نستخدم level_resolver إذا موجود
    default = resolver.get("default", {})
    bands = [
        ("advanced",     default.get("advanced",     {"gte": 85})),
        ("intermediate", default.get("intermediate", {"gte": 60, "lt": 85})),
        ("beginner",     default.get("beginner",     {"lt": 60})),
    ]
    for name, cond in bands:
        gte = cond.get("gte", None)
        lt  = cond.get("lt",  None)
        if (gte is None or score >= gte) and (lt is None or score < lt):
            return name
    # fallback
    if score >= 85: return "advanced"
    if score >= 60: return "intermediate"
    return "beginner"

def weighted_overall(scores: dict, weights: dict) -> int:
    num, den = 0.0, 0.0
    for k, v in scores.items():
        w = float(weights.get(k, 0.0))
        num += w * _clamp(v)
        den += w
    return _clamp(num / den) if den > 0 else 0

def _passes(score: int, cond: dict) -> bool:
    ok = True
    if "lt"  in cond: ok &= score <  cond["lt"]
    if "lte" in cond: ok &= score <= cond["lte"]
    if "gt"  in cond: ok &= score >  cond["gt"]
    if "gte" in cond: ok &= score >= cond["gte"]
    return ok

def _eval_rec_condition(scores: dict, rec_if: dict) -> bool:
    # يدعم {domain: X, ...} أو any/all
    if "domain" in rec_if:
        d = rec_if["domain"]
        s = _clamp(scores.get(d, 0))
        return _passes(s, rec_if)
    if "any" in rec_if:
        return any(_eval_rec_condition(scores, sub) for sub in rec_if["any"])
    if "all" in rec_if:
        return all(_eval_rec_condition(scores, sub) for sub in rec_if["all"])
    return False

def _signals_get(sig: dict, dotted: str):
    cur = sig
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur

def apply_signals_boosts(resources: list, signals: dict, rules: dict) -> list:
    # boosts بسيطة حسب signals_rules
    sr = rules.get("signals_rules", {})

    def boost_types(lst, types):
        score_map = {}
        tset = [x.lower() for x in (types or [])]
        for i, r in enumerate(lst):
            base = 0
            t = (r.get("type") or r.get("resType") or "").lower()
            if tset and t in tset:
                base += 3
            score_map[i] = base
        return score_map

    agg = {i: 0 for i in range(len(resources))}

    if "prefers_video" in sr:
        sc = boost_types(resources, sr["prefers_video"].get("boost_types"))
        for i, v in sc.items():
            agg[i] += v

    if "likes_hands_on" in sr:
        sc = boost_types(resources, sr["likes_hands_on"].get("boost_types"))
        for i, v in sc.items():
            agg[i] += v

    if "time_pressure_high" in sr:
        maxh = sr["time_pressure_high"].get("prefer_est_under_hours", 10)
        for i, r in enumerate(resources):
            est = (r.get("est") or "")
            nums = []
            for tok in est.replace("–", "-").split():
                tok = tok.strip().lower()
                if tok.endswith("h"):
                    n = tok[:-1]
                    try:
                        nums.append(float(n))
                    except:
                        pass
            mint = min(nums) if nums else None
            if mint is not None and mint <= maxh:
                agg[i] += 2

    order = sorted(range(len(resources)), key=lambda i: agg[i], reverse=True)
    return [resources[i] for i in order]

def build_advice(scores: dict, rules: dict) -> list[str]:
    out = []
    for rec in rules.get("recommendations", []):
        cond = rec.get("if", {})
        if _eval_rec_condition(scores, cond):
            out.append(rec.get("then"))
    return out

def pick_resources_for_domain(domain: str, level: str, rules: dict, needed: int, signals: dict) -> list:
    dom_res = rules.get("resources", {}).get("domains", {}).get(domain, {})
    candidates = list(dom_res.get(level, []))
    if not candidates:
        return []
    candidates = apply_signals_boosts(candidates, signals or {}, rules)
    return candidates[:needed]

def distribute_by_weeks(items: list, weeks: int, weekly_cap: tuple[int, int]) -> list:
    # round-robin مع احترام الحد الأعلى
    min_cap, max_cap = weekly_cap
    buckets = {w: [] for w in range(1, weeks + 1)}
    w = 1
    for it in items:
        tried = 0
        while tried < weeks and len(buckets[w]) >= max_cap:
            w = 1 if w >= weeks else w + 1
            tried += 1
        buckets[w].append(it)
        w = 1 if w >= weeks else w + 1

    out = []
    for wk in range(1, weeks + 1):
        for it in buckets[wk]:
            it = dict(it)
            it["week"] = wk
            out.append(it)
    return out

def build_plan(scores: dict, signals: dict, rules: dict) -> dict:
    # 1) تطبيع الدرجات + overall
    scores = {k: _clamp(v) for k, v in (scores or {}).items()}
    weights = rules.get("weights", {})
    overall = weighted_overall(scores, weights)

    # 2) تحويل الدرجات إلى مستويات
    resolver = rules.get("plan_builder", {}).get("level_resolver", {})
    levels = {d: level_of(scores.get(d, 0), rules.get("thresholds", {}), resolver) for d in scores.keys()}

    # 3) معطيات البناء
    pb = rules.get("plan_builder", {})
    weeks = int(pb.get("weeks", 4))
    pick_counts = pb.get("pick_counts", {"beginner": 3, "intermediate": 2, "advanced": 2})
    domain_priority = pb.get("domain_priority", list(scores.keys()))
    weekly_cap = pb.get("weekly_cap", {"min": 3, "max": 6})
    min_cap = int(weekly_cap.get("min", 3))
    max_cap = int(weekly_cap.get("max", 6))

    # 4) تجميع الموارد حسب الدومينات (من الأضعف للأقوى)
    raw_items = []
    for d in sorted(domain_priority, key=lambda x: scores.get(x, 0)):
        lvl = levels.get(d, "beginner")
        need = int(pick_counts.get(lvl, 2))
        picked = pick_resources_for_domain(d, lvl, rules, need, signals)
        for r in picked:
            raw_items.append({
                "type": "resource",
                "domain": d,
                "title": r.get("title"),
                "url": r.get("url"),
                "provider": r.get("provider"),
                "resType": r.get("type"),
                "est": r.get("est"),
            })

    # 5) موارد إضافية حسب درجات الكورسات داخل signals.course_grades
    course_cfg = pb.get("course_rules", {"threshold": 60, "pick_per_course": 2})
    course_threshold = int(course_cfg.get("threshold", 60))
    course_pick = int(course_cfg.get("pick_per_course", 2))
    courses = rules.get("resources", {}).get("courses", {})
    grades = (signals or {}).get("course_grades", {}) or {}
    for cname, grade in grades.items():
        g = _clamp(grade)
        if g < course_threshold and cname in courses:
            extra = courses[cname][:course_pick]
            for r in extra:
                raw_items.append({
                    "type": "resource",
                    "course": cname,
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "provider": r.get("provider"),
                    "resType": r.get("type"),
                    "est": r.get("est"),
                })

    # 6) عادات أسبوعية + روتينات soft-skills
    habits = pb.get("weekly_habits", []) or []
    softs = pb.get("soft_skills_routines", {}) or {}

    for h in habits:
        raw_items.append({
            "type": "action",
            "title": h.get("text"),
            "week": int(h.get("week", 1))
        })

    thresholds = rules.get("thresholds", {})
    def is_below_intermediate(domain):
        return scores.get(domain, 0) < thresholds.get(domain, {}).get("intermediate", 60)

    for soft_domain, arr in softs.items():
        if is_below_intermediate(soft_domain):
            for a in arr:
                raw_items.append({
                    "type": "action",
                    "domain": soft_domain,
                    "title": a.get("text"),
                    "week": int(a.get("week", 1))
                })

    # 7) توزيع الموارد على الأسابيع
    resources = [x for x in raw_items if x.get("type") == "resource"]
    actions   = [x for x in raw_items if x.get("type") == "action"]

    distributed = distribute_by_weeks(resources, weeks, (min_cap, max_cap))

    for act in actions:
        if not act.get("week"):
            act["week"] = 1
        distributed.append(act)

    # 8) نصائح من قسم recommendations
    advice = build_advice(scores, rules)

    # 9) ترتيب نهائي
    distributed.sort(key=lambda x: (int(x.get("week", 1)), x.get("type"), x.get("domain") or "", x.get("title") or ""))

    return {
        "overall": overall,
        "levels": levels,
        "items": distributed,
        "advice": advice
    }
