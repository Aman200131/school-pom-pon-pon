/*
# Security hardening: restrict SECURITY DEFINER function access + fix search_path

## Security Changes
1. Revoke EXECUTE on get_my_school_id() and is_platform_owner() from anon role.
   These functions are only used inside RLS policies (which run as the authenticated
   user) — they should not be callable directly by unauthenticated requests.
   Authenticated users can still call them (harmless — they only return the caller's
   own school_id or role).

2. Set a fixed search_path on set_updated_at() trigger function to resolve the
   "mutable search_path" security warning.

3. handle_new_user() already has SET search_path = public — no change needed.
*/

REVOKE EXECUTE ON FUNCTION get_my_school_id() FROM anon;
REVOKE EXECUTE ON FUNCTION is_platform_owner() FROM anon;

-- Recreate set_updated_at with a fixed search_path
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;
