import pyodbc

def get_connection():
    return pyodbc.connect(
        'DRIVER={ODBC Driver 17 for SQL Server};'
        'SERVER=.\\SQLEXPRESS;'
        'DATABASE=VaultlineDB;'
        'Trusted_Connection=yes;'
    )