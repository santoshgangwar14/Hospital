-- Phase 2: EMR, IPD (beds, admissions, nursing, MAR), IPD billing (deposits, GST, packages, discounts), discharge
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('admin','receptionist','doctor','billing','nurse'));
ALTER TABLE tariff_items ADD COLUMN IF NOT EXISTS gst_pct numeric(5,2) NOT NULL DEFAULT 0 CHECK (gst_pct BETWEEN 0 AND 100);
ALTER TABLE tariff_items ADD COLUMN IF NOT EXISTS sac_code text NOT NULL DEFAULT '';
ALTER TABLE patients ADD COLUMN IF NOT EXISTS blood_group text NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS wards (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  name text NOT NULL, category text NOT NULL DEFAULT 'general', daily_rate numeric(10,2) NOT NULL CHECK (daily_rate>=0),
  gst_pct numeric(5,2) NOT NULL DEFAULT 0 CHECK (gst_pct BETWEEN 0 AND 100), active boolean NOT NULL DEFAULT true, UNIQUE (tenant_id,name));
CREATE TABLE IF NOT EXISTS beds (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  ward_id uuid NOT NULL REFERENCES wards(id), bed_no text NOT NULL, active boolean NOT NULL DEFAULT true, UNIQUE (tenant_id,ward_id,bed_no));
CREATE TABLE IF NOT EXISTS packages (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  name text NOT NULL, price numeric(12,2) NOT NULL CHECK (price>=0), includes_room boolean NOT NULL DEFAULT false,
  gst_pct numeric(5,2) NOT NULL DEFAULT 0, active boolean NOT NULL DEFAULT true, UNIQUE (tenant_id,name));
CREATE TABLE IF NOT EXISTS admissions (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  adm_no text NOT NULL, patient_id uuid NOT NULL REFERENCES patients(id), doctor_id uuid NOT NULL REFERENCES doctors(id),
  bed_id uuid NOT NULL REFERENCES beds(id), package_id uuid REFERENCES packages(id), reason text NOT NULL DEFAULT '', diagnosis text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'admitted' CHECK (status IN ('admitted','discharge_initiated','discharged')),
  admitted_at timestamptz NOT NULL DEFAULT now(), discharge_ts timestamptz, discharged_at timestamptz,
  clr_nursing boolean NOT NULL DEFAULT false, clr_pharmacy boolean NOT NULL DEFAULT false, summary jsonb NOT NULL DEFAULT '{}',
  created_by uuid, UNIQUE (tenant_id,adm_no));
CREATE UNIQUE INDEX IF NOT EXISTS adm_bed_active ON admissions (tenant_id,bed_id) WHERE status<>'discharged';
CREATE UNIQUE INDEX IF NOT EXISTS adm_patient_active ON admissions (tenant_id,patient_id) WHERE status<>'discharged';
CREATE TABLE IF NOT EXISTS bed_stays (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  admission_id uuid NOT NULL REFERENCES admissions(id), bed_id uuid NOT NULL REFERENCES beds(id), ward_name text NOT NULL,
  daily_rate numeric(10,2) NOT NULL, gst_pct numeric(5,2) NOT NULL DEFAULT 0, from_at timestamptz NOT NULL DEFAULT now(), to_at timestamptz);
