import os, sys, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__))); sys.path.insert(0, os.path.dirname(__file__))
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import psycopg2, pytest
from test_platform import client, make, plat, TABLES  # noqa: F401
from hms.db import platform_tx, tenant_tx

P2 = ["wards", "beds", "packages", "admissions", "bed_stays", "vitals", "encounters", "allergies", "diagnosis_master", "rx_templates",
      "documents", "nursing_notes", "med_orders", "mar_entries", "diet_orders", "ipd_charges", "ipd_payments", "ipd_bills", "discount_requests"]


def login(t, user, pw):
    tok = client.post("/api/auth/login", headers={"X-Tenant": t["code"]}, json={"username": user, "password": pw}).json()["token"]
    return {"X-Tenant": t["code"], "Authorization": "Bearer " + tok}


@pytest.fixture(scope="module")
def hosp(plat):
    A = make(plat, "Alpha Advance Hospital", "#334455", "AA", "AB", plan="advance")
    B = make(plat, "Beta Advance Hospital", "#445566", "BA", "BB", plan="advance")
    out = []
    for T in (A, B):
        h = T["h"]
        d = client.post("/api/doctors", headers=h, json={"name": "Dr IPD " + T["code"], "department": "Medicine"}).json()
        ward = client.post("/api/wards", headers=h, json={"name": "General", "daily_rate": 1000, "gst_pct": 0, "beds": ["G1", "G2"]}).json()
        pr = client.post("/api/wards", headers=h, json={"name": "Private", "daily_rate": 6000, "gst_pct": 5, "beds": ["P1"]}).json()
        board = client.get("/api/beds/board", headers=h).json()
        beds = {b["bed_no"]: b["bed_id"] for w in board for b in w["beds"]}
        p = client.post("/api/patients", headers=h, json={"name": "Ipd Patient", "mobile": "9111111111"}).json()
        tf = client.post("/api/tariff", headers=h, json={"code": "XR", "name": "X-ray", "price": 500, "gst_pct": 0}).json()
        tf2 = client.post("/api/tariff", headers=h, json={"code": "PRC", "name": "Procedure", "price": 2000, "gst_pct": 12, "sac_code": "9993"}).json()
        client.post("/api/users", headers=h, json={"username": "nurse1", "name": "Nurse", "role": "nurse", "password": "nursepass1"})
        client.post("/api/users", headers=h, json={"username": "doc1", "name": "Doc", "role": "doctor", "password": "docpass123", "doctor_id": d["id"]})
        client.post("/api/users", headers=h, json={"username": "bill1", "name": "Bill", "role": "billing", "password": "billpass12"})
        client.post("/api/users", headers=h, json={"username": "rec1", "name": "Rec", "role": "receptionist", "password": "recpass123"})
        out.append({**T, "doc": d, "beds": beds, "pat": p, "tf": tf, "tf2": tf2,
                    "nurse": login(T, "nurse1", "nursepass1"), "doctor": login(T, "doc1", "docpass123"), "bill": login(T, "bill1", "billpass12"), "rec": login(T, "rec1", "recpass123")})
    return out


def backdate(adm_id, days):
    """Move admission and its first bed stay back in time so room charges span several days."""
    with platform_tx() as c:
        c.execute("update admissions set admitted_at=now() - (%s || ' days')::interval where id=%s", (days, adm_id))
        c.execute("update bed_stays set from_at=now() - (%s || ' days')::interval where admission_id=%s", (days, adm_id))


def test_new_tables_isolated_and_flag_gated(plat, hosp):
    A, B = hosp
    adm = client.post("/api/admissions", headers=A["rec"], json={"patient_id": A["pat"]["id"], "doctor_id": A["doc"]["id"], "bed_id": A["beds"]["G1"]}).json()
    client.post(f"/api/admissions/{adm['id']}/nursing-notes", headers=A["nurse"], json={"note": "Patient comfortable"})
    for t in P2:
        with tenant_tx(B["id"]) as c:
            c.execute(f"select count(*) n from {t} where tenant_id=%s", (A["id"],)); assert c.fetchone()["n"] == 0, t
        with tenant_tx(None) as c:
            c.execute(f"select count(*) n from {t}"); assert c.fetchone()["n"] == 0, t
    assert client.get(f"/api/admissions/{adm['id']}", headers=B["h"]).status_code == 404
    basic = make(plat, "Basic Only Hospital", "#111111", "BO", "BB")
    for path in ("/api/beds/board", "/api/admissions", "/api/packages"):
        assert client.get(path, headers=basic["h"]).status_code == 403, path
    assert B["beds"]["G1"] != A["beds"]["G1"]
    assert client.post("/api/admissions", headers=B["rec"], json={"patient_id": B["pat"]["id"], "doctor_id": B["doc"]["id"], "bed_id": A["beds"]["G2"]}).status_code == 404   # A's bed unusable from B


