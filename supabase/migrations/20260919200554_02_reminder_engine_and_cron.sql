/*
# Reminder Engine + pg_cron Schedule

## Overview
Creates the core reminder engine as a SECURITY DEFINER SQL function that:
1. Scans all unpaid fees across all schools.
2. Computes reminder dates from each fee's due_date: 7d, 3d, 2d, 1d before, and overdue (after due date).
3. Inserts call_records (status='scheduled') for each due reminder stage, respecting the
   unique(fee_id, reminder_type) constraint so duplicate reminders are never created.
4. Returns a summary of newly scheduled calls.

Also creates:
- A mock-call processor function that simulates call outcomes for scheduled calls
  whose scheduled_at has passed (used when no real provider is configured).
- pg_cron schedule to run the reminder engine every hour.
- pg_cron schedule to process pending calls every 15 minutes.

## Security
- run_reminder_engine() and process_pending_calls_mock() are SECURITY DEFINER
  so the cron job (running as postgres) can operate across all schools.
  They are NOT exposed to anon/authenticated via grants — only cron and the
  service-role edge function can call them.

## Important Notes
1. Reminders are only created for fees with status='unpaid'.
2. The unique(fee_id, reminder_type) constraint prevents duplicate reminders per stage.
3. Reminder dates are computed as due_date - interval, and overdue = due_date + 1 day.
4. Only creates a call_record when the reminder date has arrived (<= now()).
5. Mock processor assigns a random status (answered/no_answer/busy/failed) and sets called_at.
6. pg_cron runs in the GMT timezone by default.
*/

-- ============================================================
-- run_reminder_engine: scan unpaid fees and create scheduled call_records
-- ============================================================
CREATE OR REPLACE FUNCTION run_reminder_engine()
RETURNS TABLE(
  school_id uuid,
  fee_id uuid,
  reminder_type text,
  scheduled_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_now timestamptz := now();
  r RECORD;
BEGIN
  -- For each unpaid fee, check each reminder stage
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
      INTO school_id, fee_id, reminder_type, scheduled_at;
      IF FOUND THEN RETURN NEXT; END IF;
    END IF;

    -- 3 days before
    IF v_now >= (r.due_date - interval '3 days') THEN
      INSERT INTO call_records (school_id, student_id, parent_id, fee_id, reminder_type, scheduled_at, status)
      VALUES (r.school_id, r.student_id, r.parent_id, r.fee_id, '3d', v_now, 'scheduled')
      ON CONFLICT (fee_id, reminder_type) DO NOTHING
      RETURNING call_records.school_id, call_records.fee_id, call_records.reminder_type, call_records.scheduled_at
      INTO school_id, fee_id, reminder_type, scheduled_at;
      IF FOUND THEN RETURN NEXT; END IF;
    END IF;

    -- 2 days before
    IF v_now >= (r.due_date - interval '2 days') THEN
      INSERT INTO call_records (school_id, student_id, parent_id, fee_id, reminder_type, scheduled_at, status)
      VALUES (r.school_id, r.student_id, r.parent_id, r.fee_id, '2d', v_now, 'scheduled')
      ON CONFLICT (fee_id, reminder_type) DO NOTHING
      RETURNING call_records.school_id, call_records.fee_id, call_records.reminder_type, call_records.scheduled_at
      INTO school_id, fee_id, reminder_type, scheduled_at;
      IF FOUND THEN RETURN NEXT; END IF;
    END IF;

    -- 1 day before
    IF v_now >= (r.due_date - interval '1 day') THEN
      INSERT INTO call_records (school_id, student_id, parent_id, fee_id, reminder_type, scheduled_at, status)
      VALUES (r.school_id, r.student_id, r.parent_id, r.fee_id, '1d', v_now, 'scheduled')
      ON CONFLICT (fee_id, reminder_type) DO NOTHING
      RETURNING call_records.school_id, call_records.fee_id, call_records.reminder_type, call_records.scheduled_at
      INTO school_id, fee_id, reminder_type, scheduled_at;
      IF FOUND THEN RETURN NEXT; END IF;
    END IF;

    -- Overdue (after due date)
    IF v_now >= (r.due_date + interval '1 day') THEN
      INSERT INTO call_records (school_id, student_id, parent_id, fee_id, reminder_type, scheduled_at, status)
      VALUES (r.school_id, r.student_id, r.parent_id, r.fee_id, 'overdue', v_now, 'scheduled')
      ON CONFLICT (fee_id, reminder_type) DO NOTHING
      RETURNING call_records.school_id, call_records.fee_id, call_records.reminder_type, call_records.scheduled_at
      INTO school_id, fee_id, reminder_type, scheduled_at;
      IF FOUND THEN RETURN NEXT; END IF;
    END IF;
  END LOOP;
END;
$$;

-- ============================================================
-- process_pending_calls_mock: simulate call outcomes for due scheduled calls
-- Used when no real calling provider is configured.
-- ============================================================
CREATE OR REPLACE FUNCTION process_pending_calls_mock()
RETURNS TABLE(
  call_id uuid,
  new_status text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  r RECORD;
  v_statuses text[] := ARRAY['answered','no_answer','busy','failed'];
  v_new_status text;
BEGIN
  FOR r IN
    SELECT id FROM call_records
    WHERE status = 'scheduled' AND scheduled_at <= now()
    ORDER BY scheduled_at
    LIMIT 100
  LOOP
    v_new_status := v_statuses[1 + floor(random() * 4)::int];
    UPDATE call_records
    SET status = v_new_status,
        called_at = now(),
        provider_call_id = 'mock_' || gen_random_uuid()::text
    WHERE id = r.id;
    call_id := r.id;
    new_status := v_new_status;
    RETURN NEXT;
  END LOOP;
END;
$$;

-- ============================================================
-- Grant execution only to service_role (not anon/authenticated)
-- ============================================================
REVOKE EXECUTE ON FUNCTION run_reminder_engine() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION process_pending_calls_mock() FROM PUBLIC;

-- ============================================================
-- Enable pg_cron and schedule jobs
-- ============================================================
CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA extensions;

-- Schedule reminder engine every hour at minute 0
SELECT cron.schedule(
  'reminder-engine-hourly',
  '0 * * * *',
  $$SELECT run_reminder_engine();$$
);

-- Schedule mock call processor every 15 minutes
SELECT cron.schedule(
  'mock-call-processor',
  '*/15 * * * *',
  $$SELECT process_pending_calls_mock();$$
);
