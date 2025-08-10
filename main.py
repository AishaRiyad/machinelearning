from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import EmailStr
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import jwt
from passlib.context import CryptContext
from uuid import uuid4
from sqlalchemy.orm import Session
from sqlalchemy import text  # للترقية التلقائية

from database import Base, engine, get_db
from models import User, Assessment, Evaluation, Plan
from schemas import SignupIn, LoginIn, AssessmentIn, EvaluateIn, PlanIn

# rules engine
from rules_engine import load_rules, build_plan, _clamp

JWT_SECRET = "dev-secret-change-me"
JWT_ALG = "HS256"
TOKEN_EXPIRE_DAYS = 7
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="Skill Quest Backend", version="1.4.0")

# 🔐 CORS: اسمحي فقط لأصل الواجهة الأمامية
ALLOWED_ORIGINS = [
    "http://127.0.0.1:5500",
    "http://localhost:5500",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _auto_migrate():
    """
    يضمن وجود عمود advice_json في جدول plans (يُضاف تلقائيًا إذا ناقص).
    """
    with engine.begin() as conn:
        # إنشاء الجداول إن لم تكن موجودة
        Base.metadata.create_all(bind=engine)

        # فحص أعمدة plans
        rows = conn.execute(text("PRAGMA table_info(plans)")).fetchall()
        cols = {row[1] for row in rows}

        # إضافة advice_json إذا غير موجود
        if "advice_json" not in cols:
            conn.execute(text("ALTER TABLE plans ADD COLUMN advice_json TEXT DEFAULT '[]'"))
            print("✅ DB migration: added plans.advice_json")
        else:
            print("ℹ️ DB migration: plans.advice_json already exists")

@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    _auto_migrate()

def make_token(user_id: str, email: EmailStr) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": str(email),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=TOKEN_EXPIRE_DAYS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def require_auth(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization")
    token = authorization.split(" ", 1)[1]
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return data
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

def clamp_score(v):
    try:
        v = float(v)
    except Exception:
        v = 0.0
    return max(0, min(100, round(v)))

# -------- Auth --------
@app.post("/api/auth/signup", status_code=201)
def signup(body: SignupIn, db: Session = Depends(get_db)):
    email = body.email.lower().strip()
    exists = db.query(User).filter(User.email == email).first()
    if exists:
        raise HTTPException(status_code=409, detail="Email already registered")
    u = User(
        id=str(uuid4()),
        name=(body.name or "User"),
        email=email,
        pass_hash=pwd_ctx.hash(body.password),
    )
    db.add(u)
    db.commit()
    return {"userId": u.id, "email": u.email, "name": u.name}

@app.post("/api/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    email = body.email.lower().strip()
    u = db.query(User).filter(User.email == email).first()
    if not u or not pwd_ctx.verify(body.password, u.pass_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = make_token(u.id, u.email)
    return {"token": token}

@app.get("/api/auth/me")
def me(auth=Depends(require_auth), db: Session = Depends(get_db)):
    u = db.query(User).filter(User.id == auth["sub"]).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return {"id": u.id, "email": u.email, "name": u.name}

# -------- Me latest --------
@app.get("/api/me/latest")
def me_latest(auth=Depends(require_auth), db: Session = Depends(get_db)):
    e = (
        db.query(Evaluation)
        .filter(Evaluation.user_id == auth["sub"])
        .order_by(Evaluation.created_at.desc())
        .first()
    )
    p = (
        db.query(Plan)
        .filter(Plan.user_id == auth["sub"])
        .order_by(Plan.created_at.desc())
        .first()
    )
    out = {}
    if e:
        out["evaluation"] = {
            "id": e.id,
            "assessmentId": e.assessment_id,
            "domainScores": e.get_domain_scores(),
            "createdAt": e.created_at,
        }
    if p:
        out["plan"] = {
            "id": p.id,
            "evaluationId": p.evaluation_id,
            "items": p.get_items(),
            "createdAt": p.created_at,
            "startedAt": p.started_at,
            "advice": p.get_advice(),
        }
    return out

# -------- Assessments --------
@app.post("/api/assessments/", status_code=201)
def create_assessment(body: AssessmentIn, auth=Depends(require_auth), db: Session = Depends(get_db)):
    aid = str(uuid4())
    a = Assessment(
        id=aid,
        user_id=auth["sub"],
        created_at=datetime.utcnow().isoformat() + "Z",
    )
    a.set_scores(body.scores or {})
    a.set_signals(body.signals or {})
    db.add(a)
    db.commit()
    return {"assessmentId": aid}

@app.get("/api/assessments/{aid}")
def get_assessment(aid: str, auth=Depends(require_auth), db: Session = Depends(get_db)):
    a = db.query(Assessment).filter(Assessment.id == aid, Assessment.user_id == auth["sub"]).first()
    if not a:
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "id": a.id,
        "userId": a.user_id,
        "scores": a.get_scores(),
        "signals": a.get_signals(),
        "createdAt": a.created_at,
    }

# -------- Evaluation (rule-based) --------
@app.post("/api/evaluate/", status_code=201)
def evaluate(body: EvaluateIn, auth=Depends(require_auth), db: Session = Depends(get_db)):
    a = db.query(Assessment).filter(Assessment.id == body.assessmentId, Assessment.user_id == auth["sub"]).first()
    if not a:
        raise HTTPException(status_code=404, detail="Assessment not found")

    rules = load_rules()

    # درجات الدومينات + overall من الأوزان في ملف القواعد
    raw_scores = a.get_scores() or {}
    domain_scores = {k: clamp_score(v) for k, v in raw_scores.items()}

    weights = rules.get("weights", {})
    num, den = 0.0, 0.0
    for k, v in domain_scores.items():
        w = float(weights.get(k, 0.0))
        num += w * _clamp(v)
        den += w
    overall = _clamp(num / den) if den > 0 else 0
    domain_scores["overall"] = overall  # نخزّنه داخل JSON

    eid = str(uuid4())
    e = Evaluation(
        id=eid,
        user_id=auth["sub"],
        assessment_id=a.id,
        created_at=datetime.utcnow().isoformat() + "Z",
    )
    e.set_domain_scores(domain_scores)
    db.add(e)
    db.commit()
    return {"evaluationId": eid, "domainScores": domain_scores}

@app.get("/api/evaluate/{eid}")
def get_evaluation(eid: str, auth=Depends(require_auth), db: Session = Depends(get_db)):
    e = db.query(Evaluation).filter(Evaluation.id == eid, Evaluation.user_id == auth["sub"]).first()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "id": e.id,
        "userId": e.user_id,
        "assessmentId": e.assessment_id,
        "domainScores": e.get_domain_scores(),
        "createdAt": e.created_at,
    }

