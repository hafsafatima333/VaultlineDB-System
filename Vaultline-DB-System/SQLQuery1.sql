
IF DB_ID('VaultlineDB') IS NOT NULL
BEGIN
    ALTER DATABASE VaultlineDB SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE VaultlineDB;
END
GO

CREATE DATABASE VaultlineDB;
GO


USE VaultlineDB;
GO
--user table
CREATE TABLE USERS (
    UserId           INT           IDENTITY (1001, 1) PRIMARY KEY,
    FullName         VARCHAR (80)  NOT NULL,
    Email            VARCHAR (100) UNIQUE NOT NULL,
    PasswordHash     VARCHAR (250) NOT NULL,
    Role             VARCHAR (20)  CHECK (Role IN ('Customer', 'Merchant', 'Admin')) NOT NULL,
    Status           VARCHAR (20)  DEFAULT 'Active' CHECK (Status IN ('Active', 'Locked', 'Suspended')),
    FailedLoginCount INT           DEFAULT 0,
    CreatedAt        DATETIME      DEFAULT GETDATE()
);

--wallets table
CREATE TABLE WALLETS (
    WalletId   INT             IDENTITY (5001, 1) PRIMARY KEY,
    UserID     INT             UNIQUE FOREIGN KEY REFERENCES USERS (UserId),
    AccountNo  VARCHAR (20)    UNIQUE NOT NULL,
    Balance    DECIMAL (18, 2) DEFAULT 0.00 CHECK (Balance >= 0),
    DailyLimit DECIMAL (18, 2) DEFAULT 50000.00,
    CreatedAt  DATETIME        DEFAULT GETDATE()
);

--TRANSACTION TABLE
CREATE TABLE TRANSACTIONS (
    TransactionId    INT             IDENTITY (88001, 1) PRIMARY KEY,
    SenderWalletId   INT             FOREIGN KEY REFERENCES WALLETS (WalletId),
    ReceiverWalletId INT             FOREIGN KEY REFERENCES WALLETS (WalletId),
    Amount           DECIMAL (18, 2) NOT NULL CHECK (Amount > 0),
    TransactionType  VARCHAR (20)    CHECK (TransactionType IN ('Transfer', 'Deposit', 'Refund')),
    Status           VARCHAR (20)    DEFAULT 'Verified' CHECK (Status IN ('Verified', 'Flagged', 'Failed', 'Pending')),
    TransactionDate  DATETIME        DEFAULT GETDATE()
);

-- FRAUD ALERTS TABLE
CREATE TABLE FraudAlerts (
    AlertID       INT           IDENTITY (1, 1) PRIMARY KEY,
    TransactionID INT           NULL FOREIGN KEY REFERENCES Transactions (TransactionID),
    UserID        INT           FOREIGN KEY REFERENCES Users (UserID),
    RiskLevel     VARCHAR (10)  CHECK (RiskLevel IN ('Low', 'Medium', 'High')),
    Reason        VARCHAR (255) NOT NULL,
    CreatedAt     DATETIME      DEFAULT GETDATE()
);

-- SECURITY AUDIT LOGS TABLE
CREATE TABLE SecurityLogs (
    LogID       INT           IDENTITY (1, 1) PRIMARY KEY,
    UserID      INT           NULL FOREIGN KEY REFERENCES Users (UserID),
    ActionType  VARCHAR (50)  NOT NULL,
    Description VARCHAR (255),
    Timestamp   DATETIME      DEFAULT GETDATE()
);

-- SYSTEM SETTINGS TABLE
CREATE TABLE SystemSettings (
    SettingID          INT             IDENTITY (1, 1) PRIMARY KEY,
    MaxDailyLimit      DECIMAL (18, 2) DEFAULT 50000.00,
    HighValueThreshold DECIMAL (18, 2) DEFAULT 100000.00,
    MaxFailedLogins    INT             DEFAULT 3
);

-- Initial Settings Insert
INSERT  INTO SystemSettings (MaxDailyLimit, HighValueThreshold, MaxFailedLogins)
VALUES                     (50000.00, 100000.00, 3);


GO
--FUNCTION:
CREATE OR ALTER PROCEDURE usp_RegisterUser
@FullName VARCHAR (70), @Email VARCHAR (50), @PasswordHash VARCHAR (300), @Role VARCHAR (20), @AccountNo VARCHAR (30)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @NewUserId AS INT;
    DECLARE @Calculatelimit AS DECIMAL (17, 2);
    IF (@ROLE = 'Merchant')
        SET @CalculateLimit = 200000.00;
    ELSE
        SET @CalculateLimit = 50000.00;
    --INSERTION IN USER TABLE
    INSERT  INTO USERS (FullName, Email, PasswordHash, Role)
    VALUES            (@FullName, @Email, @PasswordHash, @Role);
    SET @NewUserId = SCOPE_IDENTITY();
    --INSERTION IN WALLET TABLE
    INSERT  INTO WALLETS (UserId, AccountNo, Balance, DailyLimit)
    VALUES              (@NewUserId, @AccountNo, 0.00, @Calculatelimit);
    PRINT 'User and Wallet Successfully Registered!';