CREATE TABLE IF NOT EXISTS vitals (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  patient_id uuid NOT NULL REFERENCES patients(id), admission_id uuid REFERENCES admissions(id), appointment_id uuid REFERENCES appointments(id),
  temp numeric(4,1), pulse int, bp_sys int, bp_dia int, resp int, spo2 int, weight numeric(5,1), height numeric(5,1), notes text NOT NULL DEFAULT '',
  recorded_by uuid, recorded_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS encounters (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  patient_id uuid NOT NULL REFERENCES patients(id), doctor_id uuid NOT NULL REFERENCES doctors(id), appointment_id uuid REFERENCES appointments(id),
  admission_id uuid REFERENCES admissions(id), complaints text NOT NULL DEFAULT '', history text NOT NULL DEFAULT '', examination text NOT NULL DEFAULT '',
  diagnosis_code text NOT NULL DEFAULT '', diagnosis_text text NOT NULL DEFAULT '', plan text NOT NULL DEFAULT '', follow_up_date date,
  created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS allergies (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  patient_id uuid NOT NULL REFERENCES patients(id), allergen text NOT NULL, reaction text NOT NULL DEFAULT '', severity text NOT NULL DEFAULT '',
  noted_by uuid, noted_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS diagnosis_master (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  code text NOT NULL, name text NOT NULL, UNIQUE (tenant_id,code));
CREATE TABLE IF NOT EXISTS rx_templates (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  doctor_id uuid NOT NULL REFERENCES doctors(id), name text NOT NULL, items jsonb NOT NULL DEFAULT '[]', advice text NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS documents (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  patient_id uuid NOT NULL REFERENCES patients(id), admission_id uuid REFERENCES admissions(id),
  doc_type text NOT NULL CHECK (doc_type IN ('certificate','consent','other')), title text NOT NULL, body text NOT NULL, created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS nursing_notes (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  admission_id uuid NOT NULL REFERENCES admissions(id), note text NOT NULL, by_user uuid, by_name text, at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS med_orders (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  admission_id uuid NOT NULL REFERENCES admissions(id), drug text NOT NULL, dose text NOT NULL DEFAULT '', route text NOT NULL DEFAULT '',
  frequency text NOT NULL DEFAULT '', active boolean NOT NULL DEFAULT true, ordered_by uuid, ordered_at timestamptz NOT NULL DEFAULT now(), stopped_at timestamptz);
CREATE TABLE IF NOT EXISTS mar_entries (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  order_id uuid NOT NULL REFERENCES med_orders(id), admission_id uuid NOT NULL REFERENCES admissions(id),
  status text NOT NULL CHECK (status IN ('given','held','refused')), note text NOT NULL DEFAULT '', by_user uuid, by_name text, at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS diet_orders (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  admission_id uuid NOT NULL REFERENCES admissions(id), diet text NOT NULL, notes text NOT NULL DEFAULT '', active boolean NOT NULL DEFAULT true,
  ordered_by uuid, at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS ipd_charges (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  admission_id uuid NOT NULL REFERENCES admissions(id), description text NOT NULL, qty int NOT NULL CHECK (qty>0), price numeric(10,2) NOT NULL CHECK (price>=0),
  gst_pct numeric(5,2) NOT NULL DEFAULT 0, sac_code text NOT NULL DEFAULT '', in_package boolean NOT NULL DEFAULT false,
  cancelled boolean NOT NULL DEFAULT false, added_by uuid, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS ipd_payments (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  admission_id uuid NOT NULL REFERENCES admissions(id), kind text NOT NULL CHECK (kind IN ('advance','payment','refund')),
  mode text NOT NULL CHECK (mode IN ('cash','upi','card')), amount numeric(12,2) NOT NULL CHECK (amount>0), ref text NOT NULL DEFAULT '',
  receipt_no text NOT NULL, by_user uuid, at timestamptz NOT NULL DEFAULT now(), UNIQUE (tenant_id,receipt_no));
CREATE TABLE IF NOT EXISTS ipd_bills (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  admission_id uuid NOT NULL REFERENCES admissions(id), bill_no text NOT NULL, kind text NOT NULL CHECK (kind IN ('interim','final')),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','void')), lines jsonb NOT NULL, subtotal numeric(12,2) NOT NULL,
  discount numeric(12,2) NOT NULL DEFAULT 0, taxable numeric(12,2) NOT NULL, cgst numeric(12,2) NOT NULL, sgst numeric(12,2) NOT NULL, total numeric(12,2) NOT NULL,
  created_by uuid, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (tenant_id,bill_no));
CREATE UNIQUE INDEX IF NOT EXISTS one_active_final ON ipd_bills (tenant_id,admission_id) WHERE kind='final' AND status='active';
CREATE TABLE IF NOT EXISTS discount_requests (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  admission_id uuid NOT NULL REFERENCES admissions(id), amount numeric(12,2) NOT NULL CHECK (amount>0), reason text NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','used')), requested_by uuid, decided_by uuid,
  at timestamptz NOT NULL DEFAULT now());

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['wards','beds','packages','admissions','bed_stays','vitals','encounters','allergies','diagnosis_master','rx_templates','documents',
     'nursing_notes','med_orders','mar_entries','diet_orders','ipd_charges','ipd_payments','ipd_bills','discount_requests'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I USING (tenant_id = nullif(current_setting('app.tenant_id', true),'')::uuid)
                      WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true),'')::uuid)$p$, t);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I (tenant_id)', t||'_tenant_idx', t);
    EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON %I TO hms_app', t);
  END LOOP; END $$;
GRANT ALL ON ALL TABLES IN SCHEMA public TO hms_platform;
