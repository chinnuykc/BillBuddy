import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
import logging
import json
import re
import os
from io import StringIO

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

def is_valid_email(email):
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", email.strip()))

def login(email, password):
    if not is_valid_email(email) or not password:
        st.error("Please provide a valid email and password")
        return None
    try:
        response = requests.post(f"{BASE_URL}/login", json={"email": email, "password": password, "name": ""})
        response.raise_for_status()
        return response.json()["token"]
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        try:
            error_detail = response.json().get("detail", "Login failed")
        except (NameError, ValueError, AttributeError):
            pass
        st.error(error_detail)
        logger.error(f"Login error: {error_detail}")
        return None

def signup(email, password, name):
    if not is_valid_email(email) or not password or not name:
        st.error("Please provide a valid email, password, and name")
        return None, None
    try:
        response = requests.post(f"{BASE_URL}/signup", json={"email": email, "password": password, "name": name})
        response.raise_for_status()
        data = response.json()
        return data["token"], data["previous_expenses"]
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        if 'response' in locals():
            try:
                error_detail = response.json().get("detail", "Signup failed")
            except (ValueError, AttributeError):
                pass
        st.error(error_detail)
        logger.error(f"Signup error: {error_detail}")
        return None, None

def create_group(token, name, members):
    if not name or not members:
        st.error("Please provide group name and members")
        return False
    members_list = [m.strip() for m in members.split(",") if m.strip() and is_valid_email(m.strip())]
    if not members_list:
        st.error("Please provide at least one valid email for members")
        return False
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "name": name,
        "members": members_list,
        "created_by": st.session_state.email,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    logger.info(f"Creating group with payload: {json.dumps(payload, indent=2)}")
    try:
        response = requests.post(f"{BASE_URL}/group", json=payload, headers=headers)
        response.raise_for_status()
        st.success(f"Group '{name}' created successfully!")
        st.rerun()
        return True
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        try:
            error_detail = response.json().get("detail", "Error creating group")
        except (NameError, ValueError, AttributeError):
            pass
        logger.error(f"Error creating group: {error_detail}")
        st.error(f"Failed to create group: {error_detail}")
        return False

def get_groups(token):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(f"{BASE_URL}/groups", headers=headers)
        response.raise_for_status()
        groups = response.json()
        logger.info(f"Groups fetched: {json.dumps(groups, indent=2)}")
        return groups
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        try:
            error_detail = response.json().get("detail", "Error fetching groups")
        except (NameError, ValueError, AttributeError):
            pass
        logger.error(f"Error fetching groups: {error_detail}")
        st.error(f"Error fetching groups: {error_detail}")
        return []

def add_expense(token, description, amount, participants, paid_by, split_method, custom_splits=None, group_id=None):
    if not description or amount <= 0 or not participants or not paid_by:
        st.error("Please fill out all required fields with valid values")
        return False
    participants_list = [p.strip() for p in participants.split(",") if p.strip() and is_valid_email(p.strip())]
    if not participants_list:
        st.error("Please provide at least one valid participant email")
        return False
    if not is_valid_email(paid_by):
        st.error("Please provide a valid email for paid_by")
        return False
    if split_method.lower() == "custom":
        if not custom_splits:
            st.error("Custom splits are required for custom split method")
            return False
        if not all(is_valid_email(p) for p in custom_splits.keys()):
            st.error("All custom split emails must be valid")
            return False
        if set(custom_splits.keys()) != set(participants_list):
            st.error("Custom splits must include all participants")
            return False
        if abs(sum(custom_splits.values()) - amount) > 0.01:
            st.error(f"Sum of custom splits ({sum(custom_splits.values()):.2f}) must equal total amount ({amount:.2f})")
            return False
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "description": description,
        "amount": float(amount),
        "participants": participants_list,
        "paid_by": paid_by.strip(),
        "split_method": split_method.lower(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "group_id": group_id
    }
    if split_method.lower() == "custom" and custom_splits:
        payload["splits"] = custom_splits
    logger.info(f"Sending add expense request: {json.dumps(payload, indent=2)}")
    try:
        response = requests.post(f"{BASE_URL}/expense", json=payload, headers=headers)
        response.raise_for_status()
        logger.info(f"Add expense response: {response.json()}")
        st.success("Expense added successfully!")
        st.rerun()
        return True
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        try:
            error_detail = response.json().get("detail", "Error adding expense")
        except (NameError, ValueError, AttributeError):
            pass
        logger.error(f"Error adding expense: {error_detail}")
        st.error(f"Failed to add expense: {error_detail}")
        return False

