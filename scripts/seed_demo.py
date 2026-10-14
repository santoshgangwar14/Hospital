"""python -m scripts.seed_demo  -> creates two demo hospitals on different plans with different branding."""
from datetime import date, timedelta
from fastapi.testclient import TestClient
from hms.app import app
from hms.db import platform_tx
from hms.security import hash_pw

c = TestClient(app)
with platform_tx() as cur:
    cur.execute("insert into platform_users(username,name,password_hash) values('owner','Platform Owner',%s) on conflict (username) do nothing", (hash_pw("ownerpass123"),))
tok = c.post("/api/platform/login", json={"username": "owner", "password": "ownerpass123"}).json()["token"]
P = {"Authorization": "Bearer " + tok}
DEMOS = [("demo-city", "Demo City Hospital", "basic", "#0b6e99", "#e8f4f8", "DC", "Care for every family"),
         ("demo-sunrise", "Demo Sunrise Hospital", "advance", "#b3472b", "#fbece6", "SR", "Healing with trust")]
for code, name, plan, p1, p2, pre, tag in DEMOS:
    r = c.post("/api/platform/tenants", headers=P, json={"name": name, "code": code, "plan": plan, "primary": p1, "secondary": p2, "tagline": tag,
               "uhid_prefix": pre, "bill_prefix": pre + "B", "admin_password": "admin12345", "phone": "0000000000", "address": "Demo address"})
    h = {"X-Tenant": code}
    A = {**h, "Authorization": "Bearer " + c.post("/api/auth/login", headers=h, json={"username": "admin", "password": "admin12345"}).json()["token"]}
    if r.status_code == 409:
        print(code, "already exists")
        if plan == "advance":   # idempotent IPD demo data (conflicts are ignored)
            c.post("/api/wards", headers=A, json={"name": "General Ward", "daily_rate": 1000, "beds": ["G1", "G2", "G3", "G4"]})
            c.post("/api/wards", headers=A, json={"name": "Private Room", "daily_rate": 3000, "beds": ["P1", "P2"]})
            c.post("/api/packages", headers=A, json={"name": "Demo Minor Procedure Package", "price": 15000, "includes_room": False})
            c.post("/api/users", headers=A, json={"username": "nurse", "name": "Ward Nurse", "role": "nurse", "password": "nurse12345"})
            c.post("/api/users", headers=A, json={"username": "billing", "name": "Billing Desk", "role": "billing", "password": "billing12345"})
        continue
    d = c.post("/api/doctors", headers=A, json={"name": "Dr. Demo Physician", "department": "General Medicine", "qualification": "MBBS", "fee_new": 300, "fee_followup": 150}).json()
    for wd in range(0, 6):
        c.post(f"/api/doctors/{d['id']}/schedules", headers=A, json={"weekday": wd, "start_time": "10:00", "end_time": "13:00", "slot_minutes": 15})
    c.post("/api/tariff", headers=A, json={"code": "CON", "name": "OPD Consultation", "price": 300})
    c.post("/api/tariff", headers=A, json={"code": "FUP", "name": "Follow-up Consultation", "price": 150})
    c.post("/api/users", headers=A, json={"username": "reception", "name": "Front Desk", "role": "receptionist", "password": "recep12345"})
    c.post("/api/users", headers=A, json={"username": "doctor", "name": "Dr. Demo Physician", "role": "doctor", "password": "doctor12345", "doctor_id": d["id"]})
    c.post("/api/patients", headers=A, json={"name": "Demo Patient", "mobile": "9999900000", "gender": "Male"})
    if plan == "advance":   # demo values only; the hospital sets its own rates, taxes and packages
        c.post("/api/wards", headers=A, json={"name": "General Ward", "daily_rate": 1000, "beds": ["G1", "G2", "G3", "G4"]})
        c.post("/api/wards", headers=A, json={"name": "Private Room", "daily_rate": 3000, "beds": ["P1", "P2"]})
        c.post("/api/packages", headers=A, json={"name": "Demo Minor Procedure Package", "price": 15000, "includes_room": False})
        c.post("/api/users", headers=A, json={"username": "nurse", "name": "Ward Nurse", "role": "nurse", "password": "nurse12345"})
        c.post("/api/users", headers=A, json={"username": "billing", "name": "Billing Desk", "role": "billing", "password": "billing12345"})
        print("   IPD demo: nurse / nurse12345, billing / billing12345")
    print(f"{name}: site /site/{code} | staff /app/?t={code} | admin / admin12345 | reception / recep12345 | doctor / doctor12345")
print("Platform console: /app/platform.html | owner / ownerpass123  (CHANGE THESE)")
