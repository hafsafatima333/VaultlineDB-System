CREATE DATABASE VaultlineDB;
GO 

USE VaultlineDB;
GO

--user table 
CREATE TABLE USERS(
UserId INT IDENTITY (1001, 1) PRIMARY KEY ,
FullName VARCHAR(80) NOT NULL,
Email VARCHAR (100) UNIQUE NOT NULL,
PasswordHash VARCHAR(250) NOT NULL,
Role VARCHAR(20) CHECK (Role IN ('Customer' , 'Merchant' , 'Admin')) NOT NULL,
Status VARCHAR (20) DEFAULT 'Active' CHECK (Status IN ( 'Active' , 'Locked' , 'Suspended')), 
FailedLoginCount INT DEFAULT 0 , 
CreatedAt DATETIME DEFAULT GETDATE()
);

--wallets table 
CREATE TABLE WALLETS (
WalletId INT IDENTITY (5001, 1 ) PRIMARY KEY ,
UserID INT UNIQUE FOREIGN KEY REFERENCES USERS(UserId) , 
AccountNo VARCHAR(20) UNIQUE NOT NULL,
Balance DECIMAL(18, 2) DEFAULT 0.00 CHECK (Balance >= 0),
DailyLimit DECIMAL(18,2) DEFAULT 50000.00 ,  
CreatedAt DATETIME DEFAULT GETDATE()
);


--TRANSACTION TABLE 
CREATE TABLE TRANSACTIONS (
TransactionId INT IDENTITY(88001, 1) PRIMARY KEY ,
SenderWalletId INT FOREIGN KEY REFERENCES WALLETS (WalletId),
ReceiverWalletId INT FOREIGN KEY REFERENCES WALLETS (WalletId), 
Amount DECIMAL(18, 2) NOT NULL CHECK (Amount > 0) , 
TransactionType VARCHAR(20) CHECK (TransactionType IN ('Transfer' , 'Deposit' , 'Refund')), 
Status VARCHAR(20) DEFAULT 'Verified' CHECK (Status IN ( 'Verified' , 'Flagged' , 'Failed' , 'Pending')),
TransactionDate DATETIME DEFAULT GETDATE()
);

-- FRAUD ALERTS TABLE
CREATE TABLE FraudAlerts (
    AlertID INT IDENTITY(1,1) PRIMARY KEY,
    TransactionID INT NULL FOREIGN KEY REFERENCES Transactions(TransactionID),
    UserID INT FOREIGN KEY REFERENCES Users(UserID),
    RiskLevel VARCHAR(10) CHECK (RiskLevel IN ('Low', 'Medium', 'High')),
    Reason VARCHAR(255) NOT NULL,
    CreatedAt DATETIME DEFAULT GETDATE()
);

-- SECURITY AUDIT LOGS TABLE
CREATE TABLE SecurityLogs (
    LogID INT IDENTITY(1,1) PRIMARY KEY,
    UserID INT NULL FOREIGN KEY REFERENCES Users(UserID),
    ActionType VARCHAR(50) NOT NULL,
    Description VARCHAR(255),
    Timestamp DATETIME DEFAULT GETDATE()
);

-- SYSTEM SETTINGS TABLE
CREATE TABLE SystemSettings (
    SettingID INT PRIMARY KEY IDENTITY(1,1),
    MaxDailyLimit DECIMAL(18, 2) DEFAULT 50000.00,
    HighValueThreshold DECIMAL(18, 2) DEFAULT 100000.00,
    MaxFailedLogins INT DEFAULT 3
);
-- Initial Settings Insert
INSERT INTO SystemSettings (MaxDailyLimit, HighValueThreshold, MaxFailedLogins) VALUES (50000.00, 100000.00, 3);


--FUNCTION: 
CREATE PROCEDURE  sp_RegisterUser 
@FullName VARCHAR(70),
@Email VARCHAR(50),
@PasswordHash VARCHAR(300),
@Role VARCHAR(20), 
@AccountNo VARCHAR(30)

AS 
BEGIN 
SET NOCOUNT ON;
DECLARE @NewUserId INT ;
DECLARE @Calculatelimit DECIMAL(17,2);
IF ( @ROLE = 'Merchant')
      SET @CalculateLimit = 200000.00;
ELSE
    SET @CalculateLimit = 50000.00;

--INSERTION IN USER TABLE 
INSERT INTO USERS (FullName, Email, PasswordHash, Role) VALUES ( @FullName, @Email,@PasswordHash,@Role);
SET @NewUserId = SCOPE_IDENTITY();
 
--INSERTION IN WALLET TABLE 
INSERT INTO WALLETS(UserId, AccountNo, Balance, DailyLimit) VALUES (@NewUserId , @AccountNo, 0.00, @Calculatelimit);
PRINT 'User and Wallet Successfully Registered!';
END;
GO


--main 
EXEC sp_RegisterUser 
    @FullName = 'Hafsa Fatima', 
    @Email = 'hafsa@vaultline.com', 
    @PasswordHash = 'hashed_pass_123', 
    @Role = 'Customer', 
    @AccountNo = 'VL-10492';