END


GO
--main
EXECUTE usp_RegisterUser @FullName = 'Hafsa Fatima', @Email = 'hafsa@vaultline.com', @PasswordHash = 'hashed_pass_123', @Role = 'Customer', @AccountNo = 'VL-10492';

EXECUTE usp_RegisterUser @FullName = 'Hashmat Ali', @Email = 'ali@vaultline.com', @PasswordHash = 'hashed_pass_456', @Role = 'Customer', @AccountNo = 'VL-10493';

EXECUTE usp_RegisterUser @FullName = 'Daniya Khan', @Email = 'daniya@gmail.com', @PasswordHash = 'hashed_pass_789', @Role = 'Merchant', @AccountNo = 'VL-10494';

EXECUTE usp_RegisterUser @FullName = 'Zubair Alam', @Email = 'zubair123@gmail.com', @PasswordHash = 'hashed_pass_675', @Role = 'Customer', @AccountNo = 'VL-10495';

SELECT *
FROM   USERS;

SELECT *
FROM   WALLETS;

SELECT *
FROM   TRANSACTIONS;


GO
---------------------------------------------------2ND METHOD
CREATE OR ALTER PROCEDURE usp_PerformTransfer
@SenderWalletId INT, @ReceiverWalletId INT, @Amount DECIMAL (18, 2)
AS
BEGIN
    SET NOCOUNT ON;
    IF (@SenderWalletId = @ReceiverWalletId)
        BEGIN
            PRINT 'ERROR: SENDER AND RECEIVER CANNOT BE THE SAME!';
            RETURN;
        END


-- NEW CHECK: Sender Account Status Check (Locked / Suspended)
    DECLARE @SenderStatus VARCHAR(20);
    SELECT @SenderStatus = u.Status FROM WALLETS w INNER JOIN  USERS u ON w.UserId = u.UserId WHERE w.WalletId = @SenderWalletId;
    IF (@SenderStatus <> 'Active')
    BEGIN 
         PRINT 'ERROR: SENDER ACCOUNT IS LOCKED OR SUSPENDED!';
        RETURN;
    END




    DECLARE @SenderBalance AS DECIMAL (18, 2);
    DECLARE @SenderDailyLimit AS DECIMAL (18, 2);
    DECLARE @TodaysTotalSent AS DECIMAL (18, 2);
    SELECT @SenderBalance = Balance,
           @SenderDailyLimit = DailyLimit
    FROM   WALLETS
    WHERE  WalletId = @SenderWalletId;
    IF (@SenderBalance IS NULL
        OR @SenderBalance < @Amount)
        BEGIN
            PRINT 'ERROR: INSUFFICIENT BALANCE!';
            RETURN;
        END
    --Add daily transfer limit check in transfer procedure
    SELECT @TodaysTotalSent = ISNULL(SUM(Amount), 0)
    FROM   TRANSACTIONS
    WHERE  SenderWalletId = @SenderWalletId
           AND TransactionType = 'Transfer'
           AND CAST (TransactionDate AS DATE) = CAST (GETDATE() AS DATE)
           AND Status <> 'Failed';
    IF ((@TodaysTotalSent + @Amount) > @SenderDailyLimit)
        BEGIN
            PRINT 'ERROR: DAILY TRANSFER LIMIT EXCEEDED!';
            RETURN;
        END
    --ACID TRANSACTIONS
    BEGIN TRANSACTION;
    BEGIN TRY
        UPDATE WALLETS
        SET    Balance = Balance - @Amount
        WHERE  WalletId = @SenderWalletId;
        UPDATE WALLETS
        SET    Balance = Balance + @Amount
        WHERE  WalletId = @ReceiverWalletId;
        INSERT  INTO TRANSACTIONS (SenderWalletId, ReceiverWalletId, Amount, TransactionType, Status)
        VALUES                   (@SenderWalletId, @ReceiverWalletId, @Amount, 'Transfer', 'Verified');
        --COMMIT CONCEPT
        COMMIT TRANSACTION;
        PRINT 'Transaction Completed Successfully!';
    END TRY
    BEGIN CATCH
        ROLLBACK;
        PRINT 'Error: Transaction Failed!';
    END CATCH