def add_group_expense(token, group_id, expenses):
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "group_id": group_id,
        "expenses": []
    }
    for exp in expenses:
        if not exp["description"] or exp["amount"] <= 0 or not exp["participants"] or not exp["paid_by"]:
            continue
        participants_list = [p.strip() for p in exp["participants"].split(",") if p.strip() and is_valid_email(p.strip())]
        if not participants_list:
            continue
        if not is_valid_email(exp["paid_by"]):
            continue
        if exp["split_method"].lower() == "custom":
            custom_splits = exp.get("custom_splits")
            if not custom_splits:
                st.error(f"Custom splits required for expense: {exp['description']}")
                continue
            if not all(is_valid_email(p) for p in custom_splits.keys()):
                st.error(f"Invalid email in custom splits for expense: {exp['description']}")
                continue
            if set(custom_splits.keys()) != set(participants_list):
                st.error(f"Custom splits must include all participants for expense: {exp['description']}")
                continue
            if abs(sum(custom_splits.values()) - exp["amount"]) > 0.01:
                st.error(f"Sum of custom splits ({sum(custom_splits.values()):.2f}) must equal total amount ({exp['amount']:.2f}) for expense: {exp['description']}")
                continue
            expense = {
                "description": exp["description"],
                "amount": float(exp["amount"]),
                "participants": participants_list,
                "paid_by": exp["paid_by"].strip(),
                "split_method": exp["split_method"].lower(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "splits": custom_splits
            }
        else:
            expense = {
                "description": exp["description"],
                "amount": float(exp["amount"]),
                "participants": participants_list,
                "paid_by": exp["paid_by"].strip(),
                "split_method": exp["split_method"].lower(),
                "created_at": datetime.now(timezone.utc).isoformat()
            }
        payload["expenses"].append(expense)
    if not payload["expenses"]:
        st.error("No valid expenses provided")
        return False
    logger.info(f"Sending add group expenses request: {json.dumps(payload, indent=2)}")
    try:
        response = requests.post(f"{BASE_URL}/group-expense", json=payload, headers=headers)
        response.raise_for_status()
        st.success("Group expenses added successfully!")
        st.rerun()
        return True
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        try:
            error_detail = response.json().get("detail", "Error adding group expenses")
        except (NameError, ValueError, AttributeError):
            pass
        logger.error(f"Error adding group expenses: {error_detail}")
        st.error(f"Failed to add group expenses: {error_detail}")
        return False

def add_payment(token, amount, payer, payee, group_id=None, description="Debt repayment"):
    if not is_valid_email(payer) or not is_valid_email(payee):
        st.error("Please provide valid emails for payer and payee")
        return False
    if amount <= 0:
        st.error("Payment amount must be positive")
        return False
    if payer == payee:
        st.error("Payer and payee must be different")
        return False
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "amount": float(amount),
        "payer": payer.strip(),
        "payee": payee.strip(),
        "description": description,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "group_id": group_id
    }
    logger.info(f"Sending add payment request: {json.dumps(payload, indent=2)}")
    try:
        response = requests.post(f"{BASE_URL}/payment", json=payload, headers=headers)
        response.raise_for_status()
        st.success("Payment recorded successfully!")
        st.rerun()
        return True
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        try:
            error_detail = response.json().get("detail", "Error adding payment")
        except (NameError, ValueError, AttributeError):
            pass
        logger.error(f"Error adding payment: {error_detail}")
        st.error(f"Failed to add payment: {error_detail}")
        return False

