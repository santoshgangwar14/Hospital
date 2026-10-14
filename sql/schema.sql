-- White-label Hospital Platform: schema v1 (Phase 1: platform core + Basic plan)
-- Isolation is enforced by PostgreSQL row-level security. The app role has NO bypass.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='hms_app') THEN
    CREATE ROLE hms_app LOGIN PASSWORD 'hms_app' NOSUPERUSER NOBYPASSRLS; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='hms_platform') THEN
    CREATE ROLE hms_platform LOGIN PASSWORD 'hms_platform' NOSUPERUSER BYPASSRLS; END IF;
END $$;

CREATE TABLE IF NOT EXISTS plans (code text PRIMARY KEY, name text NOT NULL, modules text[] NOT NULL);
CREATE TABLE IF NOT EXISTS tenants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text UNIQUE NOT NULL CHECK (code ~ '^[a-z0-9-]{2,30}$'),
  name text NOT NULL, domain text UNIQUE,
  plan text NOT NULL REFERENCES plans(code),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','trial','suspended')),
  modules_add text[] NOT NULL DEFAULT '{}', modules_remove text[] NOT NULL DEFAULT '{}',
  branding jsonb NOT NULL DEFAULT '{}', settings jsonb NOT NULL DEFAULT '{}',
  trial_ends date, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS platform_users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), username text UNIQUE NOT NULL, name text NOT NULL,
  password_hash text NOT NULL, role text NOT NULL DEFAULT 'superadmin' CHECK (role IN ('superadmin','partner','support')),
  partner_tenant_ids uuid[] NOT NULL DEFAULT '{}', active boolean NOT NULL DEFAULT true);

CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  username text NOT NULL, name text NOT NULL, role text NOT NULL CHECK (role IN ('admin','receptionist','doctor','billing')),
  password_hash text NOT NULL, doctor_id uuid, active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (tenant_id, username));
CREATE TABLE IF NOT EXISTS doctors (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  name text NOT NULL, department text NOT NULL DEFAULT '', qualification text NOT NULL DEFAULT '', bio text NOT NULL DEFAULT '',
  photo_url text NOT NULL DEFAULT '', fee_new numeric(10,2) NOT NULL DEFAULT 0 CHECK (fee_new>=0),
  fee_followup numeric(10,2) NOT NULL DEFAULT 0 CHECK (fee_followup>=0), active boolean NOT NULL DEFAULT true);
CREATE TABLE IF NOT EXISTS doctor_schedules (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  doctor_id uuid NOT NULL REFERENCES doctors(id), weekday int NOT NULL CHECK (weekday BETWEEN 0 AND 6),
  start_time time NOT NULL, end_time time NOT NULL CHECK (end_time>start_time), slot_minutes int NOT NULL DEFAULT 15 CHECK (slot_minutes BETWEEN 5 AND 120));
CREATE TABLE IF NOT EXISTS patients (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  uhid text NOT NULL, name text NOT NULL, mobile text NOT NULL, gender text NOT NULL DEFAULT '', dob date, address text NOT NULL DEFAULT '',
  created_by uuid, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (tenant_id, uhid));
CREATE INDEX IF NOT EXISTS patients_mobile ON patients (tenant_id, mobile);
CREATE TABLE IF NOT EXISTS appointments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  patient_id uuid REFERENCES patients(id), guest_name text, guest_mobile text,
  doctor_id uuid NOT NULL REFERENCES doctors(id), appt_date date NOT NULL, appt_time time, token_no int,
  status text NOT NULL DEFAULT 'booked' CHECK (status IN ('requested','booked','waiting','in_consultation','done','cancelled','no_show')),
  source text NOT NULL DEFAULT 'reception' CHECK (source IN ('online','reception','walkin')), notes text NOT NULL DEFAULT '',
  created_by uuid, created_at timestamptz NOT NULL DEFAULT now());
CREATE UNIQUE INDEX IF NOT EXISTS appt_slot_once ON appointments (tenant_id, doctor_id, appt_date, appt_time)
  WHERE appt_time IS NOT NULL AND status NOT IN ('cancelled','no_show');
