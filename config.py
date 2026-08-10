import os
def get_dashboard_credentials():
    return (os.getenv("DASHBOARD_USER","admin"),os.getenv("DASHBOARD_PASSWORD",""))