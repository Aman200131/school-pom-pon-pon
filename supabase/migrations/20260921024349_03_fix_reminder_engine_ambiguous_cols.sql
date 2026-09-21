/*
# Fix reminder engine: resolve ambiguous column references

## Problem
The RETURNING clause in run_reminder_engine() used variable names (school_id, fee_id, 
reminder_type, scheduled_at) that are identical to call_records column names, causing 
a "column reference is ambiguous" PL/pgSQL error.

## Fix
Drop and recreate the function with output variables prefixed with `out_` to avoid 
naming collisions with table columns.

## Security
Function remains SECURITY DEFINER, execution revoked from PUBLIC.
*/

DROP FUNCTION IF EXISTS run_reminder_engine();

CREATE FUNCTION run_reminder_engine()
RETURNS TABLE(
  out_school_id uuid,
  out_fee_id uuid,
  out_reminder_type text,
  out_scheduled_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_now timestamptz := now();
  r RECORD;
BEGIN
  FOR r IN
    SELECT f.id AS fee_id, f.school_id, f.student_id, f.due_date,
           sp.parent_id
    FROM fees f
    JOIN student_parents sp ON sp.student_id = f.student_id AND sp.school_id = f.school_id
    WHERE f.status = 'unpaid'
  LOOP
    -- 7 days before
    IF v_now >= (r.due_date - interval '7 days') THEN
      INSERT INTO call_records (school_id, student_id, parent_id, fee_id, reminder_type, scheduled_at, status)
      VALUES (r.school_id, r.student_id, r.parent_id, r.fee_id, '7d', v_now, 'scheduled')
      ON CONFLICT (fee_id, reminder_type) DO NOTHING
      RETURNING call_records.school_id, call_records.fee_id, call_records.reminder_type, call_records.scheduled_at
      INTO out_school_id, out_fee_id, out_reminder_type, out_scheduled_at;
      IF FOUND THEN RETURN NEXT; END IF;
    END IF;

    -- 3 days before
    IF v_now >= (r.due_date - interval '3 days') THEN
      INSERT INTO call_records (school_id, student_id, parent_id, fee_id, reminder_type, scheduled_at, status)
      VALUES (r.school_id, r.student_id, r.parent_id, r.fee_id, '3d', v_now, 'scheduled')
      ON CONFLICT (fee_id, reminder_type) DO NOTHING
      RETURNING call_records.school_id, call_records.fee_id, call_records.reminder_type, call_records.scheduled_at
      INTO out_school_id, out_fee_id, out_reminder_type, out_scheduled_at;
      IF FOUND THEN RETURN NEXT; END IF;
    END IF;

    -- 2 days before
    IF v_now >= (r.due_date - interval '2 days') THEN
      INSERT INTO call_records (school_id, student_id, parent_id, fee_id, reminder_type, scheduled_at, status)
      VALUES (r.school_id, r.student_id, r.parent_id, r.fee_id, '2d', v_now, 'scheduled')
      ON CONFLICT (fee_id, reminder_type) DO NOTHING
      RETURNING call_records.school_id, call_records.fee_id, call_records.reminder_type, call_records.scheduled_at
      INTO out_school_id, out_fee_id, out_reminder_type, out_scheduled_at;
      IF FOUND THEN RETURN NEXT; END IF;
    END IF;

    -- 1 day before
    IF v_now >= (r.due_date - interval '1 day') THEN
      INSERT INTO call_records (school_id, student_id, parent_id, fee_id, reminder_type, scheduled_at, status)
      VALUES (r.school_id, r.student_id, r.parent_id, r.fee_id, '1d', v_now, 'scheduled')
      ON CONFLICT (fee_id, reminder_type) DO NOTHING
      RETURNING call_records.school_id, call_records.fee_id, call_records.reminder_type, call_records.scheduled_at
      INTO out_school_id, out_fee_id, out_reminder_type, out_scheduled_at;
      IF FOUND THEN RETURN NEXT; END IF;
    END IF;

    -- Overdue (after due date)
    IF v_now >= (r.due_date + interval '1 day') THEN
      INSERT INTO call_records (school_id, student_id, parent_id, fee_id, reminder_type, scheduled_at, status)
      VALUES (r.school_id, r.student_id, r.parent_id, r.fee_id, 'overdue', v_now, 'scheduled')
      ON CONFLICT (fee_id, reminder_type) DO NOTHING
      RETURNING call_records.school_id, call_records.fee_id, call_records.reminder_type, call_records.scheduled_at
      INTO out_school_id, out_fee_id, out_reminder_type, out_scheduled_at;
      IF FOUND THEN RETURN NEXT; END IF;
    END IF;
  END LOOP;
END;
$$;

REVOKE EXECUTE ON FUNCTION run_reminder_engine() FROM PUBLIC;

-- Update the cron schedule to use the new function signature
SELECT cron.unschedule('reminder-engine-hourly');
SELECT cron.schedule(
  'reminder-engine-hourly',
  '0 * * * *',
  $$SELECT run_reminder_engine();$$
);