END


GO
--2ND MAIN
UPDATE WALLETS
SET    BALANCE = 10000.00
WHERE  WalletId = 5001;

EXECUTE usp_PerformTransfer @SenderWalletId = 5001, @ReceiverWalletId = 5002, @Amount = 3000.00;

--Test 1
--THIS WILL GENERATE THE ERROR --> ERROR: SENDER AND RECEIVER CANNOT BE THE SAME
EXECUTE usp_PerformTransfer @SenderWalletId = 5001, @ReceiverWalletId = 5001, @Amount = 500.00;

--Test2
--DAILY TRANSFER LIMIT EXCEEDED CASE
UPDATE WALLETS
SET    Balance = 60000.00
WHERE  WalletId = 5001;

EXECUTE usp_PerformTransfer @SenderWalletId = 5001, @ReceiverWalletId = 5002, @Amount = 55000.00;

---------------------

SELECT * FROM WALLETS ;
--SENDER
UPDATE WALLETS
SET    Balance = 500.00
WHERE  WalletId = 5004;
--REC
UPDATE WALLETS
SET    Balance = 500.00
WHERE  WalletId = 5003;
EXECUTE usp_PerformTransfer @SenderWalletId = 5003, @ReceiverWalletId = 5004, @Amount = 100.00;
GO

--------------------------------------------------------------------------------------------------3rd function
--3rd function
CREATE OR ALTER PROCEDURE usp_HandleFailedLogin
@Email VARCHAR (100)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @CurrentUserId AS INT;
    DECLARE @CurrentFailedCount AS INT;
    DECLARE @MaxLogin AS INT;
    SELECT TOP 1 @MaxLogin = MaxFailedLogins
    FROM   SystemSettings;
    SELECT @CurrentUserId = UserId,
           @CurrentFailedCount = FailedLoginCount
    FROM   USERS
    WHERE  Email = @Email;
    IF (@CurrentUserId IS NOT NULL)
        BEGIN
            SET @CurrentFailedCount = @CurrentFailedCount + 1;
            IF (@CurrentFailedCount >= @MaxLogin)
                BEGIN
                    UPDATE USERS
                    SET    FailedLoginCount = @CurrentFailedCount,
                           Status           = 'LOCKED'
                    WHERE  UserId = @CurrentUserId;
                    INSERT  INTO SecurityLogs (UserId, ActionType, Description)
                    VALUES                   (@CurrentUserId, 'ACCOUNT_LOCKED', 'Account locked due to consecutive failed login attempts.');
                    PRINT 'Account has been LOCKED due to multiple failed attempts!';
                END
            ELSE
                BEGIN
                    UPDATE USERS
                    SET    FailedLoginCount = @CurrentFailedCount
                    WHERE  UserId = @CurrentUserId;
                    PRINT 'Invalid password. Attempt recorded.';
                END
        END
    ELSE
        BEGIN
            PRINT 'User not found!';
        END
END


GO
--3rd main
EXECUTE usp_HandleFailedLogin
@Email = 'hafsa@vaultline.com';

EXECUTE usp_HandleFailedLogin 
@Email = 'hafsa@vaultline.com';

EXECUTE usp_HandleFailedLogin 
@Email = 'hafsa@vaultline.com';

EXECUTE usp_HandleFailedLogin 
@Email = 'fakeuser@gmail.com';

SELECT UserId, FullName, Email, Status,FailedLoginCount FROM   USERS WHERE  Email = 'hafsa@vaultline.com';

SELECT * FROM   SecurityLogs;


GO
------------------------------------------4th method
CREATE OR ALTER TRIGGER trg_CheckHighValueTransaction
    ON TRANSACTIONS
    AFTER INSERT
    AS BEGIN
           SET NOCOUNT ON;
           DECLARE @HightThreshold AS DECIMAL (18, 2);
           SELECT TOP 1 @HightThreshold = HighValueThreshold
           FROM   SystemSettings;
           INSERT INTO FraudAlerts (TransactionID, UserID, RiskLevel, Reason)
           SELECT i.TransactionId,
                  w.UserId,
                  'High',
                  CONCAT('High Value Transfer Detected: PKR ', i.Amount)
           FROM   inserted AS i
                  INNER JOIN
                  WALLETS AS W
                  ON i.SenderWalletId = w.WalletId
           WHERE  i.Amount >= @HightThreshold;
       END


GO
----4th main
UPDATE WALLETS
SET    Balance = 160000.00
WHERE  WalletId = 5001;

EXECUTE usp_PerformTransfer @SenderWalletId = 5001, @ReceiverWalletId = 5002, @Amount = 105000.00;

