BillBuddy
BillBuddy is a web-based expense-sharing application designed to simplify tracking and settling shared expenses among friends, roommates, or groups. Inspired by Splitwise, it offers a user-friendly interface to manage expenses, calculate balances, record payments, and generate financial reports. Built with a modern tech stack, BillBuddy ensures scalability, security, and ease of use.
Features

User Authentication: Secure signup and login with JWT-based authentication and bcrypt password hashing.
Expense Management: Add single or group expenses with equal or custom split methods.
Group Functionality: Create and manage groups for collaborative expense tracking.
Payment Tracking: Record payments to settle debts, with group-specific options.
Balance Calculation: Real-time net and group balances for clear visibility.
PDF Reports: Generate professional financial summaries using a Scala-based LaTeX service.
Debugging Tools: Admin features for database testing, data fixing, and raw data access.
Responsive UI: Streamlit-powered interface for seamless interaction.

Tech Stack

Frontend: Streamlit
Backend: FastAPI
Database: MongoDB Atlas
Reporting: Scala with MongoDB Scala Driver and LaTeX
Dependencies:
Python: pydantic, pymongo, python-jose[cryptography], passlib[bcrypt], python-dotenv, requests
Scala: org.mongodb.scala
Tools: latexmk for PDF compilation



Project Structure
BillBuddy/
├── streamlit_app.py      # Streamlit frontend
├── main.py               # FastAPI backend
├── auth.py               # Authentication logic (JWT, password hashing)
├── models.py             # Pydantic models for data validation
├── database.py           # MongoDB Atlas connection setup
├── SplitwiseReport.scala # Scala-based PDF reporting service
├── .env                  # Environment variables (not included in repo)
├── requirements.txt      # Python dependencies
└── README.md             # Project documentation

Prerequisites

Python 3.8+
MongoDB Atlas account and cluster
Scala 2.13+ and SBT (for Scala reporting)
LaTeX distribution (e.g., TeX Live) with latexmk
Git

Setup Instructions

Clone the Repository
git clone https://github.com/your-username/BillBuddy.git
cd BillBuddy


Set Up Environment Variables

Create a .env file in the root directory:MONGO_URI=mongodb+srv://<username>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
API_BASE_URL=http://localhost:8000


Replace <username>, <password>, and <cluster> with your MongoDB Atlas credentials. URL-encode special characters in the password (e.g., @ to %40).


Install Python Dependencies
pip install -r requirements.txt

Sample requirements.txt:
streamlit==1.24.0
fastapi==0.95.2
uvicorn==0.22.0
pydantic==1.10.7
pymongo==4.3.3
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
python-dotenv==1.0.0
requests==2.31.0
pandas==2.0.1


Set Up MongoDB Atlas

Create a MongoDB Atlas cluster.
Add the database splitwise_db with collections: users, expenses, groups, payments.
Update .env with the correct MONGO_URI.


Set Up Scala Environment

Install Scala and SBT.
Ensure latexmk is installed for PDF compilation.
Note: The Scala service uses a local MongoDB URI (mongodb://localhost:27017) by default. To use MongoDB Atlas, update SplitwiseReport.scala with the Atlas URI.


Run the Application

Start the FastAPI backend:uvicorn main:app --host 0.0.0.0 --port 8000


Start the Streamlit frontend:streamlit run streamlit_app.py


Run the Scala reporting service:sbt "run <user_id>"

Replace <user_id> with a valid MongoDB user ID.



Usage

Access the App

Open http://localhost:8501 in your browser to access the Streamlit interface.
Sign up or log in with an email and password.


Create a Group

Navigate to the "Create Group" section.
Enter a group name and comma-separated member emails.


Add Expenses

Choose "Single Expense" or "Group Expense".
Enter details (description, amount, participants, paid_by).
Select equal or custom split method.


Record Payments

Select a user to pay, enter amount, and optional group.
Submit to update balances.


View Balances

Check net and group balances in tables.
Download balances as CSV or expenses as PDF.


Generate PDF Reports

Use the "Download Expenses PDF" button to request a report.
The Scala service generates a LaTeX-based PDF in the output directory.



Debugging Tools

Test Database Connection: Use the /test-db endpoint or Streamlit’s debug tools.
Fix Expenses: Correct invalid custom splits via /fix-expenses.
Clear Database: Drop all collections (use with caution).
Raw Data: View raw MongoDB data via /raw-data.

Known Issues

MongoDB URI Conflict: The Scala service uses a local MongoDB URI, while the Python backend uses MongoDB Atlas. Update SplitwiseReport.scala for consistency.
Custom Splits: Ensure custom split amounts sum to the total expense amount to avoid validation errors.
PDF Dependencies: Requires latexmk and a LaTeX distribution for report generation.

Contributing
Contributions are welcome! To contribute:

Fork the repository.
Create a feature branch: git checkout -b feature/your-feature.
Commit changes: git commit -m "Add your feature".
Push to the branch: git push origin feature/your-feature.
Open a pull request with a clear description.

Please follow the code style and include tests for new features.
