from flask import Flask, render_template, request, jsonify, session
from db import get_connection
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import random
import os

app = Flask(__name__)

# FIXED: Static secret key so sessions survive server restarts/hot-reloads
app.secret_key = 'vaultline-core-secure-key-2026-production-vault'

def format_dt(dt):
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    return dt.strftime("%d %b %Y, %I:%M %p")

def get_sent_today(cursor, wallet_id):
    cursor.execute("""
        SELECT ISNULL(SUM(Amount), 0)
        FROM TRANSACTIONS
        WHERE SenderWalletId = ?
          AND TransactionType = 'Transfer'
          AND CAST(TransactionDate AS DATE) = CAST(GETDATE() AS DATE)
          AND Status <> 'Failed'
    """, wallet_id)
    return float(cursor.fetchone()[0])

def get_wallet_payload(cursor, user_id):
    """Shared helper: builds the wallet dict used by login/check-session/get_wallet."""
    cursor.execute("""
        SELECT WalletId, AccountNo, Balance, DailyLimit
        FROM WALLETS WHERE UserID = ?
    """, user_id)
    wallet = cursor.fetchone()
    if not wallet:
        return None
    sent_today = get_sent_today(cursor, wallet[0])
    return {
        "wallet_id": wallet[0],
        "account_no": wallet[1],
        "balance": float(wallet[2]),
        "daily_limit": float(wallet[3]),
        "sent_today": sent_today
    }

@app.route('/')
def home():
    return render_template('index.html')

# ---------------- REGISTER ----------------
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json or {}
    full_name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    role = data.get('role', 'Customer')
    account_no = 'VL-' + str(random.randint(10000, 99999))

    if not full_name or not email or not password or not role:
        return jsonify({"success": False, "error": "All fields are required"}), 400

    if len(password) < 6:
        return jsonify({"success": False, "error": "Password must contain at least 6 characters."}), 400

    password_hash = generate_password_hash(password)

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM USERS WHERE Email = ?", email)
        if cursor.fetchone():
            conn.close()
            return jsonify({"success": False, "error": "This email is already registered. Please sign in instead."}), 400

        cursor.execute("""
            EXEC usp_RegisterUser 
            @FullName=?, @Email=?, @PasswordHash=?, @Role=?, @AccountNo=?
        """, full_name, email, password_hash, role, account_no)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "account_no": account_no})
    except Exception as e:
        error_text = str(e)
        if "UNIQUE KEY" in error_text or "duplicate key" in error_text.lower():
            return jsonify({"success": False, "error": "This email is already registered. Please sign in instead."}), 400
        return jsonify({"success": False, "error": "Registration failed. Please try again."}), 500

