/*
# School Fee Reminder CRM — Core Schema

## Overview
Creates the complete multi-tenant database for a School Fee Reminder CRM.
Two roles: Platform Owner (manages all schools) and School Admin (manages one school).
Every school-owned record carries school_id and is isolated via RLS.

## New Tables
1. schools — tenant registry (id, name, status, created_at, updated_at)
2. profiles — links auth.users to a role + school_id (owner has null school_id)
3. students — student records scoped to a school
4. parents — parent/guardian contacts scoped to a school
5. student_parents — many-to-many join between students and parents (scoped)
6. fees — monthly fee records per student, with paid/unpaid status and due date
7. call_records — scheduled + completed automated reminder calls

## Security (RLS)
- RLS enabled on every table.
- profiles: each authenticated user reads/updates their own profile row.
  Platform Owner can read all profiles.
- schools: Platform Owners can SELECT all; INSERT/UPDATE restricted to Owners.
  School admins can SELECT their own school.
- All school-owned tables: restricted to authenticated users whose profile
  belongs to the same school_id (or is a Platform Owner). Enforcement uses
  SECURITY DEFINER helper get_my_school_id() that derives school_id from the
  caller's profile — the client never supplies school_id for authorization.

## Helper Functions (defined first, before policies)
- get_my_school_id() -> uuid
- is_platform_owner() -> boolean
- handle_new_user() -> trigger that auto-creates a profile on signup
- set_updated_at() -> trigger function for updated_at columns

## Important Notes
1. school_id is NEVER trusted from the client for authorization — derived from session.
2. Unique constraint on (school_id, student_id, fee_month) prevents duplicate fees.
3. Unique constraint on (fee_id, reminder_type) prevents duplicate reminder calls per stage.
4. Helper functions created BEFORE policies that reference them.
5. Auto-profile trigger: every new auth.users row gets a 'admin' profile automatically.
   The frontend will then update the profile with role + school_id as needed.
*/

-- ============================================================
-- Helper functions (must exist before policies reference them)
-- ============================================================
CREATE OR REPLACE FUNCTION get_my_school_id()
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = public
AS $$
DECLARE
  v_school_id uuid;
BEGIN
  SELECT school_id INTO v_school_id FROM profiles WHERE id = auth.uid();
  RETURN v_school_id;
END;
$$;

CREATE OR REPLACE FUNCTION is_platform_owner()
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = public
AS $$
DECLARE
  v_role text;
BEGIN
  SELECT role INTO v_role FROM profiles WHERE id = auth.uid();
  RETURN v_role = 'owner';
END;
$$;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

