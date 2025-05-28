from fastapi import FastAPI, HTTPException, status, Depends, Response
from fastapi.security import OAuth2PasswordBearer
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError
from typing import List, Dict, Optional
import requests
import logging
from models import User, Expense, Group, GroupExpense, Payment
from database import users_collection, expenses_collection, groups_collection, payments_collection, db
from auth import get_password_hash, verify_password, create_access_token, decode_token
from datetime import datetime
from bson import ObjectId
from pymongo.errors import PyMongoError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

class ExpenseResponse(BaseModel):
    id: str
    amount: float
    description: str
    paid_by: str
    splits: Dict[str, float]
    balances_for_current_user: Dict[str, float]
    group_id: Optional[str] = None
    group_name: Optional[str] = None

class BatchExpenseResponse(BaseModel):
    inserted: List[str]
    details: List[ExpenseResponse]

class UserExpense(BaseModel):
    id: str
    description: str
    amount: float
    paid_by: str
    participants: List[str]
    splits: Dict[str, float]
    created_at: datetime
    group_id: Optional[str] = None
    group_name: Optional[str] = None

class UserExpensesResponse(BaseModel):
    expenses: List[UserExpense]
    net_balances: Dict[str, float]
    group_balances: Dict[str, float]

class GroupResponse(BaseModel):
    id: str
    name: str
    members: List[str]
    created_by: str
    created_at: datetime

class DebugInfo(BaseModel):
    database_name: str
    users_collection_name: str
    expenses_collection_name: str
    groups_collection_name: str
    payments_collection_name: str
    users_count: int
    expenses_count: int
    groups_count: int
    payments_count: int

class SignupResponse(BaseModel):
    token: str
    previous_expenses: UserExpensesResponse

class PdfRequest(BaseModel):
    email: str
    group_id: Optional[str] = None

class PaymentResponse(BaseModel):
    id: str
    amount: float
    payer: str
    payee: str
    description: str
    created_at: datetime
    group_id: Optional[str] = None
    group_name: Optional[str] = None

async def get_current_user(token: str = Depends(oauth2_scheme)):
    email = decode_token(token)
    user = users_collection.find_one({"email": email})
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")
    return user

def calculate_split(expense: Expense):
    amount = expense.amount
    paid_by = expense.paid_by
    participants = expense.participants

    if expense.split_method == "equal":
        share = round(amount / len(participants), 2)
        splits = {p: share for p in participants}
    elif expense.split_method == "custom":
        if not expense.split_amounts or not all(p in expense.split_amounts for p in participants):
            logger.warning(f"Invalid or missing split_amounts for custom split in expense '{expense.description}'. Using equal split.")
            share = round(amount / len(participants), 2)
            splits = {p: share for p in participants}
        else:
            splits = expense.split_amounts
            if round(sum(splits.values()), 2) != round(amount, 2):
                logger.warning(f"Split amounts do not sum to total for expense '{expense.description}'. Using equal split.")
                share = round(amount / len(participants), 2)
                splits = {p: share for p in participants}
    else:
        logger.error(f"Unknown split_method '{expense.split_method}' for expense '{expense.description}'. Using equal split.")
        share = round(amount / len(participants), 2)
        splits = {p: share for p in participants}
    return splits

def calculate_user_balance(splits: dict, paid_by: str, current_user_email: str):
    balances = {}
    if current_user_email == paid_by:
        for p, amt in splits.items():
            if p != current_user_email:
                balances[p] = amt
    elif current_user_email in splits:
        balances[paid_by] = -splits[current_user_email]
    return balances

async def validate_users(participants: List[str], paid_by: str):
    unregistered = []
    for email in set(participants + [paid_by]):
        if not users_collection.find_one({"email": email}):
            unregistered.append(email)
            logger.info(f"Sending registration request to {email}")
    return unregistered

async def validate_group_members(group_id: str, participants: List[str], paid_by: str):
    try:
        group = groups_collection.find_one({"_id": ObjectId(group_id)})
        if not group:
            raise HTTPException(status_code=400, detail="Group not found")
        group_members = set(group["members"])
        all_participants = set(participants + [paid_by])
        if not all_participants.issubset(group_members):
            raise HTTPException(status_code=400, detail="All participants and paid_by must be group members")
        return group
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid group_id")