def add_sample_expense(token, email):
    description = "Sample Dinner Expense"
    amount = 600.0
    participants = f"{email},friend@example.com"
    paid_by = email
    split_method = "equal"
    add_expense(token, description, amount, participants, paid_by, split_method)

def add_test_expense(token, email):
    headers = {"Authorization": f"Bearer {token}"}
    participants = [email, "test@example.com"]
    amount = 500.0
    share = round(amount / len(participants), 2)
    payload = {
        "description": "Test Expense (Direct Insert)",
        "amount": amount,
        "participants": participants,
        "paid_by": email,
        "split_method": "equal",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "unregistered_participants": ["test@example.com"],
        "splits": {p: share for p in participants}
    }
    logger.info(f"Sending test expense request: {json.dumps(payload, indent=2)}")
    try:
        response = requests.post(f"{BASE_URL}/test-expense", json=payload, headers=headers)
        response.raise_for_status()
        st.success("Test expense inserted via API!")
        st.rerun()
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        try:
            error_detail = response.json().get("detail", "Error adding test expense")
        except (NameError, ValueError, AttributeError):
            pass
        logger.error(f"Error adding test expense: {error_detail}")
        st.error(f"Failed to insert test expense: {error_detail}")

def get_user_expenses(token):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(f"{BASE_URL}/user/expenses", headers=headers)
        response.raise_for_status()
        data = response.json()
        logger.info(f"API response from /user/expenses: {json.dumps(data, indent=2)}")
        return data
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        try:
            error_detail = response.json().get("detail", "Error fetching expenses")
        except (NameError, ValueError, AttributeError):
            pass
        logger.error(f"Error fetching expenses: {error_detail}")
        st.error(f"Error fetching expenses: {error_detail}")
        return {"expenses": [], "net_balances": {}, "group_balances": {}}

def get_payments(token):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(f"{BASE_URL}/raw-data", headers=headers)
        response.raise_for_status()
        data = response.json()
        payments = data.get("payments", [])
        logger.info(f"Fetched {len(payments)} payments")
        return payments
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        try:
            error_detail = response.json().get("detail", "Error fetching payments")
        except (NameError, ValueError, AttributeError):
            pass
        logger.error(f"Error fetching payments: {error_detail}")
        st.error(f"Error fetching payments: {error_detail}")
        return []

def send_reminder(token, expense_id, to_email):
    if not is_valid_email(to_email):
        st.error("Please provide a valid email for reminder")
        return
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.post(f"{BASE_URL}/reminder/{expense_id}/{to_email}", headers=headers)
        response.raise_for_status()
        st.success("Reminder sent!")
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        try:
            error_detail = response.json().get("detail", "Error sending reminder")
        except (NameError, ValueError, AttributeError):
            pass
        logger.error(f"Error sending reminder: {error_detail}")
        st.error(error_detail)

def download_expenses_pdf(token, email, group_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"email": email, "group_id": group_id}
    try:
        response = requests.post(f"{BASE_URL}/generate-pdf", json=payload, headers=headers)
        response.raise_for_status()
        filename = f"expenses_{email}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
        st.download_button(
            label="Download PDF",
            data=response.content,
            file_name=filename,
            mime="application/pdf"
        )
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        try:
            error_detail = response.json().get("detail", "Error generating PDF")
        except (NameError, ValueError, AttributeError):
            pass
        logger.error(f"Error generating PDF: {error_detail}")
        st.error(f"Failed to generate PDF: {error_detail}")

def get_debug_info():
    try:
        response = requests.get(f"{BASE_URL}/debug")
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching debug info: {str(e)}")
        return {"error": str(e)}

def test_db_connection():
    try:
        response = requests.get(f"{BASE_URL}/test-db")
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error testing DB connection: {str(e)}")
        return {"error": str(e)}

