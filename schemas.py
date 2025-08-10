from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Dict, Any

class SignupIn(BaseModel):
    name: Optional[str] = "User"
    email: EmailStr
    password: str = Field(min_length=6)

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class AssessmentIn(BaseModel):
    scores: Dict[str, Any]
    signals: Optional[Dict[str, Any]] = {}

class EvaluateIn(BaseModel):
    assessmentId: str

class PlanIn(BaseModel):
    evaluationId: str
