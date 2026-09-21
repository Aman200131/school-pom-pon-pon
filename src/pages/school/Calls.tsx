import { useEffect, useState } from 'react';
import { useAuth } from '@/context/AuthContext';
import { supabase } from '@/lib/supabase';
import { EmptyState } from '@/components/EmptyState';
import { Phone, Search, RefreshCw, Loader2 } from 'lucide-react';
import type { CallWithDetails } from '@/types/database';

const statusBadge: Record<string, string> = {
  scheduled: 'badge-info',
  answered: 'badge-success',
  no_answer: 'badge-warning',
  busy: 'badge-warning',
  failed: 'badge-danger',
};

const reminderLabels: Record<string, string> = {
  '7d': '7 days before',
  '3d': '3 days before',
  '2d': '2 days before',
  '1d': '1 day before',
  overdue: 'Overdue',
};

export function CallsPage() {
  const { profile } = useAuth();
  const schoolId = profile?.school_id;
  const [calls, setCalls] = useState<CallWithDetails[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [triggering, setTriggering] = useState(false);
  const [triggerMsg, setTriggerMsg] = useState<string | null>(null);

  useEffect(() => {
    if (schoolId) fetchCalls();
  }, [schoolId]);

  const fetchCalls = async () => {
    if (!schoolId) return;
    setLoading(true);
    const { data } = await supabase
      .from('call_records')
      .select('*, students(name, admission_number), parents(name, phone), fees(fee_month, amount, due_date)')
      .eq('school_id', schoolId)
      .order('scheduled_at', { ascending: false })
      .limit(200);
    setCalls((data as CallWithDetails[]) || []);
    setLoading(false);
  };

  const triggerReminders = async () => {
    setTriggering(true);
    setTriggerMsg(null);
    try {
      const apiUrl = `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/run-reminders`;
      const { data: sessionData } = await supabase.auth.getSession();
      const response = await fetch(apiUrl, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${sessionData.session?.access_token}`,
          'Content-Type': 'application/json',
        },
      });
      if (!response.ok) throw new Error(`Failed (${response.status})`);
      const result = await response.json();
      setTriggerMsg(`Scheduled ${result.scheduled} new calls, processed ${result.processed} pending calls.`);
      fetchCalls();
    } catch (err) {
      setTriggerMsg(`Error: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
    setTriggering(false);
  };

  const filtered = calls.filter((c) => {
    const matchesSearch =
      c.students?.name?.toLowerCase().includes(search.toLowerCase()) ||
      c.parents?.name?.toLowerCase().includes(search.toLowerCase()) ||
      c.parents?.phone?.includes(search);
    const matchesStatus = statusFilter === 'all' || c.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  if (loading) {
    return <div className="flex h-full items-center justify-center py-20 text-slate-400">Loading...</div>;
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-slate-900">Call Records</h1>
        <button onClick={triggerReminders} disabled={triggering} className="btn-secondary">
          {triggering ? <Loader2 className="animate-spin" size={18} /> : <RefreshCw size={18} />}
          Run Reminder Engine Now
        </button>
      </div>

      {triggerMsg && (
        <div className="mb-4 rounded-lg bg-blue-50 px-4 py-3 text-sm text-blue-800">{triggerMsg}</div>
      )}

      <div className="mb-4 flex flex-wrap gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
          <input
            type="text"
            placeholder="Search by student, parent, or phone..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="input-field pl-10"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="input-field max-w-[140px]"
        >
          <option value="all">All Status</option>
          <option value="scheduled">Scheduled</option>
          <option value="answered">Answered</option>
          <option value="no_answer">No Answer</option>
          <option value="busy">Busy</option>
          <option value="failed">Failed</option>
        </select>
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          icon={<Phone size={32} />}
          title="No call records"
          description="Calls are generated automatically by the reminder engine. Click 'Run Reminder Engine Now' to trigger it."
        />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-slate-500">
                <th className="px-4 py-3 font-medium">Student</th>
                <th className="px-4 py-3 font-medium">Parent</th>
                <th className="px-4 py-3 font-medium">Phone</th>
                <th className="px-4 py-3 font-medium">Reminder</th>
                <th className="px-4 py-3 font-medium">Scheduled</th>
                <th className="px-4 py-3 font-medium">Called</th>
                <th className="px-4 py-3 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((call) => (
                <tr key={call.id} className="border-b border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-3 font-medium text-slate-900">{call.students?.name || '—'}</td>
                  <td className="px-4 py-3 text-slate-600">{call.parents?.name || '—'}</td>
                  <td className="px-4 py-3 text-slate-600">{call.parents?.phone || '—'}</td>
                  <td className="px-4 py-3 text-slate-600">{reminderLabels[call.reminder_type] || call.reminder_type}</td>
                  <td className="px-4 py-3 text-slate-500 text-xs">
                    {new Date(call.scheduled_at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}
                  </td>
                  <td className="px-4 py-3 text-slate-500 text-xs">
                    {call.called_at ? new Date(call.called_at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }) : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`badge ${statusBadge[call.status] || 'badge-neutral'}`}>
                      {call.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