def fix_expenses(token):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.post(f"{BASE_URL}/fix-expenses", headers=headers)
        response.raise_for_status()
        st.success(response.json()["message"])
        st.rerun()
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        try:
            error_detail = response.json().get("detail", "Error fixing expenses")
        except (NameError, ValueError, AttributeError):
            pass
        logger.error(f"Error fixing expenses: {error_detail}")
        st.error(f"Failed to fix expenses: {error_detail}")

def clear_database(token):
    if st.checkbox("Confirm: I understand this will delete all data"):
        headers = {"Authorization": f"Bearer {token}"}
        try:
            response = requests.post(f"{BASE_URL}/clear-db", headers=headers)
            response.raise_for_status()
            st.success("Database cleared!")
            st.rerun()
        except requests.exceptions.RequestException as e:
            error_detail = str(e)
            try:
                error_detail = response.json().get("detail", "Error clearing database")
            except (NameError, ValueError, AttributeError):
                pass
            logger.error(f"Error clearing database: {error_detail}")
            st.error(f"Failed to clear database: {error_detail}")
    else:
        st.warning("Please confirm to clear the database")

def get_raw_data():
    try:
        response = requests.get(f"{BASE_URL}/raw-data")
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching raw data: {str(e)}")
        return {"error": str(e)}

def download_balances(net_balances):
    if not net_balances:
        st.warning("No balances to download.")
        return
    balance_data = [
        {"User": user, "Amount": f"₹{abs(amount):.2f}", "Status": "You are owed" if amount > 0 else "You owe"}
        for user, amount in net_balances.items()
    ]
    df = pd.DataFrame(balance_data)
    csv = df.to_csv(index=False)
    st.download_button(
        label="Download Balances as CSV",
        data=csv,
        file_name=f"balances_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
    )

st.title("Splitwise App")

if "token" not in st.session_state:
    st.session_state.token = None
    st.session_state.email = None
    st.session_state.previous_expenses = None

if not st.session_state.token:
    tab1, tab2 = st.tabs(["Login", "Signup"])
    with tab1:
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")
        if st.button("Login"):
            token = login(email, password)
            if token:
                st.session_state.token = token
                st.session_state.email = email
                st.session_state.previous_expenses = None
                st.rerun()
    with tab2:
        email = st.text_input("Email", key="signup_email")
        password = st.text_input("Password", type="password", key="signup_password")
        name = st.text_input("Name", key="signup_name")
        if st.button("Signup"):
            token, previous_expenses = signup(email, password, name)
            if token:
                st.session_state.token = token
                st.session_state.email = email
                st.session_state.previous_expenses = previous_expenses
                st.rerun()