EXECUTE usp_PerformTransfer @SenderWalletId = 5004, @ReceiverWalletId = 5003, @Amount = 120000.00;

SELECT *FROM   FraudAlerts;

SELECT *FROM   WALLETS;


GO
----------------------------------------------------------5th component
CREATE OR ALTER VIEW vw_TransactionAuditSummary
AS
SELECT t.TransactionId,
       ISNULL(uSender.FullName, 'System Deposit / Top-Up') AS SenderName,
       ISNULL(wSender.AccountNo, 'N/A') AS SenderAccount,
       uReceiver.FullName AS ReceiverName,
       wReceiver.AccountNo AS ReceiverAccount,
       t.Amount,
       t.TransactionType,
       t.Status,
       t.TransactionDate
FROM TRANSACTIONS AS t
LEFT JOIN WALLETS AS wSender ON t.SenderWalletId = wSender.WalletId
LEFT JOIN USERS AS uSender ON wSender.UserID = uSender.UserId
INNER JOIN WALLETS AS wReceiver ON t.ReceiverWalletId = wReceiver.WalletId
INNER JOIN USERS AS uReceiver ON wReceiver.UserID = uReceiver.UserId;
GO
SELECT * FROM   vw_TransactionAuditSummary;


----------------------------------------------------------6th method 
CREATE OR ALTER PROCEDURE usp_DepositFunds
@WalletId INT ,
@Amount DECIMAL (18,2) 

AS 
BEGIN
    SET NOCOUNT ON ;
    --CONDITIONS
    IF(@Amount <= 0 )
    BEGIN 
    
        PRINT 'ERROR: DEPOSIT AMOUNT MUST BE GREATER THAN ZERO!';
        RETURN;
    END

    IF NOT EXISTS(SELECT 1 FROM WALLETS WHERE WalletId = @WalletId)
    BEGIN 
    PRINT 'ERROR: WALLET NOT FOUND!';
    RETURN;
    END

----------BALANCE DEPOSIT     
    BEGIN TRANSACTION ;
    BEGIN TRY 
    UPDATE WALLETS SET Balance = Balance + @Amount WHERE WalletId = @WalletId ;

    INSERT  INTO TRANSACTIONS (SenderWalletId, ReceiverWalletId, Amount, TransactionType, Status)  VALUES (NULL, @WalletId, @Amount, 'Deposit', 'Verified');
    COMMIT TRANSACTION;
    PRINT 'Funds Deposited Successfully!';
    END TRY 
    BEGIN CATCH 
    ROLLBACK TRANSACTION ;
    PRINT 'Error: Deposit Failed!';
    END CATCH 
    END;
    GO


    -------------6TH MAIN 
    EXEC usp_DepositFunds
    @WalletId = 5001,
    @Amount = 500;

    SELECT * FROM WALLETS WHERE WalletId = 5001;

    -- 2. Negative Amount Test (Error test)
   EXEC usp_DepositFunds 
   @WalletId = 5001, 
   @Amount = -500.00;

-- 3. Invalid Wallet Test (Error test)
   EXEC usp_DepositFunds 
   @WalletId = 9999, 
   @Amount = 2000.00;

   -- Verification
   SELECT * FROM WALLETS WHERE WalletId = 5001;
   SELECT * FROM TRANSACTIONS WHERE TransactionType = 'Deposit';
   GO


  
  ---------------------7th method 
  CREATE OR ALTER PROCEDURE usp_UnlockAccount
    @Email VARCHAR(100)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @CurrentUserId INT;

    SELECT @CurrentUserId = UserId FROM USERS WHERE Email = @Email ; 
IF (@CurrentUserId IS NOT NULL ) 
    BEGIN 
    UPDATE USERS SET Status = 'Active' , FailedLoginCount = 0 WHERE UserId = @CurrentUserId;
    INSERT INTO SecurityLogs(UserID , ActionType, Description) VALUES ( @CurrentUserId , 'ACCOUNT_UNLOCKED', 'Account manually unlocked by Admin.');
    PRINT 'Account has been successfully unlocked!';
    END 

ELSE 
    BEGIN 
    PRINT 'ERROR: USER NOT FOUND!';
    END 

END;
GO 


----7TH MAIN 
EXEC  usp_UnlockAccount
@Email = 'hafsa@vaultline.com';

--for verfication 
SELECT  *  FROM USERS WHERE Email = 'hafsa@vaultline.com';
SELECT * FROM SecurityLogs WHERE ActionType = 'ACCOUNT_UNLOCKED';
SELECT * FROM TRANSACTIONS;
GO