def test_bed_rules_transfer_and_room_charges(hosp):
    A, B = hosp
    h = A["h"]
    p2 = client.post("/api/patients", headers=h, json={"name": "Second Patient", "mobile": "9222222222"}).json()
    dup_bed = client.post("/api/admissions", headers=h, json={"patient_id": p2["id"], "doctor_id": A["doc"]["id"], "bed_id": A["beds"]["G1"]})
    assert dup_bed.status_code == 409                                                       # bed already occupied by the first test's patient
    again = client.post("/api/admissions", headers=h, json={"patient_id": A["pat"]["id"], "doctor_id": A["doc"]["id"], "bed_id": A["beds"]["G2"]})
    assert again.status_code == 409                                                         # same patient already admitted
    adm = client.post("/api/admissions", headers=h, json={"patient_id": p2["id"], "doctor_id": A["doc"]["id"], "bed_id": A["beds"]["G2"]}).json()
    backdate(adm["id"], 3)
    acc = client.get(f"/api/admissions/{adm['id']}/account", headers=h).json()
    room = [l for l in acc["lines"] if l["kind"] == "room"]
    assert len(room) == 1 and room[0]["qty"] == 4 and float(room[0]["amount"]) == 4000.0    # 3 days ago .. today = 4 calendar dates
    assert client.post(f"/api/admissions/{adm['id']}/transfer", headers=h, json={"bed_id": A["beds"]["P1"]}).status_code == 200
    acc = client.get(f"/api/admissions/{adm['id']}/account", headers=h).json()
    room = {l["description"]: l for l in acc["lines"] if l["kind"] == "room"}
    assert room["Bed charges: General"]["qty"] == 3 and room["Bed charges: Private"]["qty"] == 1   # transfer day is charged at the new bed's rate
    assert float(acc["total"]) == 3 * 1000 + 6000 + 300.0                                       # private bed 5% GST on 6000


def test_services_gst_discount_approval_and_final_bill(hosp):
    A, B = hosp
    h = A["h"]
    p3 = client.post("/api/patients", headers=h, json={"name": "Third Patient", "mobile": "9333333333"}).json()
    adm = client.post("/api/admissions", headers=h, json={"patient_id": p3["id"], "doctor_id": A["doc"]["id"], "bed_id": A["beds"]["P1"]})
    if adm.status_code == 409:   # P1 taken by the previous test: free it by discharging nothing, use a new ward bed instead
        client.post(f"/api/wards", headers=h, json={"name": "Extra", "daily_rate": 1000, "beds": ["E1"]})
        board = client.get("/api/beds/board", headers=h).json()
        bed = [b["bed_id"] for w in board for b in w["beds"] if b["bed_no"] == "E1"][0]
        adm = client.post("/api/admissions", headers=h, json={"patient_id": p3["id"], "doctor_id": A["doc"]["id"], "bed_id": bed})
    adm = adm.json()
    aid = adm["id"]
    assert client.post(f"/api/admissions/{aid}/charges", headers=A["bill"], json={"tariff_id": A["tf2"]["id"], "qty": 1, "price": 1}).status_code == 200
    assert client.post(f"/api/admissions/{aid}/charges", headers=A["bill"], json={"description": "free text", "price": 10}).status_code == 422
    acc = client.get(f"/api/admissions/{aid}/account", headers=h).json()
    svc = [l for l in acc["lines"] if l["kind"] == "service"][0]
    assert float(svc["price"]) == 2000.0 and float(svc["tax"]) == 240.0                      # server price, 12% GST
    assert float(acc["cgst"]) == float(acc["sgst"]) and abs(float(acc["total"]) - float(acc["taxable"]) - 2 * float(acc["cgst"])) < 0.011
    # discount above the 10% limit: billing user needs an approved request
    assert client.post(f"/api/admissions/{aid}/bills", headers=A["bill"], json={"kind": "interim", "discount": 1000}).status_code == 403
    rq = client.post(f"/api/admissions/{aid}/discount-requests", headers=A["bill"], json={"amount": 1000, "reason": "Staff relative"}).json()
    assert client.post(f"/api/discount-requests/{rq['id']}/decide", headers=A["bill"], json={"approve": True}).status_code == 403   # only admin approves
    assert client.post(f"/api/discount-requests/{rq['id']}/decide", headers=h, json={"approve": True}).status_code == 200
    ok = client.post(f"/api/admissions/{aid}/bills", headers=A["bill"], json={"kind": "interim", "discount": 1000})
    assert ok.status_code == 200
    assert client.post(f"/api/admissions/{aid}/bills", headers=A["bill"], json={"kind": "interim", "discount": 1000}).status_code == 403   # approval is single-use
    fin = client.post(f"/api/admissions/{aid}/bills", headers=A["bill"], json={"kind": "final"})
    assert fin.status_code == 200 and fin.json()["bill_no"].startswith("IP/")
    assert client.post(f"/api/admissions/{aid}/charges", headers=A["bill"], json={"tariff_id": A["tf"]["id"]}).status_code == 409    # locked after final bill
    assert client.post(f"/api/admissions/{aid}/bills", headers=A["bill"], json={"kind": "final"}).status_code == 409
    inv = client.get(f"/api/ipd-bills/{fin.json()['id']}", headers=h).json()
    assert inv["patient_name"] == "Third Patient" and "gst_note" in inv