else:
    st.write(f"Logged in as: {st.session_state.email}")
    if st.button("Logout"):
        st.session_state.token = None
        st.session_state.email = None
        st.session_state.previous_expenses = None
        st.rerun()

    if st.session_state.previous_expenses and st.session_state.previous_expenses.get("expenses"):
        st.subheader("Your Previous Expenses")
        expenses = st.session_state.previous_expenses["expenses"]
        expense_data = [
            {
                "ID": exp["id"],
                "Description": exp["description"],
                "Amount": f"₹{exp['amount']:.2f}",
                "Paid By": exp["paid_by"],
                "Participants": ", ".join(exp["participants"]),
                "Splits": ", ".join([f"{k}: ₹{v:.2f}" for k, v in exp["splits"].items()]),
                "Group": exp["group_name"],
                "Created At": exp["created_at"]
            }
            for exp in expenses
        ]
        st.dataframe(pd.DataFrame(expense_data), use_container_width=True)
        if st.button("Clear Previous Expenses"):
            st.session_state.previous_expenses = None
            st.rerun()

    st.subheader("Create Group")
    with st.expander("Create a New Group"):
        group_name = st.text_input("Group Name")
        group_members = st.text_input("Members (comma-separated emails)")
        if st.button("Create Group"):
            create_group(st.session_state.token, group_name, group_members)

    st.subheader("Add Expense")
    expense_type = st.radio("Expense Type", ["Single Expense", "Group Expense"])
    
    if expense_type == "Single Expense":
        description = st.text_input("Description", key="single_desc")
        amount = st.number_input("Amount", min_value=0.01, step=0.01, key="single_amount")
        participants = st.text_input("Participants (comma-separated emails)", key="single_participants")
        paid_by = st.text_input("Paid by (email)", key="single_paid_by")
        split_method = st.radio("Split method", options=["Equal", "Custom"], key="single_split_method")
        custom_splits = {}
        if split_method == "Custom" and participants:
            participants_list = [p.strip() for p in participants.split(",") if p.strip() and is_valid_email(p.strip())]
            if not participants_list:
                st.error("Please provide at least one valid participant email")
            else:
                st.write("Enter amounts for each participant:")
                for p in participants_list:
                    val = st.number_input(f"{p}'s share", min_value=0.0, step=0.01, key=f"single_split_{p}")
                    custom_splits[p] = val
                sum_splits = sum(custom_splits.values())
                if abs(sum_splits - amount) > 0.01:
                    st.error(f"Sum of splits ({sum_splits:.2f}) must equal amount ({amount:.2f})")
                elif st.button("Add Single Expense"):
                    add_expense(st.session_state.token, description, amount, participants, paid_by, split_method, custom_splits)
        else:
            if st.button("Add Single Expense"):
                add_expense(st.session_state.token, description, amount, participants, paid_by, split_method)

    else:
        groups = get_groups(st.session_state.token)
        group_options = [(g["name"], g["id"]) for g in groups]
        group_name = st.selectbox("Select Group", options=[g[0] for g in group_options], key="group_select")
        group_id = next((g[1] for g in group_options if g[0] == group_name), None)
        
        if group_id:
            group = next((g for g in groups if g["id"] == group_id), None)
            if not group:
                st.error("Selected group not found")
            else:
                st.write(f"Group Members: {', '.join(group['members'])}")
                num_expenses = st.number_input("Number of Expenses", min_value=1, step=1, key="num_expenses")
                expenses = []
                for i in range(num_expenses):
                    with st.expander(f"Expense {i+1}"):
                        description = st.text_input("Description", key=f"group_desc_{i}")
                        amount = st.number_input("Amount", min_value=0.01, step=0.01, key=f"group_amount_{i}")
                        participants = st.text_input("Participants (comma-separated emails, must be group members)", key=f"group_participants_{i}")
                        paid_by = st.text_input("Paid by (email, must be group member)", key=f"group_paid_by_{i}")
                        split_method = st.radio("Split method", options=["Equal", "Custom"], key=f"group_split_method_{i}")
                        custom_splits = {}
                        if split_method == "Custom" and participants:
                            participants_list = [p.strip() for p in participants.split(",") if p.strip() and is_valid_email(p.strip())]
                            if not participants_list:
                                st.error("Please provide at least one valid participant email")
                            else:
                                st.write("Enter amounts for each participant:")
                                for p in participants_list:
                                    val = st.number_input(f"{p}'s share", min_value=0.0, step=0.01, key=f"group_split_{p}_{i}")
                                    custom_splits[p] = val
                                sum_splits = sum(custom_splits.values())
                                if abs(sum_splits - amount) > 0.01:
                                    st.error(f"Sum of splits ({sum_splits:.2f}) must equal amount ({amount:.2f})")
                        expenses.append({
                            "description": description,
                            "amount": amount,
                            "participants": participants,
                            "paid_by": paid_by,
                            "split_method": split_method,
                            "custom_splits": custom_splits if split_method == "Custom" else None
                        })
                if st.button("Add Group Expenses"):
                    add_group_expense(st.session_state.token, group_id, expenses)

    st.subheader("Record Payment")
    with st.expander("Record Payment"):
        data = get_user_expenses(st.session_state.token)
        net_balances = data.get("net_balances", {})
        # Log net_balances for debugging
        logger.info(f"net_balances: {json.dumps(net_balances, indent=2)}")
        st.write("Debug: Current net_balances:", net_balances)
        # Assume positive amount means you owe (adjust based on backend)
        users = [user for user, amount in net_balances.items() if amount > 0]
        if not users:
            st.write("You don't owe anyone at the moment. Check balances below or add expenses.")
        else:
            user_options = [(user, f"{user} (You owe ₹{abs(amount):.2f})") for user, amount in net_balances.items() if amount > 0]
            selected_user_label = st.selectbox("Select User to Pay", options=[u[1] for u in user_options], key="payment_user_select")
            selected_user = next((u[0] for u in user_options if u[1] == selected_user_label), None)
            amount = st.number_input("Payment Amount", min_value=0.01, step=0.01, value=abs(net_balances.get(selected_user, 0)), key="payment_amount")
            description = st.text_input("Description (optional)", value="Debt repayment", key="payment_description")
            groups = get_groups(st.session_state.token)
            group_options = [(g["name"], g["id"]) for g in groups] + [("No Group", None)]
            group_name = st.selectbox("Select Group (optional)", options=[g[0] for g in group_options], key="payment_group_select")
            group_id = next((g[1] for g in group_options if g[0] == group_name), None)
            payer = st.session_state.email
            payee = selected_user
            st.write(f"You are paying: {payee}")
            if st.button("Record Payment"):
                add_payment(st.session_state.token, amount, payer, payee, group_id, description)

    st.subheader("Your Expenses")
    if st.button("Refresh Expenses"):
        st.rerun()
    data = get_user_expenses(st.session_state.token)
    expenses = data.get("expenses", [])
    net_balances = data.get("net_balances", {})
    group_balances = data.get("group_balances", {})

    if not expenses:
        st.write("No expenses found. Add an expense using the form above, or click below to add a sample expense.")
        if st.button("Add Sample Expense"):
            add_sample_expense(st.session_state.token, st.session_state.email)
    else:
        expense_data = [
            {
                "ID": exp["id"],
                "Description": exp["description"],
                "Amount": f"₹{exp['amount']:.2f}",
                "Paid By": exp["paid_by"],
                "Participants": ", ".join(exp["participants"]),
                "Splits": ", ".join([f"{k}: ₹{v:.2f}" for k, v in exp["splits"].items()]),
                "Group": exp["group_name"] if exp.get("group_name") else "None",
                "Created At": exp["created_at"]
            } for exp in expenses
        ]
        st.dataframe(pd.DataFrame(expense_data), use_container_width=True)

        st.subheader("Send Reminder")
        expense_id = st.text_input("Expense ID (from table above)")
        to_email = st.text_input("Recipient Email")
        if st.button("Send Reminder"):
            send_reminder(st.session_state.token, expense_id, to_email)

    st.subheader("Your Balances")
    if net_balances:
        balance_data = [
            {"User": user, "Amount": f"₹{abs(amount):.2f}", "Status": "You are owed" if amount < 0 else "You owe"}
            for user, amount in net_balances.items()
        ]
        st.dataframe(pd.DataFrame(balance_data), use_container_width=True)
        download_balances(net_balances)
    else:
        st.write("No balances to display.")

    if group_balances:
        st.subheader("Group Balances")
        group_balance_data = [
            {"Group": group, "Amount": f"₹{abs(amount):.2f}", "Status": "You are owed" if amount > 0 else "You owe"}
            for group, amount in group_balances.items()
        ]
        st.dataframe(pd.DataFrame(group_balance_data), use_container_width=True)

    st.subheader("Download Expenses")
    if st.button("Download Expenses PDF"):
        download_expenses_pdf(st.session_state.token, st.session_state.email)

    st.subheader("Debug Tools")
    with st.expander("Debug Information"):
        debug_info = get_debug_info()
        st.write(debug_info)
        if st.button("Test DB Connection"):
            st.write(test_db_connection())
        if st.button("Fix Expenses"):
            fix_expenses(st.session_state.token)
        if st.button("Clear Database"):
            clear_database(st.session_state.token)
        if st.button("View Raw Data"):
            raw_data = get_raw_data()
            st.write(raw_data)