EXEC sp_RegisterUser     
@FullName = 'Hashmat Ali',
    @Email = 'ali@vaultline.com',
    @PasswordHash = 'hashed_pass_456',
    @Role = 'Customer', 
    @AccountNo = 'VL-10493';


    SELECT * FROM USERS;
SELECT * FROM WALLETS;


---------------------------------------------------2ND METHOD
CREATE PROCEDURE sp_PerformTransfer
     @SenderWalletId INT ,
     @ReceiverWalletId INT ,
     @Amount Decimal(18,2)
AS
BEGIN 
SET NOCOUNT ON;
DECLARE @SenderBalance DECIMAL(18,2);

SELECT @SenderBalance = Balance FROM WALLETS WHERE WalletId = @SenderWalletId;    
IF(@SenderBalance IS NULL OR @SenderBalance < @Amount)
BEGIN 
     PRINT 'ERROR: INSUFFICIENT BALANCE!';
     RETURN;
END

BEGIN TRANSACTION; 
BEGIN TRY 
    UPDATE WALLETS SET Balance = Balance - @Amount WHERE WalletId = @SenderWalletId ;

    UPDATE WALLETS SET Balance = Balance + @Amount WHERE WalletId = @ReceiverWalletId;

    INSERT INTO TRANSACTIONS( SenderWalletId , ReceiverWalletId, Amount, TransactionType, Status) VALUES ( @SenderWalletId , @ReceiverWalletId, @Amount , 'Transfer', 'Verified');


--COMMIT CONCEPT
    COMMIT TRANSACTION ;
    PRINT 'Transaction Completed Successfully!';
    END TRY


    BEGIN CATCH 
    ROLLBACK TRANSACTION;
    PRINT 'Error: Transaction Failed!';
    END CATCH

END;
GO

--2ND MAIN 
UPDATE WALLETS SET BALANCE = 10000.00  WHERE WalletId = 5001;

EXEC sp_PerformTransfer
@SenderWalletId = 5001,
@ReceiverWalletId = 5002,
@Amount = 3000.00;


--------------------------------------------------------------------------------------------------3rd function

--3rd function
CREATE PROCEDURE sp_HandleFailedLogin
    @Email VARCHAR(100)
AS
BEGIN 
    SET NOCOUNT ON;

    DECLARE @CurrentUserId INT;
    DECLARE @CurrentFailedCount INT;
    DECLARE @MaxLogin INT;

  
    SELECT TOP 1 @MaxLogin = MaxFailedLogins FROM SystemSettings;
    SELECT @CurrentUserId = UserId, @CurrentFailedCount = FailedLoginCount FROM USERS WHERE Email = @Email;

  
    IF (@CurrentUserId IS NOT NULL)
 BEGIN 
       
        SET @CurrentFailedCount = @CurrentFailedCount + 1;

       
        IF (@CurrentFailedCount >= @MaxLogin)
 BEGIN
            UPDATE USERS 
            SET FailedLoginCount = @CurrentFailedCount, Status = 'LOCKED' 
            WHERE UserId = @CurrentUserId;

            INSERT INTO SecurityLogs (UserId, ActionType, Description) 
            VALUES (@CurrentUserId, 'ACCOUNT_LOCKED', 'Account locked due to consecutive failed login attempts.');

            PRINT 'Account has been LOCKED due to multiple failed attempts!';
 END 
        ELSE 
 BEGIN 
            UPDATE USERS 
            SET FailedLoginCount = @CurrentFailedCount 
            WHERE UserId = @CurrentUserId;

            PRINT 'Invalid password. Attempt recorded.';
 END 
 END
    ELSE 
 BEGIN 
        PRINT 'User not found!';
    END 
END;
GO


--3rd main
 EXEC sp_HandleFailedLogin   @Email = 'hafsa@vaultline.com';
 EXEC sp_HandleFailedLogin   @Email = 'hafsa@vaultline.com';
 EXEC sp_HandleFailedLogin   @Email = 'hafsa@vaultline.com';


 EXEC sp_HandleFailedLogin @Email = 'fakeuser@gmail.com';
 SELECT UserId, FullName, Email, Status, FailedLoginCount FROM USERS WHERE Email = 'hafsa@vaultline.com';

 SELECT * FROM SecurityLogs;


 ----------------------------------------4th method  

 CREATE OR ALTER TRIGGER trg_CheckHighValueTransaction 
 ON TRANSACTIONS
 AFTER INSERT 
 AS

 BEGIN
      SET NOCOUNT ON ; 
      DECLARE @HightThreshold DECIMAL (18,2);


      SELECT TOP 1 @HightThreshold = HighValueThreshold FROM SystemSettings; 

      INSERT INTO FraudAlerts (TransactionID, UserID, RiskLevel, Reason) SELECT i.TransactionId, w.UserId ,'High' , CONCAT ('High Value Transfer Detected: PKR ', i.Amount ) 

      FROM inserted i INNER JOIN WALLETS W ON i.SenderWalletId = w.WalletId  WHERE i.Amount >= @HightThreshold; 

END;
GO 

----4th main 
UPDATE WALLETS SET Balance = 160000.00 WHERE WalletId = 5001;
EXEC sp_PerformTransfer 
@SenderWalletId = 5001,
    @ReceiverWalletId = 5002,
    @Amount = 105000.00;


    SELECT * FROM FraudAlerts;

       