@app.get("/api/evaluate/latest")
def get_latest_evaluation(auth=Depends(require_auth), db: Session = Depends(get_db)):
    e = (
        db.query(Evaluation)
        .filter(Evaluation.user_id == auth["sub"])
        .order_by(Evaluation.created_at.desc())
        .first()
    )
    if not e:
        raise HTTPException(status_code=404, detail="No evaluations found")
    return {
        "id": e.id,
        "userId": e.user_id,
        "assessmentId": e.assessment_id,
        "domainScores": e.get_domain_scores(),
        "createdAt": e.created_at,
    }

# -------- Plans (rule-based, with saved advice) --------
@app.post("/api/plans/", status_code=201)
def create_plan(body: PlanIn, auth=Depends(require_auth), db: Session = Depends(get_db)):
    e = db.query(Evaluation).filter(Evaluation.id == body.evaluationId, Evaluation.user_id == auth["sub"]).first()
    if not e:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    a = db.query(Assessment).filter(Assessment.id == e.assessment_id, Assessment.user_id == auth["sub"]).first()
    signals = a.get_signals() if a else {}
    ds = e.get_domain_scores() or {}

    rules = load_rules()
    plan_out = build_plan(ds, signals, rules)
    items = plan_out["items"]
    advice = plan_out["advice"]

    pid = str(uuid4())
    p = Plan(
        id=pid,
        user_id=auth["sub"],
        evaluation_id=e.id,
        created_at=datetime.utcnow().isoformat() + "Z",
        started_at=None,
    )
    p.set_items(items)
    p.set_advice(advice)
    db.add(p)
    db.commit()

    return {"planId": pid, "items": items, "advice": advice}

@app.get("/api/plans/{pid}")
def get_plan(pid: str, auth=Depends(require_auth), db: Session = Depends(get_db)):
    p = db.query(Plan).filter(Plan.id == pid, Plan.user_id == auth["sub"]).first()
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "id": p.id,
        "userId": p.user_id,
        "evaluationId": p.evaluation_id,
        "items": p.get_items(),
        "createdAt": p.created_at,
        "startedAt": p.started_at,
        "advice": p.get_advice(),
    }

@app.get("/api/plans/latest")
def get_latest_plan(auth=Depends(require_auth), db: Session = Depends(get_db)):
    p = (
        db.query(Plan)
        .filter(Plan.user_id == auth["sub"])
        .order_by(Plan.created_at.desc())
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="No plans found")
    return {
        "id": p.id,
        "userId": p.user_id,
        "evaluationId": p.evaluation_id,
        "items": p.get_items(),
        "createdAt": p.created_at,
        "startedAt": p.started_at,
        "advice": p.get_advice(),
    }

@app.post("/api/plans/{pid}/start")
def start_plan(pid: str, auth=Depends(require_auth), db: Session = Depends(get_db)):
    p = db.query(Plan).filter(Plan.id == pid, Plan.user_id == auth["sub"]).first()
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    p.started_at = datetime.utcnow().isoformat() + "Z"
    db.add(p)
    db.commit()
    return {"ok": True, "startedAt": p.started_at}

@app.get("/api/ping")
def ping():
    return "pong"
