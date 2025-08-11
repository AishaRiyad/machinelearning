from sqlalchemy import Column, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from database import Base
import json

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    pass_hash: Mapped[str] = mapped_column(String, nullable=False)

class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    scores_json: Mapped[str] = mapped_column(Text, default="{}")
    signals_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    def set_scores(self, obj): self.scores_json = json.dumps(obj or {}, ensure_ascii=False)
    def get_scores(self):
        try: return json.loads(self.scores_json or "{}")
        except: return {}
    def set_signals(self, obj): self.signals_json = json.dumps(obj or {}, ensure_ascii=False)
    def get_signals(self):
        try: return json.loads(self.signals_json or "{}")
        except: return {}

class Evaluation(Base):
    __tablename__ = "evaluations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    assessment_id: Mapped[str] = mapped_column(String, nullable=False)
    domain_scores_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    def set_domain_scores(self, obj): self.domain_scores_json = json.dumps(obj or {}, ensure_ascii=False)
    def get_domain_scores(self):
        try: return json.loads(self.domain_scores_json or "{}")
        except: return {}

class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    evaluation_id: Mapped[str] = mapped_column(String, nullable=False)
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str] = mapped_column(String, nullable=True)
    # ⇦ جديد: نخزن نصائح القواعد
    advice_json: Mapped[str] = mapped_column(Text, default="[]")

    def set_items(self, arr): self.items_json = json.dumps(arr or [], ensure_ascii=False)
    def get_items(self):
        try: return json.loads(self.items_json or "[]")
        except: return []

    def set_advice(self, arr): self.advice_json = json.dumps(arr or [], ensure_ascii=False)
    def get_advice(self):
        try: return json.loads(self.advice_json or "[]")
        except: return []