def test_deposits_refund_and_discharge_gating(hosp):
    A, B = hosp
    h = A["h"]
    p4 = client.post("/api/patients", headers=h, json={"name": "Fourth Patient", "mobile": "9444444444"}).json()
    client.post("/api/wards", headers=h, json={"name": "Day", "daily_rate": 1000, "beds": ["D1"]})
    bed = [b["bed_id"] for w in client.get("/api/beds/board", headers=h).json() for b in w["beds"] if b["bed_no"] == "D1"][0]
    aid = client.post("/api/admissions", headers=h, json={"patient_id": p4["id"], "doctor_id": A["doc"]["id"], "bed_id": bed}).json()["id"]
    pay = lambda k, amt: client.post(f"/api/admissions/{aid}/payments", headers=A["bill"], json={"kind": k, "mode": "cash", "amount": amt})
    assert pay("advance", 5000).status_code == 200
    assert pay("refund", 100).status_code == 422                                           # no refund before final bill
    # discharge blocked until everything is cleared
    assert client.post(f"/api/admissions/{aid}/discharge/initiate", headers=A["doctor"]).status_code == 200
    blocked = client.post(f"/api/admissions/{aid}/discharge/complete", headers=A["doctor"])
    assert blocked.status_code == 409 and "summary" in blocked.json()["detail"] and "billing" in blocked.json()["detail"]
    assert client.post(f"/api/admissions/{aid}/discharge/summary", headers=A["doctor"], json={"text": "Recovered well, advised rest"}).status_code == 200
    assert client.post(f"/api/admissions/{aid}/clearance", params={"dept": "nursing"}, headers=A["nurse"]).status_code == 200
    assert client.post(f"/api/admissions/{aid}/clearance", params={"dept": "pharmacy"}, headers=A["nurse"]).status_code == 403
    assert client.post(f"/api/admissions/{aid}/clearance", params={"dept": "pharmacy"}, headers=h).status_code == 200
    fin = client.post(f"/api/admissions/{aid}/bills", headers=A["bill"], json={"kind": "final"}).json()
    st = client.get(f"/api/admissions/{aid}/discharge/status", headers=h).json()
    assert st["billing"] is False and float(st["balance"]) == float(fin["total"]) - 5000     # excess advance must be refunded first
    assert client.post(f"/api/admissions/{aid}/discharge/complete", headers=A["doctor"]).status_code == 409
    over = 5000 - float(fin["total"])
    assert pay("refund", over + 1).status_code == 422                                      # cannot refund more than the excess
    assert pay("refund", over).status_code == 200
    done = client.post(f"/api/admissions/{aid}/discharge/complete", headers=A["doctor"])
    assert done.status_code == 200
    board = {b["bed_no"]: b["state"] for w in client.get("/api/beds/board", headers=h).json() for b in w["beds"]}
    assert board["D1"] == "vacant"                                                          # bed freed
    assert client.post(f"/api/admissions/{aid}/nursing-notes", headers=A["nurse"], json={"note": "late note"}).status_code == 409   # closed record
    csvtxt = client.get("/api/reports/vouchers.csv", params={"start": str(date.today() - timedelta(days=1)), "end": str(date.today() + timedelta(days=1))}, headers=A["bill"]).text
    assert fin["bill_no"] in csvtxt and "Patient receivable" in csvtxt