async def get_user_expenses_by_email(email: str):
    try:
        expenses = expenses_collection.find({"participants": email})
        payments = payments_collection.find({"$or": [{"payer": email}, {"payee": email}]})
        expense_list = []
        net_balances = {}
        group_balances = {}
        # Process expenses
        for expense in expenses:
            logger.debug(f"Processing expense {str(expense['_id'])}: {expense}")
            try:
                created_at = expense["created_at"]
                if isinstance(created_at, str):
                    try:
                        created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    except ValueError:
                        logger.error(f"Invalid created_at format for expense {str(expense['_id'])}")
                        continue
                expense_model = Expense(
                    description=expense["description"],
                    amount=expense["amount"],
                    participants=expense.get("participants", []),
                    paid_by=expense["paid_by"],
                    split_method=expense.get("split_method", "equal"),
                    split_amounts=expense.get("splits"),
                    created_at=created_at,
                    group_id=str(expense.get("group_id")) if expense.get("group_id") else None
                )
                splits = calculate_split(expense_model)
                user_balances = calculate_user_balance(splits, expense["paid_by"], email)
                group_name = None
                if expense_model.group_id:
                    try:
                        group = groups_collection.find_one({"_id": ObjectId(expense_model.group_id)})
                        group_name = group["name"] if group else "Unknown Group"
                        group_key = f"{group_name} ({expense_model.group_id})"
                        for user, amount in user_balances.items():
                            if user != email:
                                group_balances[group_key] = group_balances.get(group_key, 0) + (
                                    amount if expense["paid_by"] == email else -amount
                                )
                    except ValueError:
                        logger.error(f"Invalid group_id {expense_model.group_id} for expense {str(expense['_id'])}")
                        group_name = "Invalid Group"
                for user, amount in user_balances.items():
                    if user != email:
                        net_balances[user] = net_balances.get(user, 0) + (
                            amount if expense["paid_by"] == email else -amount
                        )
                expense_list.append({
                    "id": str(expense["_id"]),
                    "description": expense["description"],
                    "amount": expense["amount"],
                    "paid_by": expense["paid_by"],
                    "participants": expense.get("participants", []),
                    "splits": splits,
                    "created_at": created_at,
                    "group_id": expense_model.group_id,
                    "group_name": group_name or "Single"
                })
            except ValidationError as e:
                logger.error(f"Validation error for expense {str(expense['_id'])}: {str(e)}")
                continue
        # Process payments
        for payment in payments:
            try:
                created_at = payment["created_at"]
                if isinstance(created_at, str):
                    try:
                        created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    except ValueError:
                        logger.error(f"Invalid created_at format for payment {str(payment['_id'])}")
                        continue
                amount = payment["amount"]
                payer = payment["payer"]
                payee = payment["payee"]
                group_id = str(payment.get("group_id")) if payment.get("group_id") else None
                group_name = None
                if group_id:
                    try:
                        group = groups_collection.find_one({"_id": ObjectId(group_id)})
                        group_name = group["name"] if group else "Unknown Group"
                        group_key = f"{group_name} ({group_id})"
                        if payer == email:
                            group_balances[group_key] = group_balances.get(group_key, 0) - amount
                        elif payee == email:
                            group_balances[group_key] = group_balances.get(group_key, 0) + amount
                    except ValueError:
                        logger.error(f"Invalid group_id {group_id} for payment {str(payment['_id'])}")
                        group_name = "Invalid Group"
                if payer == email:
                    net_balances[payee] = net_balances.get(payee, 0) - amount
                elif payee == email:
                    net_balances[payer] = net_balances.get(payer, 0) + amount
            except ValidationError as e:
                logger.error(f"Validation error for payment {str(payment['_id'])}: {str(e)}")
                continue
        net_balances = {user: round(amount, 2) for user, amount in net_balances.items()}
        group_balances = {group: round(amount, 2) for group, amount in group_balances.items()}
        return {"expenses": expense_list, "net_balances": net_balances, "group_balances": group_balances}
    except PyMongoError as e:
        logger.error(f"Database error fetching user expenses: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/signup", response_model=SignupResponse)