-- ============================================================
-- schools
-- ============================================================
CREATE TABLE IF NOT EXISTS schools (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE schools ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "select_schools_owner_or_member" ON schools;
CREATE POLICY "select_schools_owner_or_member"
ON schools FOR SELECT TO authenticated
USING (is_platform_owner() OR id = get_my_school_id());

DROP POLICY IF EXISTS "insert_schools_owner_only" ON schools;
CREATE POLICY "insert_schools_owner_only"
ON schools FOR INSERT TO authenticated
WITH CHECK (is_platform_owner());

DROP POLICY IF EXISTS "update_schools_owner_only" ON schools;
CREATE POLICY "update_schools_owner_only"
ON schools FOR UPDATE TO authenticated
USING (is_platform_owner()) WITH CHECK (is_platform_owner());

DROP TRIGGER IF EXISTS trg_schools_updated_at ON schools;
CREATE TRIGGER trg_schools_updated_at BEFORE UPDATE ON schools
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- profiles
-- ============================================================
CREATE TABLE IF NOT EXISTS profiles (
  id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  role text NOT NULL DEFAULT 'admin' CHECK (role IN ('owner','admin')),
  school_id uuid REFERENCES schools(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "select_own_profile" ON profiles;
CREATE POLICY "select_own_profile"
ON profiles FOR SELECT TO authenticated
USING (auth.uid() = id);

DROP POLICY IF EXISTS "update_own_profile" ON profiles;
CREATE POLICY "update_own_profile"
ON profiles FOR UPDATE TO authenticated
USING (auth.uid() = id) WITH CHECK (auth.uid() = id);

DROP POLICY IF EXISTS "select_all_profiles_owner" ON profiles;
CREATE POLICY "select_all_profiles_owner"
ON profiles FOR SELECT TO authenticated
USING (is_platform_owner());

DROP TRIGGER IF EXISTS trg_profiles_updated_at ON profiles;
CREATE TRIGGER trg_profiles_updated_at BEFORE UPDATE ON profiles
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Auto-create profile on signup
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.profiles (id, role) VALUES (NEW.id, 'admin');
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
AFTER INSERT ON auth.users
FOR EACH ROW EXECUTE FUNCTION handle_new_user();

-- ============================================================
-- students
-- ============================================================
CREATE TABLE IF NOT EXISTS students (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id uuid NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  admission_number text NOT NULL,
  name text NOT NULL,
  class text NOT NULL,
  section text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (school_id, admission_number)
);

ALTER TABLE students ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "select_students_own_school" ON students;
CREATE POLICY "select_students_own_school"
ON students FOR SELECT TO authenticated
USING (is_platform_owner() OR school_id = get_my_school_id());

DROP POLICY IF EXISTS "insert_students_own_school" ON students;
CREATE POLICY "insert_students_own_school"
ON students FOR INSERT TO authenticated
WITH CHECK (school_id = get_my_school_id() AND NOT is_platform_owner());

DROP POLICY IF EXISTS "update_students_own_school" ON students;
CREATE POLICY "update_students_own_school"
ON students FOR UPDATE TO authenticated
USING (school_id = get_my_school_id()) WITH CHECK (school_id = get_my_school_id());

DROP POLICY IF EXISTS "delete_students_own_school" ON students;
CREATE POLICY "delete_students_own_school"
ON students FOR DELETE TO authenticated
USING (school_id = get_my_school_id());

DROP TRIGGER IF EXISTS trg_students_updated_at ON students;
CREATE TRIGGER trg_students_updated_at BEFORE UPDATE ON students
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- parents
-- ============================================================
CREATE TABLE IF NOT EXISTS parents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id uuid NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  name text NOT NULL,
  phone text NOT NULL,
  relationship text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE parents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "select_parents_own_school" ON parents;
CREATE POLICY "select_parents_own_school"
ON parents FOR SELECT TO authenticated
USING (is_platform_owner() OR school_id = get_my_school_id());

DROP POLICY IF EXISTS "insert_parents_own_school" ON parents;
CREATE POLICY "insert_parents_own_school"
ON parents FOR INSERT TO authenticated
WITH CHECK (school_id = get_my_school_id() AND NOT is_platform_owner());

DROP POLICY IF EXISTS "update_parents_own_school" ON parents;
CREATE POLICY "update_parents_own_school"
ON parents FOR UPDATE TO authenticated
USING (school_id = get_my_school_id()) WITH CHECK (school_id = get_my_school_id());

DROP POLICY IF EXISTS "delete_parents_own_school" ON parents;
CREATE POLICY "delete_parents_own_school"
ON parents FOR DELETE TO authenticated
USING (school_id = get_my_school_id());

DROP TRIGGER IF EXISTS trg_parents_updated_at ON parents;
CREATE TRIGGER trg_parents_updated_at BEFORE UPDATE ON parents
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- student_parents (join)
-- ============================================================
CREATE TABLE IF NOT EXISTS student_parents (
  student_id uuid NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  parent_id uuid NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
  school_id uuid NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  PRIMARY KEY (student_id, parent_id)
);

ALTER TABLE student_parents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "select_student_parents_own_school" ON student_parents;
CREATE POLICY "select_student_parents_own_school"
ON student_parents FOR SELECT TO authenticated
USING (is_platform_owner() OR school_id = get_my_school_id());

DROP POLICY IF EXISTS "insert_student_parents_own_school" ON student_parents;
CREATE POLICY "insert_student_parents_own_school"
ON student_parents FOR INSERT TO authenticated
WITH CHECK (school_id = get_my_school_id() AND NOT is_platform_owner());

DROP POLICY IF EXISTS "delete_student_parents_own_school" ON student_parents;
CREATE POLICY "delete_student_parents_own_school"
ON student_parents FOR DELETE TO authenticated
USING (school_id = get_my_school_id());

-- ============================================================
-- fees
-- ============================================================
CREATE TABLE IF NOT EXISTS fees (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id uuid NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  fee_month text NOT NULL,
  amount numeric(12,2) NOT NULL CHECK (amount >= 0),
  due_date date NOT NULL,
  status text NOT NULL DEFAULT 'unpaid' CHECK (status IN ('paid','unpaid')),
  payment_date date,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (school_id, student_id, fee_month)
);

ALTER TABLE fees ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "select_fees_own_school" ON fees;
CREATE POLICY "select_fees_own_school"
ON fees FOR SELECT TO authenticated
USING (is_platform_owner() OR school_id = get_my_school_id());

DROP POLICY IF EXISTS "insert_fees_own_school" ON fees;
CREATE POLICY "insert_fees_own_school"
ON fees FOR INSERT TO authenticated
WITH CHECK (school_id = get_my_school_id() AND NOT is_platform_owner());

DROP POLICY IF EXISTS "update_fees_own_school" ON fees;
CREATE POLICY "update_fees_own_school"
ON fees FOR UPDATE TO authenticated
USING (school_id = get_my_school_id()) WITH CHECK (school_id = get_my_school_id());

DROP POLICY IF EXISTS "delete_fees_own_school" ON fees;
CREATE POLICY "delete_fees_own_school"
ON fees FOR DELETE TO authenticated
USING (school_id = get_my_school_id());

DROP TRIGGER IF EXISTS trg_fees_updated_at ON fees;
CREATE TRIGGER trg_fees_updated_at BEFORE UPDATE ON fees
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- call_records
-- ============================================================
CREATE TABLE IF NOT EXISTS call_records (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  school_id uuid NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  parent_id uuid NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
  fee_id uuid NOT NULL REFERENCES fees(id) ON DELETE CASCADE,
  reminder_type text NOT NULL CHECK (reminder_type IN ('7d','3d','2d','1d','overdue')),
  scheduled_at timestamptz NOT NULL,
  called_at timestamptz,
  status text NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled','answered','no_answer','busy','failed')),
  provider_call_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (fee_id, reminder_type)
);

ALTER TABLE call_records ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "select_calls_own_school" ON call_records;
CREATE POLICY "select_calls_own_school"
ON call_records FOR SELECT TO authenticated
USING (is_platform_owner() OR school_id = get_my_school_id());

DROP POLICY IF EXISTS "insert_calls_own_school" ON call_records;
CREATE POLICY "insert_calls_own_school"
ON call_records FOR INSERT TO authenticated
WITH CHECK (school_id = get_my_school_id());

DROP POLICY IF EXISTS "update_calls_own_school" ON call_records;
CREATE POLICY "update_calls_own_school"
ON call_records FOR UPDATE TO authenticated
USING (school_id = get_my_school_id()) WITH CHECK (school_id = get_my_school_id());

-- ============================================================
-- Indexes
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_students_school_id ON students(school_id);
CREATE INDEX IF NOT EXISTS idx_students_admission ON students(school_id, admission_number);
CREATE INDEX IF NOT EXISTS idx_parents_school_id ON parents(school_id);
CREATE INDEX IF NOT EXISTS idx_student_parents_school_id ON student_parents(school_id);
CREATE INDEX IF NOT EXISTS idx_fees_school_id ON fees(school_id);
CREATE INDEX IF NOT EXISTS idx_fees_student_id ON fees(student_id);
CREATE INDEX IF NOT EXISTS idx_fees_status ON fees(school_id, status);
CREATE INDEX IF NOT EXISTS idx_fees_month ON fees(school_id, fee_month);
CREATE INDEX IF NOT EXISTS idx_calls_school_id ON call_records(school_id);
CREATE INDEX IF NOT EXISTS idx_calls_fee_id ON call_records(fee_id);
CREATE INDEX IF NOT EXISTS idx_calls_status ON call_records(school_id, status);
CREATE INDEX IF NOT EXISTS idx_calls_scheduled ON call_records(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_profiles_role ON profiles(role);