def test_roles_emr_mar_and_audit(hosp):
    A, B = hosp
    h = A["h"]
    p5 = client.post("/api/patients", headers=h, json={"name": "Fifth Patient", "mobile": "9555555555"}).json()
    client.post("/api/wards", headers=h, json={"name": "Obs", "daily_rate": 500, "beds": ["O1"]})
    bed = [b["bed_id"] for w in client.get("/api/beds/board", headers=h).json() for b in w["beds"] if b["bed_no"] == "O1"][0]
    aid = client.post("/api/admissions", headers=A["rec"], json={"patient_id": p5["id"], "doctor_id": A["doc"]["id"], "bed_id": bed}).json()["id"]
    assert client.post(f"/api/admissions/{aid}/med-orders", headers=A["nurse"], json={"drug": "Test drug", "dose": "1 unit"}).status_code == 403   # nurses cannot prescribe
    o = client.post(f"/api/admissions/{aid}/med-orders", headers=A["doctor"], json={"drug": "Test drug", "dose": "1 unit", "frequency": "BD"})
    assert o.status_code == 200
    g = client.post(f"/api/med-orders/{o.json()['id']}/give", headers=A["nurse"], json={"status": "given"})
    assert g.status_code == 200
    assert client.post(f"/api/med-orders/{o.json()['id']}/give", headers=B["nurse"], json={"status": "given"}).status_code == 404   # other hospital
    client.post(f"/api/med-orders/{o.json()['id']}/stop", headers=A["doctor"])
    assert client.post(f"/api/med-orders/{o.json()['id']}/give", headers=A["nurse"], json={"status": "given"}).status_code == 409
    assert client.post("/api/vitals", headers=A["nurse"], json={"patient_id": p5["id"], "admission_id": aid, "pulse": 80, "bp_sys": 120, "bp_dia": 80, "spo2": 98}).status_code == 200
    assert client.post("/api/vitals", headers=A["nurse"], json={"patient_id": p5["id"], "spo2": 150}).status_code == 422             # impossible value rejected
    assert client.post("/api/encounters", headers=A["nurse"], json={"patient_id": p5["id"], "complaints": "x"}).status_code == 403
    assert client.post("/api/encounters", headers=A["doctor"], json={"patient_id": p5["id"], "admission_id": aid, "complaints": "test", "diagnosis_code": "X1", "diagnosis_text": "Test dx"}).status_code == 200
    assert client.post("/api/diagnoses/import", headers=h, json=[{"code": "t1", "name": "Test diagnosis one"}]).json()["imported"] == 1
    assert client.get("/api/diagnoses/search", params={"q": "diagnosis"}, headers=A["doctor"]).json()[0]["code"] == "T1"
    assert client.get("/api/diagnoses/search", params={"q": "diagnosis"}, headers=B["doctor"]).json() == []                         # master list is per hospital
    client.post(f"/api/patients/{p5['id']}/allergies", headers=A["doctor"], json={"allergen": "Test allergen", "severity": "mild"})
    tl = client.get(f"/api/patients/{p5['id']}/timeline", headers=A["doctor"]).json()
    kinds = {e["type"] for e in tl["events"]}
    assert {"encounter", "admission", "vitals"} <= kinds and tl["allergies"][0]["allergen"] == "Test allergen"
    assert client.get("/api/wards", headers=A["h"]).status_code in (404, 405)   # wards are only listed through the bed board
    acts = {(a["entity"], a["action"]) for a in client.get("/api/audit", headers=h, params={"limit": 500}).json()}
    assert {("admission", "create"), ("med_order", "mar"), ("ipd_bill", "create"), ("ipd_payment", "create"), ("discount", "decide")} <= acts
    with pytest.raises(psycopg2.Error):
        with tenant_tx(A["id"]) as c:
            c.execute("delete from audit_log")