async def signup(user: User):
    if users_collection.find_one({"email": user.email}):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    hashed_password = get_password_hash(user.password)
    try:
        result = users_collection.insert_one({
            "email": user.email,
            "name": user.name,
            "hashed_password": hashed_password
        })
        logger.info(f"User signed up: {user.email}, ID: {result.inserted_id}")
    except PyMongoError as e:
        logger.error(f"Database insertion failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database insertion failed: {str(e)}")
    token = create_access_token(data={"sub": user.email})
    previous_expenses = await get_user_expenses_by_email(user.email)
    return {"token": token, "previous_expenses": previous_expenses}

@app.post("/login")
async def login(user: User):
    db_user = users_collection.find_one({"email": user.email})
    if not db_user or not verify_password(user.password, db_user["hashed_password"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token(data={"sub": user.email})
    return {"token": token}

@app.post("/group", response_model=GroupResponse)
async def create_group(group: Group, current_user: dict = Depends(get_current_user)):
    if groups_collection.find_one({"name": group.name, "created_by": current_user["email"]}):
        raise HTTPException(status_code=400, detail="Group name already exists for this user")
    unregistered = await validate_users(group.members, group.created_by)
    group_dict = group.dict()
    group_dict["unregistered_members"] = unregistered
    try:
        result = groups_collection.insert_one(group_dict)
        logger.info(f"Created group: {group.name}, ID: {result.inserted_id}")
        return {
            "id": str(result.inserted_id),
            "name": group.name,
            "members": group.members,
            "created_by": group.created_by,
            "created_at": group.created_at
        }
    except PyMongoError as e:
        logger.error(f"Database insertion failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database insertion failed: {str(e)}")

@app.get("/groups", response_model=List[GroupResponse])
async def get_groups(current_user: dict = Depends(get_current_user)):
    try:
        groups = groups_collection.find({"$or": [{"created_by": current_user["email"]}, {"members": current_user["email"]}]})
        return [
            {
                "id": str(group["_id"]),
                "name": group["name"],
                "members": group["members"],
                "created_by": group["created_by"],
                "created_at": group["created_at"]
            }
            for group in groups
        ]
    except PyMongoError as e:
        logger.error(f"Error fetching groups: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching groups: {str(e)}")

@app.post("/expense", response_model=ExpenseResponse)
async def add_expense(expense: Expense, current_user: dict = Depends(get_current_user)):
    try:
        if current_user["email"] not in expense.participants:
            raise HTTPException(status_code=403, detail="Current user must be a participant")
        if expense.paid_by not in expense.participants:
            raise HTTPException(status_code=400, detail="paid_by must be one of participants")
        if expense.split_method == "custom":
            if not expense.split_amounts or set(expense.split_amounts.keys()) != set(expense.participants):
                raise HTTPException(status_code=400, detail="split_amounts must include all participants for custom split")
            if round(sum(expense.split_amounts.values()), 2) != round(expense.amount, 2):
                raise HTTPException(status_code=400, detail="split_amounts must sum to total amount")
        unregistered = await validate_users(expense.participants, expense.paid_by)
        group = None
        if expense.group_id:
            try:
                ObjectId(expense.group_id)
                group = await validate_group_members(expense.group_id, expense.participants, expense.paid_by)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid group_id")
        splits = calculate_split(expense)
        expense_dict = expense.dict(by_alias=True)
        expense_dict["created_at"] = expense.created_at or datetime.utcnow()
        expense_dict["unregistered_participants"] = unregistered
        expense_dict["splits"] = splits
        if expense.group_id:
            expense_dict["group_id"] = str(expense.group_id)
        try:
            result = expenses_collection.insert_one(expense_dict)
            logger.info(f"Inserted expense with ID {result.inserted_id} for user {current_user['email']}: {expense_dict}")
            inserted_expense = expenses_collection.find_one({"_id": result.inserted_id})
            if not inserted_expense:
                logger.error(f"Expense with ID {result.inserted_id} was not found after insertion")
                raise HTTPException(status_code=500, detail="Failed to verify expense insertion")
        except PyMongoError as e:
            logger.error(f"Database insertion failed: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Database insertion failed: {str(e)}")
        user_balances = calculate_user_balance(splits, expense.paid_by, current_user["email"])
        response = {
            "id": str(result.inserted_id),
            "amount": expense.amount,
            "description": expense.description,
            "paid_by": expense.paid_by,
            "splits": splits,
            "balances_for_current_user": user_balances,
            "group_id": expense.group_id
        }
        if expense.group_id and group:
            response["group_name"] = group["name"]
        return response
    except ValidationError as e:
        logger.error(f"Validation error for expense: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Invalid expense data: {str(e)}")
    except PyMongoError as e:
        logger.error(f"Database error adding expense: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/group-expense", response_model=BatchExpenseResponse)
async def add_group_expense(group_expense: GroupExpense, current_user: dict = Depends(get_current_user)):
    try:
        group = await validate_group_members(group_expense.group_id, [], current_user["email"])
        inserted_ids = []
        results = []
        for expense in group_expense.expenses:
            if current_user["email"] not in expense.participants:
                raise HTTPException(status_code=403, detail="Current user must be a participant in all expenses")
            if expense.paid_by not in expense.participants:
                raise HTTPException(status_code=400, detail="paid_by must be one of participants")
            if expense.split_method == "custom":
                if not expense.split_amounts or set(expense.split_amounts.keys()) != set(expense.participants):
                    raise HTTPException(status_code=400, detail="split_amounts must include all participants for custom split")
                if round(sum(expense.split_amounts.values()), 2) != round(expense.amount, 2):
                    raise HTTPException(status_code=400, detail="split_amounts must sum to total amount")
            unregistered = await validate_users(expense.participants, expense.paid_by)
            await validate_group_members(group_expense.group_id, expense.participants, expense.paid_by)
            splits = calculate_split(expense)
            expense_dict = expense.dict(by_alias=True)
            expense_dict["created_at"] = expense.created_at or datetime.utcnow()
            expense_dict["unregistered_participants"] = unregistered
            expense_dict["group_id"] = str(group_expense.group_id)
            expense_dict["splits"] = splits
            try:
                result = expenses_collection.insert_one(expense_dict)
                logger.info(f"Inserted group expense with ID {result.inserted_id} for user {current_user['email']}")
                inserted_expense = expenses_collection.find_one({"_id": result.inserted_id})
                if not inserted_expense:
                    logger.error(f"Group expense with ID {result.inserted_id} was not found after insertion")
                    raise HTTPException(status_code=500, detail="Failed to verify group expense insertion")
            except PyMongoError as e:
                logger.error(f"Database insertion failed: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Database insertion failed: {str(e)}")
            user_balances = calculate_user_balance(splits, expense.paid_by, current_user["email"])
            inserted_ids.append(str(result.inserted_id))
            results.append({
                "id": str(result.inserted_id),
                "amount": expense.amount,
                "description": expense.description,
                "paid_by": expense.paid_by,
                "splits": splits,
                "balances_for_current_user": user_balances,
                "group_id": str(group_expense.group_id),
                "group_name": group["name"]
            })
        return {"inserted": inserted_ids, "details": results}
    except ValidationError as e:
        logger.error(f"Validation error for group expense: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Invalid group expense data: {str(e)}")
    except PyMongoError as e:
        logger.error(f"Database error adding group expense: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/payment", response_model=PaymentResponse)
async def add_payment(payment: Payment, current_user: dict = Depends(get_current_user)):
    try:
        if current_user["email"] not in [payment.payer, payment.payee]:
            raise HTTPException(status_code=403, detail="Current user must be either payer or payee")
        unregistered = await validate_users([payment.payer, payment.payee], payment.payer)
        group = None
        if payment.group_id:
            try:
                ObjectId(payment.group_id)
                group = await validate_group_members(payment.group_id, [payment.payer, payment.payee], payment.payer)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid group_id")
        payment_dict = payment.dict(by_alias=True)
        payment_dict["created_at"] = payment.created_at or datetime.utcnow()
        payment_dict["unregistered"] = unregistered
        try:
            result = payments_collection.insert_one(payment_dict)
            logger.info(f"Inserted payment with ID {result.inserted_id} for user {current_user['email']}: {payment_dict}")
            inserted_payment = payments_collection.find_one({"_id": result.inserted_id})
            if not inserted_payment:
                logger.error(f"Payment with ID {result.inserted_id} was not found after insertion")
                raise HTTPException(status_code=500, detail="Failed to verify payment insertion")
        except PyMongoError as e:
            logger.error(f"Database insertion failed: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Database insertion failed: {str(e)}")
        response = {
            "id": str(result.inserted_id),
            "amount": payment.amount,
            "payer": payment.payer,
            "payee": payment.payee,
            "description": payment.description,
            "created_at": payment.created_at or datetime.utcnow(),
            "group_id": payment.group_id
        }
        if payment.group_id and group:
            response["group_name"] = group["name"]
        return response
    except ValidationError as e:
        logger.error(f"Validation error for payment: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Invalid payment data: {str(e)}")
    except PyMongoError as e:
        logger.error(f"Database error adding payment: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/reminder/{expense_id}/{to_email}")
async def send_reminder(expense_id: str, to_email: str, current_user: dict = Depends(get_current_user)):
    try:
        expense = expenses_collection.find_one({"_id": ObjectId(expense_id)})
        if not expense or current_user["email"] not in expense["participants"]:
            raise HTTPException(status_code=404, detail="Expense not found or not authorized")
        if to_email not in expense["participants"]:
            raise HTTPException(status_code=400, detail="to_email must be a participant in the expense")
        logger.info(f"Reminder sent to {to_email} for expense {expense_id}")
        return {"message": "Reminder sent"}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid expense_id")
    except PyMongoError as e:
        logger.error(f"Database error sending reminder: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/user/expenses", response_model=UserExpensesResponse)
async def get_user_expenses(current_user: dict = Depends(get_current_user)):
    return await get_user_expenses_by_email(current_user["email"])

@app.get("/user/created-expenses", response_model=UserExpensesResponse)
async def get_user_created_expenses(current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Fetching expenses for user: {current_user['email']}")
        expenses = expenses_collection.find({"created_by": current_user["email"]})
        expenses_list = list(expenses)
        logger.info(f"Found {len(expenses_list)} expenses for user {current_user['email']}")
        expense_list = []
        net_balances = {}
        group_balances = {}
        for expense in expenses_list:
            logger.debug(f"Processing expense {str(expense['_id'])}: {expense}")
            try:
                created_at = expense["created_at"]
                if isinstance(created_at, str):
                    try:
                        created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    except ValueError:
                        logger.error(f"Invalid created_at format for expense {str(expense['_id'])}")
                        continue
                expense_model = Expense(
                    description=expense["description"],
                    amount=expense["amount"],
                    participants=expense.get("participants", []),
                    paid_by=expense["paid_by"],
                    split_method=expense.get("split_method", "equal"),
                    split_amounts=expense.get("splits"),
                    created_at=created_at,
                    group_id=str(expense.get("group_id")) if expense.get("group_id") else None
                )
                splits = calculate_split(expense_model)
                user_balances = calculate_user_balance(splits, expense["paid_by"], current_user["email"])
                group_name = None
                if expense_model.group_id:
                    try:
                        group = groups_collection.find_one({"_id": ObjectId(expense_model.group_id)})
                        group_name = group["name"] if group else "Unknown Group"
                        group_key = f"{group_name} ({expense_model.group_id})"
                        for user, amount in user_balances.items():
                            if user != current_user["email"]:
                                group_balances[group_key] = group_balances.get(group_key, 0) + (
                                    amount if expense["paid_by"] == current_user["email"] else -amount
                                )
                    except ValueError:
                        logger.error(f"Invalid group_id {expense_model.group_id} for expense {str(expense['_id'])}")
                        group_name = "Invalid Group"
                for user, amount in user_balances.items():
                    if user != current_user["email"]:
                        net_balances[user] = net_balances.get(user, 0) + (
                            amount if expense["paid_by"] == current_user["email"] else -amount
                        )
                expense_list.append({
                    "id": str(expense["_id"]),
                    "description": expense["description"],
                    "amount": expense["amount"],
                    "paid_by": expense["paid_by"],
                    "participants": expense.get("participants", []),
                    "splits": splits,
                    "created_at": created_at,
                    "group_id": expense_model.group_id,
                    "group_name": group_name or "Single"
                })
            except ValidationError as e:
                logger.error(f"Validation error for expense {str(expense['_id'])}: {str(e)}")
                continue
        net_balances = {user: round(amount, 2) for user, amount in net_balances.items()}
        group_balances = {group: round(amount, 2) for group, amount in group_balances.items()}
        return {"expenses": expense_list, "net_balances": net_balances, "group_balances": group_balances}
    except PyMongoError as e:
        logger.error(f"Database error fetching created expenses: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/debug", response_model=DebugInfo)
async def get_debug_info():
    try:
        users_count = users_collection.count_documents({})
        expenses_count = expenses_collection.count_documents({})
        groups_count = groups_collection.count_documents({})
        payments_count = payments_collection.count_documents({})
        return {
            "database_name": db.name,
            "users_collection_name": users_collection.name,
            "expenses_collection_name": expenses_collection.name,
            "groups_collection_name": groups_collection.name,
            "payments_collection_name": payments_collection.name,
            "users_count": users_count,
            "expenses_count": expenses_count,
            "groups_count": groups_count,
            "payments_count": payments_count
        }
    except PyMongoError as e:
        logger.error(f"Database error fetching debug info: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/test-db")
async def test_db():
    try:
        from database import client
        client.admin.command('ping')
        return {"message": "MongoDB connection successful"}
    except PyMongoError as e:
        logger.error(f"Database error testing DB connection: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/test-expense")
async def add_test_expense(expense: dict, current_user: dict = Depends(get_current_user)):
    try:
        expense["created_by"] = current_user["email"]
        participants = expense.get("participants", [])
        amount = expense.get("amount", 0)
        if not participants or amount <= 0:
            raise HTTPException(status_code=400, detail="Invalid test expense data")
        if expense.get("split_method") == "custom":
            if not expense.get("splits") or set(expense["splits"].keys()) != set(participants):
                share = round(amount / len(participants), 2)
                expense["splits"] = {p: share for p in participants}
                expense["split_method"] = "equal"
                logger.warning(f"Invalid splits for custom test expense. Using equal split.")
        else:
            share = round(amount / len(participants), 2)
            expense["splits"] = {p: share for p in participants}
        result = expenses_collection.insert_one(expense)
        logger.info(f"Inserted test expense with ID {result.inserted_id}")
        return {"id": str(result.inserted_id)}
    except PyMongoError as e:
        logger.error(f"Database error adding test expense: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/clear-db")
async def clear_database(current_user: dict = Depends(get_current_user)):
    try:
        users_collection.drop()
        expenses_collection.drop()
        groups_collection.drop()
        payments_collection.drop()
        logger.info("Database cleared successfully")
        return {"message": "Database cleared"}
    except PyMongoError as e:
        logger.error(f"Database error clearing database: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/fix-expenses")
async def fix_expenses(current_user: dict = Depends(get_current_user)):
    try:
        fixed_count = 0
        expenses = expenses_collection.find({"split_method": "custom"})
        for expense in expenses:
            expense_id = str(expense["_id"])
            participants = expense.get("participants", [])
            amount = expense.get("amount", 0)
            splits = expense.get("splits")
            if not splits or set(splits.keys()) != set(participants) or round(sum(splits.values()), 2) != round(amount, 2):
                share = round(amount / len(participants), 2)
                new_splits = {p: share for p in participants}
                expenses_collection.update_one(
                    {"_id": ObjectId(expense_id)},
                    {"$set": {"splits": new_splits, "split_method": "equal"}}
                )
                logger.info(f"Fixed expense {expense_id}: Set equal splits and updated split_method")
                fixed_count += 1
        return {"message": f"Fixed {fixed_count} expenses"}
    except PyMongoError as e:
        logger.error(f"Database error fixing expenses: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/raw-data")
async def get_raw_data():
    try:
        users = list(users_collection.find())
        expenses = list(expenses_collection.find())
        groups = list(groups_collection.find())
        payments = list(payments_collection.find())
        for user in users:
            user["_id"] = str(user["_id"])
        for expense in expenses:
            expense["_id"] = str(expense["_id"])
        for group in groups:
            group["_id"] = str(group["_id"])
        for payment in payments:
            payment["_id"] = str(payment["_id"])
        return {"users": users, "expenses": expenses, "groups": groups, "payments": payments}
    except PyMongoError as e:
        logger.error(f"Database error fetching raw data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/generate-pdf")
async def generate_pdf(request: PdfRequest, current_user: dict = Depends(get_current_user)):
    if request.email != current_user["email"]:
        raise HTTPException(status_code=403, detail="Unauthorized to generate PDF for another user")
    try:
        scala_service_url = "http://localhost:8080/generate-pdf"
        response = requests.post(scala_service_url, json=request.dict())
        response.raise_for_status()
        return Response(content=response.content, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=expenses_{request.email}.pdf"})
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling Scala PDF service: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to generate PDF: {str(e)}")