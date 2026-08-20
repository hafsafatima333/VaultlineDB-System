from flask import Flask, render_template, request, jsonify
from db import get_connection
from datetime import datetime
import random

app = Flask(__name__)

def format_dt(dt):
    """Format a datetime into a clean, professional string for the UI."""
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    return dt.strftime("%d %b %Y, %I:%M %p")

@app.route('/')
def home():
    return render_template('index.html')

# ---------------- REGISTER ----------------
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    full_name = data.get('name')
    email = data.get('email')
    password = data.get('password')
    role = data.get('role')

    account_no = 'VL-' + str(random.randint(10000, 99999))

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            EXEC usp_RegisterUser 
            @FullName=?, @Email=?, @PasswordHash=?, @Role=?, @AccountNo=?
        """, full_name, email, password, role, account_no)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "account_no": account_no})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ---------------- LOGIN ----------------
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT UserId, FullName, Role, Status
            FROM USERS
            WHERE Email = ? AND PasswordHash = ?
        """, email, password)
        row = cursor.fetchone()

        if row is None:
            cursor.execute("EXEC usp_HandleFailedLogin @Email=?", email)
            conn.commit()
            conn.close()
            return jsonify({"success": False, "error": "Invalid email or password"})

        user_id, full_name, role, status = row

        if status != 'Active':
            conn.close()
            return jsonify({"success": False, "error": f"Account is {status}. Contact admin."})

        cursor.execute("""
            SELECT WalletId, AccountNo, Balance, DailyLimit
            FROM WALLETS WHERE UserID = ?
        """, user_id)
        wallet = cursor.fetchone()
        conn.close()

        wallet_data = None
        if wallet:
            wallet_data = {
                "wallet_id": wallet[0],
                "account_no": wallet[1],
                "balance": float(wallet[2]),
                "daily_limit": float(wallet[3])
            }

        return jsonify({
            "success": True, "name": full_name, "role": role, "status": status,
            "user_id": user_id, "wallet": wallet_data
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ---------------- GET WALLET (refresh balance) ----------------
@app.route('/api/wallet/<int:user_id>', methods=['GET'])
def get_wallet(user_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT WalletId, AccountNo, Balance, DailyLimit
            FROM WALLETS WHERE UserID = ?
        """, user_id)
        wallet = cursor.fetchone()
        conn.close()

        if not wallet:
            return jsonify({"success": False, "error": "Wallet not found"})

        return jsonify({"success": True, "wallet": {
            "wallet_id": wallet[0],
            "account_no": wallet[1],
            "balance": float(wallet[2]),
            "daily_limit": float(wallet[3])
        }})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ---------------- TRANSFER ----------------
@app.route('/api/transfer', methods=['POST'])
def transfer():
    data = request.json
    sender_wallet_id = data.get('sender_wallet_id')
    receiver_account_no = data.get('receiver_account_no')
    amount = data.get('amount')

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT WalletId FROM WALLETS WHERE AccountNo = ?", receiver_account_no)
        row = cursor.fetchone()
        if row is None:
            conn.close()
            return jsonify({"success": False, "error": "Recipient account not found"})
        receiver_wallet_id = row[0]

        cursor.execute("""
            EXEC usp_PerformTransfer 
            @SenderWalletId=?, @ReceiverWalletId=?, @Amount=?
        """, sender_wallet_id, receiver_wallet_id, amount)
        conn.commit()
        conn.close()
        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ---------------- DEPOSIT ----------------
@app.route('/api/deposit', methods=['POST'])
def deposit():
    data = request.json
    wallet_id = data.get('wallet_id')
    amount = data.get('amount')

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            EXEC usp_DepositFunds @WalletId=?, @Amount=?
        """, wallet_id, amount)
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ---------------- TRANSACTION HISTORY ----------------
@app.route('/api/history/<int:user_id>', methods=['GET'])
def history(user_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT WalletId, AccountNo FROM WALLETS WHERE UserID = ?
        """, user_id)
        wallet_row = cursor.fetchone()
        if not wallet_row:
            conn.close()
            return jsonify({"success": False, "error": "Wallet not found"})
        my_wallet_id, my_account_no = wallet_row

        cursor.execute("""
            SELECT TransactionId, SenderName, SenderAccount, ReceiverName, ReceiverAccount,
                   Amount, TransactionType, Status, TransactionDate
            FROM vw_TransactionAuditSummary
            WHERE SenderAccount = ? OR ReceiverAccount = ?
            ORDER BY TransactionDate DESC
        """, my_account_no, my_account_no)
        rows = cursor.fetchall()
        conn.close()

        transactions = []
        for r in rows:
            txn_id, sender_name, sender_acc, receiver_name, receiver_acc, amount, ttype, status, tdate = r

            if sender_acc == my_account_no:
                direction = 'out'
                counterparty = receiver_name
                account = receiver_acc
            else:
                direction = 'in'
                counterparty = sender_name
                account = sender_acc

            transactions.append({
                "id": txn_id,
                "counterparty": counterparty,
                "account": account,
                "amount": float(amount),
                "direction": direction,
                "type": ttype,
                "status": status,
                "date": format_dt(tdate)
            })

        return jsonify({"success": True, "transactions": transactions})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ---------------- FRAUD ALERTS ----------------
@app.route('/api/fraud-alerts', methods=['GET'])
def fraud_alerts():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT AlertID, RiskLevel, Reason, CreatedAt
            FROM FraudAlerts
            ORDER BY CreatedAt DESC
        """)
        rows = cursor.fetchall()
        conn.close()

        alerts = [{"id": r[0], "risk_level": r[1], "reason": r[2], "created_at": format_dt(r[3])} for r in rows]
        return jsonify({"success": True, "alerts": alerts})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ---------------- MERCHANT PAYMENTS ----------------
@app.route('/api/merchant-payments/<int:user_id>', methods=['GET'])
def merchant_payments(user_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT WalletId, AccountNo FROM WALLETS WHERE UserID = ?
        """, user_id)
        wallet_row = cursor.fetchone()
        if not wallet_row:
            conn.close()
            return jsonify({"success": False, "error": "Wallet not found"})
        my_wallet_id, my_account_no = wallet_row

        cursor.execute("""
            SELECT TransactionId, SenderName, SenderAccount, Amount, Status, TransactionDate
            FROM vw_TransactionAuditSummary
            WHERE ReceiverAccount = ?
            ORDER BY TransactionDate DESC
        """, my_account_no)
        rows = cursor.fetchall()
        conn.close()

        payments = []
        total = 0
        for r in rows:
            txn_id, sender_name, sender_acc, amount, status, tdate = r
            total += float(amount)
            payments.append({
                "id": txn_id,
                "customer": sender_name,
                "account": sender_acc,
                "amount": float(amount),
                "status": status,
                "date": format_dt(tdate)
            })

        return jsonify({"success": True, "payments": payments, "total": total})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ---------------- ANALYTICS (Admin only - platform-wide) ----------------
@app.route('/api/analytics', methods=['GET'])
def analytics():
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT CAST(TransactionDate AS DATE) AS TxnDate, COUNT(*) AS Cnt, ISNULL(SUM(Amount),0) AS Total
            FROM TRANSACTIONS
            WHERE TransactionDate >= DATEADD(DAY, -6, CAST(GETDATE() AS DATE))
            GROUP BY CAST(TransactionDate AS DATE)
            ORDER BY TxnDate
        """)
        daily = [{"date": str(r[0]), "count": r[1], "total": float(r[2])} for r in cursor.fetchall()]

        cursor.execute("""
            SELECT TransactionType, COUNT(*) AS Cnt, ISNULL(SUM(Amount),0) AS Total
            FROM TRANSACTIONS
            GROUP BY TransactionType
        """)
        by_type = [{"type": r[0], "count": r[1], "total": float(r[2])} for r in cursor.fetchall()]

        cursor.execute("""
            SELECT RiskLevel, COUNT(*) AS Cnt
            FROM FraudAlerts
            GROUP BY RiskLevel
        """)
        by_risk = [{"level": r[0], "count": r[1]} for r in cursor.fetchall()]

        cursor.execute("SELECT COUNT(*), ISNULL(SUM(Amount),0) FROM TRANSACTIONS")
        total_txns, total_volume = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) FROM FraudAlerts")
        total_flags = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM USERS WHERE Status = 'Locked'")
        locked_accounts = cursor.fetchone()[0]

        conn.close()

        return jsonify({
            "success": True,
            "daily": daily,
            "by_type": by_type,
            "by_risk": by_risk,
            "total_transactions": total_txns,
            "total_volume": float(total_volume),
            "total_flags": total_flags,
            "locked_accounts": locked_accounts
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == '__main__':
    app.run(debug=True)
