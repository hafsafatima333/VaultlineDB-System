from flask import Flask, render_template, request, jsonify, session
from db import get_connection
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import random
import os

app = Flask(__name__)
# Secret key for server-side sessions
app.secret_key = os.urandom(24)

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
    data = request.json or {}
    full_name = data.get('name')
    email = data.get('email')
    password = data.get('password')
    role = data.get('role')
    account_no = 'VL-' + str(random.randint(10000, 99999))

    if not full_name or not email or not password or not role:
        return jsonify({"success": False, "error": "All fields are required"})

    # Hash the password before it ever touches the database
    password_hash = generate_password_hash(password)

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            EXEC usp_RegisterUser 
            @FullName=?, @Email=?, @PasswordHash=?, @Role=?, @AccountNo=?
        """, full_name, email, password_hash, role, account_no)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "account_no": account_no})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ---------------- LOGIN ----------------
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"success": False, "error": "Email and password are required"})

    try:
        conn = get_connection()
        cursor = conn.cursor()
        # Fetch by email ONLY - password is verified in Python against the hash
        cursor.execute("""
            SELECT UserId, FullName, Email, PasswordHash, Role, Status
            FROM USERS
            WHERE Email = ?
        """, email)
        row = cursor.fetchone()

        if row is None:
            conn.close()
            return jsonify({"success": False, "error": "Invalid email or password"})

        user_id, full_name, user_email, password_hash, role, status = row

        if not check_password_hash(password_hash, password):
            cursor.execute("EXEC usp_HandleFailedLogin @Email=?", email)
            conn.commit()
            conn.close()
            return jsonify({"success": False, "error": "Invalid email or password"})

        if status != 'Active':
            conn.close()
            return jsonify({"success": False, "error": f"Account is {status}. Contact admin."})

        # Set Server-Side Session
        session['user_id'] = user_id
        session['role'] = role

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
            "success": True, 
            "name": full_name, 
            "email": user_email,
            "role": role, 
            "status": status,
            "user_id": user_id, 
            "wallet": wallet_data
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})



# ---------------- FORGOT / RESET PASSWORD ----------------
@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    data = request.json or {}
    email = data.get('email', '').strip()
    new_password = data.get('new_password', '').strip()

    if not email or not new_password:
        return jsonify({"success": False, "error": "Email and new password are required."}), 400

    if len(new_password) < 6:
        return jsonify({"success": False, "error": "Password must be at least 6 characters."}), 400

    new_hash = generate_password_hash(new_password)

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC usp_ResetUserPassword @Email = ?, @NewPasswordHash = ?", email, new_hash)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Password updated successfully! Please sign in."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ---------------- LOGOUT ----------------
@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True})

# ---------------- GET WALLET ----------------
@app.route('/api/wallet/<int:user_id>', methods=['GET'])
def get_wallet(user_id):
    if session.get('user_id') != user_id and session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Unauthorized access"}), 403

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
    current_uid = session.get('user_id')
    if not current_uid:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    data = request.json or {}
    receiver_account_no = data.get('receiver_account_no')
    
    try:
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid transfer amount"})

    if amount <= 0:
        return jsonify({"success": False, "error": "Amount must be greater than zero"})

    try:
        conn = get_connection()
        cursor = conn.cursor()

        # 1. Enforce Wallet Ownership via Session
        cursor.execute("""
            SELECT w.WalletId, w.Balance, w.DailyLimit, w.AccountNo, u.Status
            FROM WALLETS w
            INNER JOIN USERS u ON w.UserID = u.UserId
            WHERE w.UserID = ?
        """, current_uid)
        sender_row = cursor.fetchone()

        if not sender_row:
            conn.close()
            return jsonify({"success": False, "error": "Sender wallet not found"})

        sender_wallet_id, sender_balance, sender_daily_limit, sender_acc_no, sender_status = sender_row

        if sender_status != 'Active':
            conn.close()
            return jsonify({"success": False, "error": f"Sender account is {sender_status}"})

        if sender_acc_no == receiver_account_no:
            conn.close()
            return jsonify({"success": False, "error": "Sender and receiver cannot be the same"})

        # 2. Check Recipient Exists & Status
        cursor.execute("""
            SELECT w.WalletId, u.Status
            FROM WALLETS w
            INNER JOIN USERS u ON w.UserID = u.UserId
            WHERE w.AccountNo = ?
        """, receiver_account_no)
        receiver_row = cursor.fetchone()

        if not receiver_row:
            conn.close()
            return jsonify({"success": False, "error": "Recipient account not found"})

        receiver_wallet_id, receiver_status = receiver_row
        if receiver_status != 'Active':
            conn.close()
            return jsonify({"success": False, "error": f"Receiver account is {receiver_status}"})

        # 3. Check Balance
        if float(sender_balance) < amount:
            conn.close()
            return jsonify({"success": False, "error": "Insufficient balance"})

        # 4. Check Daily Limit
        cursor.execute("""
            SELECT ISNULL(SUM(Amount), 0)
            FROM TRANSACTIONS
            WHERE SenderWalletId = ?
              AND TransactionType = 'Transfer'
              AND CAST(TransactionDate AS DATE) = CAST(GETDATE() AS DATE)
              AND Status <> 'Failed'
        """, sender_wallet_id)
        today_sent = float(cursor.fetchone()[0])

        if (today_sent + amount) > float(sender_daily_limit):
            conn.close()
            return jsonify({"success": False, "error": "Daily transfer limit exceeded"})

        # 5. Execute Stored Procedure
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
    current_uid = session.get('user_id')
    if not current_uid:
        return jsonify({"success": False, "error": "Authentication required"}), 401

    data = request.json or {}
    try:
        amount = float(data.get('amount', 0))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid deposit amount"})

    if amount <= 0:
        return jsonify({"success": False, "error": "Deposit amount must be greater than zero"})

    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Enforce Wallet Ownership
        cursor.execute("SELECT WalletId FROM WALLETS WHERE UserID = ?", current_uid)
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "error": "Wallet not found for this account"})

        wallet_id = row[0]

        cursor.execute("""
            EXEC usp_DepositFunds @WalletId=?, @Amount=?
        """, wallet_id, amount)
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ---------------- CUSTOMER SUMMARY & HISTORY ----------------
@app.route('/api/history/<int:user_id>', methods=['GET'])
def history(user_id):
    if session.get('user_id') != user_id and session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Unauthorized access"}), 403

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT WalletId, AccountNo FROM WALLETS WHERE UserID = ?", user_id)
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
        money_in = 0.0
        money_out = 0.0
        pending_flagged = 0

        for r in rows:
            txn_id, sender_name, sender_acc, receiver_name, receiver_acc, amount, ttype, status, tdate = r
            amt_flt = float(amount)

            if sender_acc == my_account_no:
                direction = 'out'
                counterparty = receiver_name
                account = receiver_acc
                if status == 'Verified':
                    money_out += amt_flt
            else:
                direction = 'in'
                counterparty = sender_name
                account = sender_acc
                if status == 'Verified':
                    money_in += amt_flt

            if status in ['Pending', 'Flagged']:
                pending_flagged += 1

            transactions.append({
                "id": txn_id,
                "counterparty": counterparty,
                "account": account,
                "amount": amt_flt,
                "direction": direction,
                "type": ttype,
                "status": status,
                "date": format_dt(tdate)
            })

        return jsonify({
            "success": True, 
            "transactions": transactions,
            "metrics": {
                "money_in": money_in,
                "money_out": money_out,
                "total_txns": len(transactions),
                "pending_flagged": pending_flagged
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ---------------- MERCHANT SUMMARY & PAYMENTS ----------------
@app.route('/api/merchant-payments/<int:user_id>', methods=['GET'])
def merchant_payments(user_id):
    if session.get('user_id') != user_id and session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Unauthorized access"}), 403

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT WalletId, AccountNo FROM WALLETS WHERE UserID = ?", user_id)
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

        # Last 7 calendar days map (prevents date gaps in chart)
        today = datetime.now().date()
        date_map = {(today - timedelta(days=i)).strftime("%Y-%m-%d"): 0.0 for i in range(6, -1, -1)}

        cursor.execute("""
            SELECT CAST(TransactionDate AS DATE) AS TxnDate, ISNULL(SUM(Amount),0) AS Total
            FROM TRANSACTIONS
            WHERE ReceiverWalletId = ? AND Status = 'Verified' 
                  AND TransactionDate >= DATEADD(DAY, -6, CAST(GETDATE() AS DATE))
            GROUP BY CAST(TransactionDate AS DATE)
            ORDER BY TxnDate
        """, my_wallet_id)
        for r in cursor.fetchall():
            date_map[str(r[0])] = float(r[1])

        revenue_trend = [{"date": d, "total": date_map[d]} for d in sorted(date_map.keys())]

        cursor.execute("""
            SELECT ISNULL(SUM(Amount),0)
            FROM TRANSACTIONS
            WHERE ReceiverWalletId = ? AND Status = 'Verified'
                  AND CAST(TransactionDate AS DATE) = CAST(GETDATE() AS DATE)
        """, my_wallet_id)
        today_sales = float(cursor.fetchone()[0])
        conn.close()

        payments = []
        total_rev = 0.0
        verified_count = 0

        for r in rows:
            txn_id, sender_name, sender_acc, amount, status, tdate = r
            amt_flt = float(amount)
            if status == 'Verified':
                total_rev += amt_flt
                verified_count += 1
            payments.append({
                "id": txn_id,
                "customer": sender_name,
                "account": sender_acc,
                "amount": amt_flt,
                "status": status,
                "date": format_dt(tdate)
            })

        avg_payment = (total_rev / verified_count) if verified_count > 0 else 0.0

        return jsonify({
            "success": True, 
            "payments": payments, 
            "metrics": {
                "total_revenue": total_rev,
                "today_sales": today_sales,
                "total_txns": len(payments),
                "avg_payment": avg_payment
            },
            "revenue_trend": revenue_trend
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ---------------- ADMIN ONLY ENDPOINTS ----------------
@app.route('/api/fraud-alerts', methods=['GET'])
def fraud_alerts():
    if session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Admin privilege required"}), 403

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT f.AlertID, f.RiskLevel, f.Reason, f.CreatedAt, f.TransactionID, u.FullName
            FROM FraudAlerts f
            INNER JOIN USERS u ON f.UserID = u.UserId
            ORDER BY f.CreatedAt DESC
        """)
        rows = cursor.fetchall()
        conn.close()

        alerts = [{
            "id": r[0], 
            "risk_level": r[1], 
            "reason": r[2], 
            "created_at": format_dt(r[3]),
            "txn_id": r[4] if r[4] else "N/A",
            "user": r[5]
        } for r in rows]
        return jsonify({"success": True, "alerts": alerts})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/security-logs', methods=['GET'])