CREATE TABLE IF NOT EXISTS tariff_items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  code text NOT NULL, name text NOT NULL, category text NOT NULL DEFAULT 'OPD', price numeric(10,2) NOT NULL CHECK (price>=0),
  active boolean NOT NULL DEFAULT true, UNIQUE (tenant_id, code));
CREATE TABLE IF NOT EXISTS bills (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  bill_no text NOT NULL, patient_id uuid NOT NULL REFERENCES patients(id), appointment_id uuid REFERENCES appointments(id),
  subtotal numeric(12,2) NOT NULL, discount numeric(12,2) NOT NULL DEFAULT 0, total numeric(12,2) NOT NULL, paid numeric(12,2) NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'paid' CHECK (status IN ('paid','due','cancelled')), created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (tenant_id, bill_no));
CREATE TABLE IF NOT EXISTS bill_items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  bill_id uuid NOT NULL REFERENCES bills(id), description text NOT NULL, qty int NOT NULL CHECK (qty>0),
  price numeric(10,2) NOT NULL CHECK (price>=0), amount numeric(12,2) NOT NULL);
CREATE TABLE IF NOT EXISTS payments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  bill_id uuid NOT NULL REFERENCES bills(id), mode text NOT NULL CHECK (mode IN ('cash','upi','card')),
  amount numeric(12,2) NOT NULL CHECK (amount>0), ref text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS prescriptions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  patient_id uuid NOT NULL REFERENCES patients(id), doctor_id uuid NOT NULL REFERENCES doctors(id), appointment_id uuid REFERENCES appointments(id),
  complaints text NOT NULL DEFAULT '', diagnosis text NOT NULL DEFAULT '', advice text NOT NULL DEFAULT '', items jsonb NOT NULL DEFAULT '[]',
  follow_up_date date, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS counters (
  tenant_id uuid NOT NULL REFERENCES tenants(id), key text NOT NULL, value int NOT NULL DEFAULT 0, PRIMARY KEY (tenant_id, key));
CREATE TABLE IF NOT EXISTS audit_log (
  id bigserial PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id), user_id uuid, username text, action text NOT NULL,
  entity text NOT NULL, entity_id text, old_value jsonb, new_value jsonb, at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS message_queue (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id),
  channel text NOT NULL DEFAULT 'sms', to_addr text NOT NULL, body text NOT NULL, send_after timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed')), attempts int NOT NULL DEFAULT 0, last_error text);

INSERT INTO plans (code,name,modules) VALUES
 ('basic','Basic: Digital Start', ARRAY['website','patients','appointments','opd_billing','prescriptions','reminders','dashboard','users','audit']),
 ('advance','Advance: Smart Hospital', ARRAY['website','patients','appointments','opd_billing','prescriptions','reminders','dashboard','users','audit',
   'emr','ipd','ipd_billing','pharmacy','lab','insurance_basic','patient_app_extras','queue_display','mis_reports']),
 ('pro','Pro: Full Digital Hospital', ARRAY['website','patients','appointments','opd_billing','prescriptions','reminders','dashboard','users','audit',
   'emr','ipd','ipd_billing','pharmacy','lab','insurance_basic','patient_app_extras','queue_display','mis_reports',
   'ot','icu','radiology','insurance_full','abdm','inventory','hr','accounts','ambulance','telemedicine','analytics_advanced','multi_branch','nabh'])
ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, modules=EXCLUDED.modules;

-- Row-level security on every tenant table
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['users','doctors','doctor_schedules','patients','appointments','tariff_items','bills','bill_items',
                           'payments','prescriptions','counters','audit_log','message_queue'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I USING (tenant_id = nullif(current_setting('app.tenant_id', true),'')::uuid)
                      WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true),'')::uuid)$p$, t);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I (tenant_id)', t||'_tenant_idx', t);
  END LOOP; END $$;

-- Privileges: the app role cannot touch platform tables; audit_log is append-only
GRANT USAGE ON SCHEMA public TO hms_app, hms_platform;
GRANT SELECT,INSERT,UPDATE,DELETE ON users,doctors,doctor_schedules,patients,appointments,tariff_items,bills,bill_items,
      payments,prescriptions,counters,message_queue TO hms_app;
GRANT SELECT,INSERT ON audit_log TO hms_app;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO hms_app;
GRANT ALL ON ALL TABLES IN SCHEMA public TO hms_platform;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO hms_platform;
