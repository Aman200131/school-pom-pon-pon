import { createClient } from "npm:@supabase/supabase-js@2.57.4";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Client-Info, Apikey",
};

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
    const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
    const supabase = createClient(supabaseUrl, serviceRoleKey);

    // Run the reminder engine — creates scheduled call_records for due reminders
    const { data: scheduled, error: reminderError } = await supabase.rpc(
      "run_reminder_engine"
    );
    if (reminderError) throw reminderError;

    // Process pending calls in mock mode (no real provider configured)
    const { data: processed, error: processError } = await supabase.rpc(
      "process_pending_calls_mock"
    );
    if (processError) throw processError;

    const summary = {
      scheduled: (scheduled as unknown[] | null)?.length ?? 0,
      processed: (processed as unknown[] | null)?.length ?? 0,
      timestamp: new Date().toISOString(),
    };

    return new Response(JSON.stringify(summary), {
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  } catch (err) {
    return new Response(
      JSON.stringify({ error: err instanceof Error ? err.message : "Unknown error" }),
      {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      }
    );
  }
});