def security_logs():
    if session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Admin privilege required"}), 403

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.LogID, ISNULL(u.FullName, 'Unregistered / System') AS UserName,
                   s.ActionType, s.Description, s.Timestamp
            FROM SecurityLogs s
            LEFT JOIN USERS u ON s.UserID = u.UserId
            ORDER BY s.Timestamp DESC
        """)
        rows = cursor.fetchall()
        conn.close()

        logs = [{
            "id": r[0],
            "user": r[1],
            "action": r[2],
            "description": r[3],
            "timestamp": format_dt(r[4])
        } for r in rows]
        return jsonify({"success": True, "logs": logs})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/unlock-account', methods=['POST'])
def unlock_account():
    if session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Admin privilege required"}), 403

    data = request.json or {}
    email = data.get('email', '').strip()
    if not email:
        return jsonify({"success": False, "error": "Email is required"})

    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Check if user exists before invoking stored procedure
        cursor.execute("SELECT UserId, Status FROM USERS WHERE Email = ?", email)
        user_row = cursor.fetchone()

        if not user_row:
            conn.close()
            return jsonify({"success": False, "error": "User not found with this email"})

        cursor.execute("EXEC usp_UnlockAccount @Email=?", email)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": f"Account {email} unlocked successfully."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/analytics', methods=['GET'])
def analytics():
    if session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Admin privilege required"}), 403

    try:
        conn = get_connection()
        cursor = conn.cursor()

        today = datetime.now().date()
        date_map = {(today - timedelta(days=i)).strftime("%Y-%m-%d"): {"count": 0, "total": 0.0} for i in range(6, -1, -1)}

        cursor.execute("""
            SELECT CAST(TransactionDate AS DATE) AS TxnDate, COUNT(*) AS Cnt, ISNULL(SUM(Amount),0) AS Total
            FROM TRANSACTIONS
            WHERE TransactionDate >= DATEADD(DAY, -6, CAST(GETDATE() AS DATE))
            GROUP BY CAST(TransactionDate AS DATE)
            ORDER BY TxnDate
        """)
        for r in cursor.fetchall():
            d_str = str(r[0])
            if d_str in date_map:
                date_map[d_str] = {"count": r[1], "total": float(r[2])}

        daily = [{"date": d, "count": date_map[d]["count"], "total": date_map[d]["total"]} for d in sorted(date_map.keys())]

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

        cursor.execute("SELECT COUNT(*) FROM USERS WHERE Role <> 'Admin'")
        total_users = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM WALLETS")
        active_wallets = cursor.fetchone()[0]

        conn.close()

        return jsonify({
            "success": True,
            "daily": daily,
            "by_type": by_type,
            "by_risk": by_risk,
            "total_transactions": total_txns,
            "total_volume": float(total_volume),
            "total_flags": total_flags,
            "locked_accounts": locked_accounts,
            "total_users": total_users,
            "active_wallets": active_wallets
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == '__main__':
    app.run(debug=True)