# ---------------- LOGIN ----------------
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    email = data.get('email', '').strip()
    password = data.get('password', '')

    if not email or not password:
        return jsonify({"success": False, "error": "Email and password are required"}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT UserId, FullName, Email, PasswordHash, Role, Status
            FROM USERS
            WHERE Email = ?
        """, email)
        row = cursor.fetchone()

        if row is None:
            conn.close()
            return jsonify({"success": False, "error": "Invalid email or password"}), 401

        user_id, full_name, user_email, password_hash, role, status = row

        if not check_password_hash(password_hash, password):
            if role != 'Admin':
                cursor.execute("EXEC usp_HandleFailedLogin @Email=?", email)
                conn.commit()
            conn.close()
            return jsonify({"success": False, "error": "Invalid email or password"}), 401

        if status != 'Active':
            conn.close()
            if status == 'Locked':
                return jsonify({"success": False, "error": "Account is Locked due to failed login attempts. Contact Admin to unlock."}), 403
            return jsonify({"success": False, "error": f"Account is {status}. Contact Admin."}), 403

        cursor.execute("UPDATE USERS SET FailedLoginCount = 0 WHERE UserId = ?", user_id)
        conn.commit()

        session['user_id'] = user_id
        session['role'] = role

        wallet_data = get_wallet_payload(cursor, user_id)
        conn.close()

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
        return jsonify({"success": False, "error": str(e)}), 500

# ---------------- CHECK SESSION (page-reload persistence) ----------------
@app.route('/api/check-session', methods=['GET'])
def check_session():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "No active session"}), 401

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT UserId, FullName, Email, Role, Status
            FROM USERS WHERE UserId = ?
        """, user_id)
        row = cursor.fetchone()

        if not row:
            conn.close()
            session.clear()
            return jsonify({"success": False, "error": "User no longer exists"}), 401

        uid, full_name, email, role, status = row

        if status != 'Active':
            conn.close()
            session.clear()
            return jsonify({"success": False, "error": f"Account is {status}"}), 403

        wallet_data = get_wallet_payload(cursor, uid)
        conn.close()

        return jsonify({
            "success": True,
            "name": full_name,
            "email": email,
            "role": role,
            "status": status,
            "user_id": uid,
            "wallet": wallet_data
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

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

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT Status, Role FROM USERS WHERE Email = ?", email)
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "error": "No account found with this email."}), 404

        status, role = row
        if status in ['Locked', 'Suspended']:
            conn.close()
            return jsonify({
                "success": False, 
                "error": f"This account is currently {status}. Self-reset is restricted for security. Please submit an account unlock request instead."
            }), 403

        new_hash = generate_password_hash(new_password)
        cursor.execute("EXEC usp_ResetUserPassword @Email = ?, @NewPasswordHash = ?", email, new_hash)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Password updated successfully! You can now sign in."})
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
        wallet_data = get_wallet_payload(cursor, user_id)
        conn.close()

        if not wallet_data:
            return jsonify({"success": False, "error": "Wallet not found"}), 404

        return jsonify({"success": True, "wallet": wallet_data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

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
        return jsonify({"success": False, "error": "Invalid transfer amount"}), 400

    if amount <= 0:
        return jsonify({"success": False, "error": "Amount must be greater than zero"}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT w.WalletId, w.Balance, w.DailyLimit, w.AccountNo, u.Status
            FROM WALLETS w
            INNER JOIN USERS u ON w.UserID = u.UserId
            WHERE w.UserID = ?
        """, current_uid)
        sender_row = cursor.fetchone()

        if not sender_row:
            conn.close()
            return jsonify({"success": False, "error": "Sender wallet not found"}), 404

        sender_wallet_id, sender_balance, sender_daily_limit, sender_acc_no, sender_status = sender_row

        if sender_status != 'Active':
            conn.close()
            return jsonify({"success": False, "error": f"Sender account is {sender_status}"}), 403

        if sender_acc_no == receiver_account_no:
            conn.close()
            return jsonify({"success": False, "error": "Sender and receiver cannot be the same"}), 400

        cursor.execute("""
            SELECT w.WalletId, u.Status
            FROM WALLETS w
            INNER JOIN USERS u ON w.UserID = u.UserId
            WHERE w.AccountNo = ?
        """, receiver_account_no)
        receiver_row = cursor.fetchone()

        if not receiver_row:
            conn.close()
            return jsonify({"success": False, "error": "Recipient account not found"}), 404

        receiver_wallet_id, receiver_status = receiver_row
        if receiver_status != 'Active':
            conn.close()
            return jsonify({"success": False, "error": f"Receiver account is {receiver_status}"}), 403

        if float(sender_balance) < amount:
            conn.close()
            return jsonify({"success": False, "error": "Insufficient balance"}), 400

        today_sent = get_sent_today(cursor, sender_wallet_id)

        if (today_sent + amount) > float(sender_daily_limit):
            conn.close()
            return jsonify({"success": False, "error": "Daily transfer limit exceeded"}), 400

        cursor.execute("""
            EXEC usp_PerformTransfer 
            @SenderWalletId=?, @ReceiverWalletId=?, @Amount=?
        """, sender_wallet_id, receiver_wallet_id, amount)
        conn.commit()
        conn.close()
        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

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
        return jsonify({"success": False, "error": "Invalid deposit amount"}), 400

    if amount <= 0:
        return jsonify({"success": False, "error": "Deposit amount must be greater than zero"}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT WalletId FROM WALLETS WHERE UserID = ?", current_uid)
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "error": "Wallet not found for this account"}), 404

        wallet_id = row[0]
        cursor.execute("EXEC usp_DepositFunds @WalletId=?, @Amount=?", wallet_id, amount)
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

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
            return jsonify({"success": False, "error": "Wallet not found"}), 404
        my_wallet_id, my_account_no = wallet_row

        cursor.execute("""
            SELECT TransactionId, SenderName, SenderAccount, ReceiverName, ReceiverAccount,
                   Amount, TransactionType, Status, IsFlagged, TransactionDate
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
            txn_id, sender_name, sender_acc, receiver_name, receiver_acc, amount, ttype, status, is_flagged, tdate = r
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

            if status in ['Pending', 'Flagged'] or bool(is_flagged):
                pending_flagged += 1

            transactions.append({
                "id": txn_id,
                "counterparty": counterparty,
                "account": account,
                "amount": amt_flt,
                "direction": direction,
                "type": ttype,
                "status": status,
                "is_flagged": bool(is_flagged),
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
        return jsonify({"success": False, "error": str(e)}), 500

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
            return jsonify({"success": False, "error": "Wallet not found"}), 404
        my_wallet_id, my_account_no = wallet_row

        cursor.execute("""
            SELECT TransactionId, SenderName, SenderAccount, Amount, Status, IsFlagged, TransactionDate
            FROM vw_TransactionAuditSummary
            WHERE ReceiverAccount = ?
            ORDER BY TransactionDate DESC
        """, my_account_no)
        rows = cursor.fetchall()

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
            txn_id, sender_name, sender_acc, amount, status, is_flagged, tdate = r
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
                "is_flagged": bool(is_flagged),
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
        return jsonify({"success": False, "error": str(e)}), 500

# ---------------- ADMIN ONLY ENDPOINTS ----------------
@app.route('/api/fraud-alerts', methods=['GET'])
def fraud_alerts():
    if session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Admin privilege required"}), 403

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT f.AlertID, f.RiskLevel, f.Reason, f.CreatedAt, f.TransactionID, u.FullName, u.Email
            FROM FraudAlerts f
            INNER JOIN USERS u ON f.UserID = u.UserId
            ORDER BY f.CreatedAt DESC
        """)
        rows = cursor.fetchall()

        alerts = [{
            "id": r[0], 
            "risk_level": r[1], 
            "reason": r[2], 
            "created_at": format_dt(r[3]),
            "txn_id": r[4] if r[4] else "N/A",
            "user": r[5],
            "email": r[6]
        } for r in rows]

        cursor.execute("SELECT Email, FullName, Status FROM USERS WHERE Role <> 'Admin' ORDER BY FullName")
        all_users = [{"email": r[0], "name": r[1], "status": r[2]} for r in cursor.fetchall()]
        conn.close()

        return jsonify({"success": True, "alerts": alerts, "users": all_users})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

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
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/unlock-account', methods=['POST'])
def unlock_account():
    if session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Admin privilege required"}), 403

    data = request.json or {}
    email = data.get('email', '').strip()
    if not email:
        return jsonify({"success": False, "error": "Email is required"}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT UserId, Status FROM USERS WHERE Email = ?", email)
        user_row = cursor.fetchone()

        if not user_row:
            conn.close()
            return jsonify({"success": False, "error": "User not found with this email"}), 404

        uid = user_row[0]
        cursor.execute("UPDATE USERS SET Status = 'Active', FailedLoginCount = 0 WHERE UserId = ?", uid)
        cursor.execute("""
            INSERT INTO SecurityLogs(UserID, ActionType, Description)
            VALUES(?, 'ACCOUNT_UNLOCKED', 'Account status restored to Active by Admin.')
        """, uid)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": f"Account {email} successfully unlocked and set Active."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/freeze-account', methods=['POST'])
def freeze_account():
    if session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Admin privilege required"}), 403

    data = request.json or {}
    email = data.get('email', '').strip()
    if not email:
        return jsonify({"success": False, "error": "Email is required"}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT UserId, Status FROM USERS WHERE Email = ?", email)
        user_row = cursor.fetchone()

        if not user_row:
            conn.close()
            return jsonify({"success": False, "error": "User not found with this email"}), 404

        uid, status = user_row
        cursor.execute("UPDATE USERS SET Status = 'Suspended' WHERE UserId = ?", uid)
        cursor.execute("""
            INSERT INTO SecurityLogs(UserID, ActionType, Description) 
            VALUES (?, 'ACCOUNT_SUSPENDED', 'Account manually frozen/suspended by Admin investigation.')
        """, uid)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": f"Account {email} has been suspended/frozen."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ---------------- ADMIN: REFUND / REVERSE A TRANSACTION ----------------
@app.route('/api/admin/refund', methods=['POST'])
def refund_transaction():
    if session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Admin privilege required"}), 403

    admin_id = session.get('user_id')
    data = request.json or {}
    transaction_id = data.get('transaction_id')

    if not transaction_id:
        return jsonify({"success": False, "error": "Transaction ID is required"}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT SenderWalletId, ReceiverWalletId, Amount, Status, TransactionType
            FROM TRANSACTIONS WHERE TransactionId = ?
        """, transaction_id)
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "error": "Transaction not found."}), 404

        sender_wallet_id, receiver_wallet_id, amount, status, ttype = row

        if ttype != 'Transfer' or sender_wallet_id is None or receiver_wallet_id is None:
            conn.close()
            return jsonify({"success": False, "error": "Only wallet-to-wallet transfers can be refunded."}), 400

        cursor.execute("""
            SELECT COUNT(*) FROM TRANSACTIONS
            WHERE TransactionType = 'Refund' AND Amount = ?
                  AND SenderWalletId = ? AND ReceiverWalletId = ?
        """, amount, receiver_wallet_id, sender_wallet_id)
        already_refunded = cursor.fetchone()[0] > 0
        if already_refunded:
            conn.close()
            return jsonify({"success": False, "error": "This transaction has already been refunded."}), 400

        cursor.execute("EXEC usp_RefundTransaction @TransactionId=?, @AdminUserId=?", transaction_id, admin_id)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": f"Transaction #{transaction_id} refunded successfully."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

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

        cursor.execute("SELECT COUNT(*) FROM USERS WHERE Status = 'Locked' OR Status = 'Suspended'")
        locked_accounts = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM USERS WHERE Role <> 'Admin'")
        total_users = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM WALLETS w
            INNER JOIN USERS u ON w.UserID = u.UserId
            WHERE u.Status = 'Active'
        """)
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
        return jsonify({"success": False, "error": str(e)}), 500

# ============================================================
# ACCOUNT UNLOCK COMPLAINT SYSTEM
# ============================================================

def _verify_complaint_against_db(cursor, submitted_email, submitted_name, submitted_ref):
    cursor.execute("""
        SELECT u.UserId, u.FullName, u.Status, w.AccountNo
        FROM USERS u
        LEFT JOIN WALLETS w ON w.UserID = u.UserId
        WHERE u.Email = ?
    """, submitted_email)
    row = cursor.fetchone()

    if not row:
        return {
            "verified": False, 
            "user_id": None, 
            "account_status": None,
            "reason": "No user exists with the submitted email."
        }

    user_id, db_name, db_status, db_account_no = row
    ref = (submitted_ref or "").strip()

    if not ref:
        return {
            "verified": False, 
            "user_id": user_id, 
            "account_status": db_status,
            "reason": "Account reference/UserId cannot be empty."
        }

    # Safe comparison: only compare account number if wallet exists
    acc_matches = bool(db_account_no and ref.upper() == db_account_no.strip().upper())
    uid_matches = bool(ref == str(user_id))

    if not (acc_matches or uid_matches):
        return {
            "verified": False, 
            "user_id": user_id, 
            "account_status": db_status,
            "reason": "Submitted account number/UserId does not belong to this email."
        }

    if (submitted_name or "").strip().lower() != (db_name or "").strip().lower():
        return {
            "verified": False, 
            "user_id": user_id, 
            "account_status": db_status,
            "reason": "Submitted name does not match the account on record."
        }

    if db_status not in ("Locked", "Suspended"):
        return {
            "verified": False, 
            "user_id": user_id, 
            "account_status": db_status,
            "reason": f"Account is currently {db_status}, not Locked/Suspended — no unlock needed."
        }

    return {
        "verified": True, 
        "user_id": user_id, 
        "account_status": db_status, 
        "reason": "All checks passed."
    }

@app.route('/api/complaints', methods=['POST'])
def submit_complaint():
    data = request.json or {}
    full_name = data.get('full_name', '').strip()
    email = data.get('email', '').strip()
    account_ref = data.get('account_ref', '').strip()
    complaint_text = data.get('complaint_text', '').strip()

    if not full_name or not email or not account_ref or not complaint_text:
        return jsonify({"success": False, "error": "All fields are required."}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()

        result = _verify_complaint_against_db(cursor, email, full_name, account_ref)

        cursor.execute("""
            INSERT INTO Complaints
                (SubmittedFullName, SubmittedEmail, SubmittedAccountRef, ComplaintText,
                 VerifiedUserId, VerificationResult, AccountStatusAtReview)
            OUTPUT INSERTED.ComplaintId
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, full_name, email, account_ref, complaint_text,
             result["user_id"] if result["verified"] else None,
             "Verified" if result["verified"] else "Failed",
             result["account_status"])
        new_id = cursor.fetchone()[0]
        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "complaint_id": new_id,
            "message": "Your complaint has been submitted. An admin will review it shortly."
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/complaints/<int:complaint_id>/status', methods=['GET'])
def complaint_status(complaint_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT Status, AdminRemarks, CreatedAt, ReviewedAt
            FROM Complaints WHERE ComplaintId = ?
        """, complaint_id)
        row = cursor.fetchone()
        conn.close()
        if not row:
            return jsonify({"success": False, "error": "Complaint not found."}), 404
        status, remarks, created_at, reviewed_at = row
        return jsonify({
            "success": True,
            "status": status,
            "admin_remarks": remarks,
            "created_at": format_dt(created_at),
            "reviewed_at": format_dt(reviewed_at)
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/admin/complaints', methods=['GET'])
def admin_list_complaints():
    if session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Admin privilege required"}), 403

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.ComplaintId, c.SubmittedFullName, c.SubmittedEmail, c.SubmittedAccountRef,
                   c.ComplaintText, c.VerifiedUserId, c.VerificationResult, c.AccountStatusAtReview,
                   c.Status, c.AdminRemarks, c.CreatedAt, c.ReviewedAt,
                   u.Status AS LiveStatus
            FROM Complaints c
            LEFT JOIN USERS u ON u.UserId = c.VerifiedUserId
            ORDER BY c.CreatedAt DESC
        """)
        rows = cursor.fetchall()
        conn.close()

        complaints = [{
            "id": r[0],
            "submitted_name": r[1],
            "submitted_email": r[2],
            "submitted_ref": r[3],
            "complaint_text": r[4],
            "verified_user_id": r[5],
            "verification_result": r[6],
            "account_status_at_review": r[7],
            "status": r[8],
            "admin_remarks": r[9],
            "created_at": format_dt(r[10]),
            "reviewed_at": format_dt(r[11]),
            "live_account_status": r[12]
        } for r in rows]

        return jsonify({"success": True, "complaints": complaints})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/admin/complaints/<int:complaint_id>/verify', methods=['POST'])
def admin_reverify_complaint(complaint_id):
    if session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Admin privilege required"}), 403

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT SubmittedFullName, SubmittedEmail, SubmittedAccountRef, Status
            FROM Complaints WHERE ComplaintId = ?
        """, complaint_id)
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "error": "Complaint not found."}), 404

        full_name, email, account_ref, status = row
        result = _verify_complaint_against_db(cursor, email, full_name, account_ref)

        cursor.execute("""
            UPDATE Complaints
            SET VerifiedUserId = ?, VerificationResult = ?, AccountStatusAtReview = ?
            WHERE ComplaintId = ?
        """, result["user_id"] if result["verified"] else None,
             "Verified" if result["verified"] else "Failed",
             result["account_status"], complaint_id)
        conn.commit()
        conn.close()

        return jsonify({"success": True, "verification": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/admin/complaints/<int:complaint_id>/unlock', methods=['POST'])
def admin_unlock_from_complaint(complaint_id):
    if session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Admin privilege required"}), 403

    admin_id = session.get('user_id')
    data = request.json or {}
    remarks = data.get('remarks', '').strip()

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT SubmittedFullName, SubmittedEmail, SubmittedAccountRef, Status
            FROM Complaints WHERE ComplaintId = ?
        """, complaint_id)
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "error": "Complaint not found."}), 404

        full_name, email, account_ref, current_status = row
        if current_status != 'Pending':
            conn.close()
            return jsonify({"success": False, "error": f"Complaint already {current_status}."}), 400

        result = _verify_complaint_against_db(cursor, email, full_name, account_ref)

        if not result["verified"]:
            conn.close()
            return jsonify({"success": False, "error": f"Cannot unlock: verification failed — {result['reason']}"}), 400

        verified_user_id = result["user_id"]

        cursor.execute("UPDATE USERS SET Status = 'Active', FailedLoginCount = 0 WHERE UserId = ?", verified_user_id)

        final_remarks = remarks or "Account verified successfully. Account was locked and has now been unlocked."
        cursor.execute("""
            UPDATE Complaints
            SET Status = 'Resolved', AdminRemarks = ?, ReviewedAt = GETDATE(), ReviewedBy = ?,
                VerifiedUserId = ?, VerificationResult = 'Verified', AccountStatusAtReview = 'Active'
            WHERE ComplaintId = ?
        """, final_remarks, admin_id, verified_user_id, complaint_id)

        cursor.execute("""
            INSERT INTO SecurityLogs (UserID, ActionType, Description)
            VALUES (?, 'ACCOUNT_UNLOCKED', ?)
        """, verified_user_id, f"Admin unlocked account after verified complaint #{complaint_id}.")

        conn.commit()
        conn.close()

        return jsonify({"success": True, "message": f"Account for UserId {verified_user_id} unlocked and complaint resolved."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/admin/complaints/<int:complaint_id>/reject', methods=['POST'])
def admin_reject_complaint(complaint_id):
    if session.get('role') != 'Admin':
        return jsonify({"success": False, "error": "Admin privilege required"}), 403

    admin_id = session.get('user_id')
    data = request.json or {}
    remarks = data.get('remarks', '').strip()

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT Status FROM Complaints WHERE ComplaintId = ?", complaint_id)
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "error": "Complaint not found."}), 404
        if row[0] != 'Pending':
            conn.close()
            return jsonify({"success": False, "error": f"Complaint already {row[0]}."}), 400

        final_remarks = remarks or "Complaint rejected. Submitted account information could not be verified."
        cursor.execute("""
            UPDATE Complaints
            SET Status = 'Rejected', AdminRemarks = ?, ReviewedAt = GETDATE(), ReviewedBy = ?
            WHERE ComplaintId = ?
        """, final_remarks, admin_id, complaint_id)

        cursor.execute("""
            INSERT INTO SecurityLogs (UserID, ActionType, Description)
            VALUES (NULL, 'COMPLAINT_REJECTED', ?)
        """, f"Admin rejected complaint #{complaint_id}: {final_remarks}")

        conn.commit()
        conn.close()

        return jsonify({"success": True, "message": "Complaint rejected."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)
