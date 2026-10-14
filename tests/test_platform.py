import os, sys, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from datetime import date, timedelta
import psycopg2, pytest
from fastapi.testclient import TestClient
from hms.app import app
from hms.db import platform_tx, tenant_tx
from hms.security import hash_pw

TABLES = ["users", "doctors", "doctor_schedules", "patients", "appointments", "tariff_items", "bills", "bill_items",
          "payments", "prescriptions", "counters", "audit_log", "message_queue"]
client = TestClient(app)


@pytest.fixture(scope="session")
def plat():
    with platform_tx() as c:
        c.execute("insert into platform_users(username,name,password_hash) values('testowner','Owner',%s) on conflict (username) do update set password_hash=excluded.password_hash", (hash_pw("ownerpass1"),))
    r = client.post("/api/platform/login", json={"username": "testowner", "password": "ownerpass1"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


def make(plat, name, color, uh, bl, plan="basic"):
    code = "t" + uuid.uuid4().hex[:8]
    r = client.post("/api/platform/tenants", headers=plat, json={"name": name, "code": code, "plan": plan, "primary": color,
                    "uhid_prefix": uh, "bill_prefix": bl, "admin_password": "adminpass1"})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    h = {"X-Tenant": code}
    tok = client.post("/api/auth/login", headers=h, json={"username": "admin", "password": "adminpass1"}).json()["token"]
    return {"code": code, "id": str(tid), "h": {**h, "Authorization": "Bearer " + tok}, "name": name}


def seed(t):
    h = t["h"]
    d = client.post("/api/doctors", headers=h, json={"name": "Dr Test " + t["code"], "department": "General", "fee_new": 300}).json()
    day = date.today() + timedelta(days=1)
    client.post(f"/api/doctors/{d['id']}/schedules", headers=h, json={"weekday": day.weekday(), "start_time": "10:00", "end_time": "12:00", "slot_minutes": 30})
    p = client.post("/api/patients", headers=h, json={"name": "Ram Kumar", "mobile": "9876543210"}).json()
    tf = client.post("/api/tariff", headers=h, json={"code": "CON", "name": "Consultation", "price": 300}).json()
    a = client.post("/api/appointments", headers=h, json={"patient_id": p["id"], "doctor_id": d["id"], "appt_date": str(day), "appt_time": "10:00"})
    assert a.status_code == 200, a.text
    b = client.post("/api/bills", headers=h, json={"patient_id": p["id"], "items": [{"tariff_id": tf["id"], "qty": 1}], "payments": [{"mode": "cash", "amount": 300}]})
    assert b.status_code == 200, b.text
    return {"doc": d, "pat": p, "tf": tf, "appt": a.json(), "bill": b.json(), "day": day}


@pytest.fixture(scope="module")
def two(plat):
    A = make(plat, "Alpha Care Hospital", "#aa1122", "AL", "AB")
    B = make(plat, "Beta Life Hospital", "#118833", "BE", "BB")
    return A, B, seed(A), seed(B)


def test_database_level_isolation_every_table(two):
    A, B, sa, sb = two
    for t in TABLES:
        with tenant_tx(B["id"]) as c:        # B's context must never see A's rows
            c.execute(f"select count(*) n from {t} where tenant_id=%s", (A["id"],))
            assert c.fetchone()["n"] == 0, t
        with tenant_tx(None) as c:           # no context means no rows at all
            c.execute(f"select count(*) n from {t}")
            assert c.fetchone()["n"] == 0, t
    with pytest.raises(psycopg2.Error):      # B's context cannot write rows for A
        with tenant_tx(B["id"]) as c:
            c.execute("insert into doctors(tenant_id,name) values(%s,'x')", (A["id"],))
    with pytest.raises(psycopg2.Error):      # app role cannot read platform tables
        with tenant_tx(A["id"]) as c:
            c.execute("select * from tenants")


def test_api_isolation(two):
    A, B, sa, sb = two
    assert client.get(f"/api/patients/{sa['pat']['id']}", headers=B["h"]).status_code == 404
    assert client.get(f"/api/bills/{sa['bill']['id']}", headers=B["h"]).status_code == 404
    ids = [p["id"] for p in client.get("/api/patients", headers=B["h"]).json()]
    assert sa["pat"]["id"] not in ids and sb["pat"]["id"] in ids
    mixed = {"X-Tenant": B["code"], "Authorization": A["h"]["Authorization"]}    # A's token on B's hospital
    assert client.get("/api/patients", headers=mixed).status_code == 401
    assert client.post("/api/bills", headers=B["h"], json={"patient_id": sa["pat"]["id"], "items": [{"tariff_id": sb["tf"]["id"]}]}).status_code == 404


def test_numbering_is_per_tenant(two):
    A, B, sa, sb = two
    assert sa["pat"]["uhid"] == "AL000001" and sb["pat"]["uhid"] == "BE000001"
    assert sa["bill"]["bill_no"].startswith("AB/") and sb["bill"]["bill_no"].startswith("BB/")
    assert sa["bill"]["bill_no"].endswith("/00001") and sb["bill"]["bill_no"].endswith("/00001")
    dup = client.post("/api/patients", headers=A["h"], json={"name": "ram kumar", "mobile": "9876543210"})
    assert dup.status_code == 409
    assert client.post("/api/patients", headers=A["h"], json={"name": "ram kumar", "mobile": "9876543210", "force": True}).json()["uhid"] == "AL000002"


def test_branding_is_per_tenant(two):
    A, B, *_ = two
    sa = client.get(f"/site/{A['code']}").text
    sb = client.get(f"/site/{B['code']}").text
    assert "Alpha Care Hospital" in sa and "#aa1122" in sa and "Beta" not in sa
    assert "Beta Life Hospital" in sb and "#118833" in sb and "#aa1122" not in sb
    assert client.get("/api/config", headers={"X-Tenant": A["code"]}).json()["branding"]["primary"] == "#aa1122"


def test_plan_flags(plat, two):
    A, B, *_ = two
    assert client.get("/api/config", headers={"X-Tenant": A["code"]}).json()["plan"] == "basic"
    client.patch(f"/api/platform/tenants/{A['id']}", headers=plat, json={"modules_remove": ["opd_billing"]})
    assert client.get("/api/bills", headers=A["h"]).status_code == 403
    assert client.get("/api/bills", headers=B["h"]).status_code == 200      # other hospital unaffected
    client.patch(f"/api/platform/tenants/{A['id']}", headers=plat, json={"modules_remove": []})
    assert client.get("/api/bills", headers=A["h"]).status_code == 200
    client.patch(f"/api/platform/tenants/{A['id']}", headers=plat, json={"status": "suspended"})
    assert client.get("/api/patients", headers=A["h"]).status_code == 403
    client.patch(f"/api/platform/tenants/{A['id']}", headers=plat, json={"status": "active"})
    assert client.get("/api/patients", headers=A["h"]).status_code == 200


def test_billing_rules(two):
    A, B, sa, sb = two
    h = A["h"]
    r = client.post("/api/users", headers=h, json={"username": "recep1", "name": "Rec", "role": "receptionist", "password": "recpass123"})
    assert r.status_code == 200
    tok = client.post("/api/auth/login", headers={"X-Tenant": A["code"]}, json={"username": "recep1", "password": "recpass123"}).json()["token"]
    rh = {"X-Tenant": A["code"], "Authorization": "Bearer " + tok}
    body = {"patient_id": sa["pat"]["id"], "items": [{"tariff_id": sa["tf"]["id"], "price": 1}], "discount": 100, "payments": [{"mode": "upi", "amount": 200}]}
    assert client.post("/api/bills", headers=rh, json=body).status_code == 403            # discount above limit needs admin
    ok = client.post("/api/bills", headers=h, json=body)
    assert ok.status_code == 200 and float(ok.json()["total"]) == 200.0                    # client price 1 ignored, server price 300 used
    assert client.post("/api/bills", headers=rh, json={**body, "discount": 0, "payments": [{"mode": "cash", "amount": 999}]}).status_code == 422
    assert client.post("/api/bills", headers=rh, json={"patient_id": sa["pat"]["id"], "items": [{"description": "free", "price": 5}]}).status_code == 422
    rep = client.get("/api/reports/collection", headers=h).json()
    assert float(rep["total"]) == 500.0                                                      # 300 cash + 200 upi
    assert client.get("/api/users", headers=rh).status_code == 403


def test_audit_log_is_append_only(two):
    A, B, *_ = two
    acts = [(a["entity"], a["action"]) for a in client.get("/api/audit", headers=A["h"]).json()]
    assert ("patient", "create") in acts and ("bill", "create") in acts
    with pytest.raises(psycopg2.Error):
        with tenant_tx(A["id"]) as c:
            c.execute("delete from audit_log")
    with pytest.raises(psycopg2.Error):
        with tenant_tx(A["id"]) as c:
            c.execute("update audit_log set action='x'")


def test_appointments_tokens_public_booking_and_messages(two):
    A, B, sa, sb = two
    h = A["h"]
    dbl = client.post("/api/appointments", headers=h, json={"patient_id": sa["pat"]["id"], "doctor_id": sa["doc"]["id"], "appt_date": str(sa["day"]), "appt_time": "10:00"})
    assert dbl.status_code == 409
    slots = client.get(f"/api/doctors/{sa['doc']['id']}/slots", params={"day": str(sa["day"])}, headers=h).json()["slots"]
    assert "10:00" not in slots and "10:30" in slots
    w = client.post("/api/appointments", headers=h, json={"patient_id": sa["pat"]["id"], "doctor_id": sa["doc"]["id"], "appt_date": str(sa["day"])})
    assert w.json()["status"] == "waiting" and w.json()["token_no"] == 2
    pub = client.post(f"/api/public/{A['code']}/appointments", json={"name": "Sita Devi", "mobile": "9000000001", "doctor_id": sa["doc"]["id"], "appt_date": str(sa["day"]), "appt_time": "11:00"})
    assert pub.status_code == 200
    assert client.post(f"/api/public/{A['code']}/appointments", json={"name": "Other", "mobile": "9000000002", "doctor_id": sb["doc"]["id"], "appt_date": str(sb["day"])}).status_code == 404
    sent = client.post("/api/messages/dispatch", headers=h).json()["sent"]
    assert sent >= 3


def test_doctor_role_and_prescription(two):
    A, B, sa, sb = two
    h = A["h"]
    client.post("/api/users", headers=h, json={"username": "drtest", "name": "Dr", "role": "doctor", "password": "docpass123", "doctor_id": sa["doc"]["id"]})
    tok = client.post("/api/auth/login", headers={"X-Tenant": A["code"]}, json={"username": "drtest", "password": "docpass123"}).json()["token"]
    dh = {"X-Tenant": A["code"], "Authorization": "Bearer " + tok}
    r = client.post("/api/prescriptions", headers=dh, json={"patient_id": sa["pat"]["id"], "diagnosis": "x", "items": [{"name": "Tab A", "dose": "1-0-1"}]})
    assert r.status_code == 200
    assert client.get(f"/api/prescriptions/{r.json()['id']}", headers=dh).json()["doctor_name"].startswith("Dr Test")
    assert client.post("/api/prescriptions", headers=h, json={"patient_id": sa["pat"]["id"]}).status_code == 403   # admin has no doctor profile
    assert client.post("/api/doctors", headers=dh, json={"name": "Nope"}).status_code == 403
    assert client.get(f"/api/prescriptions/{r.json()['id']}", headers=B["h"]).status_code == 404
