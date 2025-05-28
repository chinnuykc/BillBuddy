from pydantic import BaseModel, Field, EmailStr, field_validator
from typing import List, Optional, Dict, Literal
from datetime import datetime
from pydantic import BaseModel, field_validator, ConfigDict
from typing import Optional

class User(BaseModel):
    email: EmailStr
    password: str
    name: str

    @field_validator("password")
    def password_length(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        return v

class UserInDB(User):
    hashed_password: str

class Group(BaseModel):
    name: str
    members: List[EmailStr]
    created_by: EmailStr
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

    @field_validator("members")
    def members_not_empty(cls, v):
        if not v:
            raise ValueError("Members list cannot be empty")
        return v

class Expense(BaseModel):
    description: str
    amount: float
    participants: List[EmailStr]
    paid_by: EmailStr
    split_method: Literal["equal", "custom"] = "equal"
    split_amounts: Optional[Dict[EmailStr, float]] = Field(default=None, alias="splits")
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    group_id: Optional[str] = None

    @field_validator("amount")
    def amount_positive(cls, v):
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v

    @field_validator("participants")
    def participants_not_empty(cls, v):
        if not v:
            raise ValueError("Participants list cannot be empty")
        return v

class GroupExpense(BaseModel):
    group_id: str
    expenses: List[Expense]

    @field_validator("expenses")
    def expenses_not_empty(cls, v):
        if not v:
            raise ValueError("Expenses list cannot be empty")
        return v

class Payment(BaseModel):
    amount: float
    payer: str
    payee: str
    description: str
    created_at: str
    group_id: Optional[str] = None  # Changed from Optional[int] to Optional[str]

    @field_validator('payer', 'payee', mode='after')
    @classmethod
    def different_users(cls, v: str, info) -> str:
        other_field = 'payee' if info.field_name == 'payer' else 'payer'
        other_value = info.data.get(other_field)
        if other_value is not None and v == other_value:
            raise ValueError('Payer and payee must be different')
        return v

    model_config = ConfigDict(validate_by_name=True)