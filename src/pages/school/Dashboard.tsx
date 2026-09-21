import { useEffect, useState } from 'react';
import { useAuth } from '@/context/AuthContext';
import { supabase } from '@/lib/supabase';
import { StatCard } from '@/components/StatCard';
import { EmptyState } from '@/components/EmptyState';
import {
  Users,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Phone,
  PhoneCall,
  PhoneOff,
  XOctagon,
  Upload,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import type { FeeWithStudent, CallWithDetails } from '@/types/database';

interface DashboardStats {
  totalStudents: number;
  paidFees: number;
  unpaidFees: number;
  overdueFees: number;
  todayCalls: number;
  answeredCalls: number;
  noAnswerCalls: number;
  failedCalls: number;
}

export function SchoolDashboard() {
  const { profile } = useAuth();
  const schoolId = profile?.school_id;
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [recentFees, setRecentFees] = useState<FeeWithStudent[]>([]);
  const [recentCalls, setRecentCalls] = useState<CallWithDetails[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!schoolId) return;
    fetchDashboardData();
  }, [schoolId]);

  const fetchDashboardData = async () => {
    if (!schoolId) return;
    setLoading(true);

    const today = new Date().toISOString().split('T')[0];

    const [students, fees, todayCallsR, allCalls] = await Promise.all([
      supabase.from('students').select('id', { count: 'exact', head: true }).eq('school_id', schoolId),
      supabase.from('fees').select('id, status, due_date').eq('school_id', schoolId),
      supabase.from('call_records').select('id, status').eq('school_id', schoolId).gte('scheduled_at', today + 'T00:00:00'),
      supabase.from('call_records').select('id, status').eq('school_id', schoolId),
    ]);

    const feesData = fees.data || [];
    const paid = feesData.filter((f) => f.status === 'paid').length;
    const unpaid = feesData.filter((f) => f.status === 'unpaid').length;
    const overdue = feesData.filter(
      (f) => f.status === 'unpaid' && new Date(f.due_date) < new Date(today)
    ).length;

    const allCallsData = allCalls.data || [];
    const answered = allCallsData.filter((c) => c.status === 'answered').length;
    const noAnswer = allCallsData.filter((c) => c.status === 'no_answer').length;
    const failed = allCallsData.filter((c) => c.status === 'failed').length;

    setStats({
      totalStudents: students.count || 0,
      paidFees: paid,
      unpaidFees: unpaid,
      overdueFees: overdue,
      todayCalls: todayCallsR.data?.length || 0,
      answeredCalls: answered,
      noAnswerCalls: noAnswer,
      failedCalls: failed,
    });

    // Recent fees
    const { data: feesWithStudents } = await supabase
      .from('fees')
      .select('*, students(name, admission_number, class, section)')
      .eq('school_id', schoolId)
      .order('created_at', { ascending: false })
      .limit(5);
    setRecentFees((feesWithStudents as FeeWithStudent[]) || []);

    // Recent calls
    const { data: callsWithDetails } = await supabase
      .from('call_records')
      .select('*, students(name, admission_number), parents(name, phone), fees(fee_month, amount, due_date)')
      .eq('school_id', schoolId)
      .order('created_at', { ascending: false })
      .limit(5);
    setRecentCalls((callsWithDetails as CallWithDetails[]) || []);

    setLoading(false);
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center py-20">
        <div className="text-slate-400">Loading dashboard...</div>
      </div>
    );
  }

  if (stats && stats.totalStudents === 0) {
    return (
      <div>
        <h1 className="mb-6 text-2xl font-bold text-slate-900">Dashboard</h1>
        <EmptyState
          icon={<Users size={32} />}
          title="No students yet"
          description="Import your students and fees via Excel to get started."
          action={
            <Link to="/import" className="btn-primary">
              <Upload size={18} />
              Import Students
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-slate-900">Dashboard</h1>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Total Students" value={stats?.totalStudents ?? 0} icon={<Users size={24} />} tone="info" />
        <StatCard label="Paid Fees" value={stats?.paidFees ?? 0} icon={<CheckCircle2 size={24} />} tone="success" />
        <StatCard label="Unpaid Fees" value={stats?.unpaidFees ?? 0} icon={<XCircle size={24} />} tone="warning" />
        <StatCard label="Overdue Fees" value={stats?.overdueFees ?? 0} icon={<AlertTriangle size={24} />} tone="danger" />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Today's Scheduled Calls" value={stats?.todayCalls ?? 0} icon={<Phone size={24} />} tone="info" />
        <StatCard label="Answered Calls" value={stats?.answeredCalls ?? 0} icon={<PhoneCall size={24} />} tone="success" />
        <StatCard label="No-Answer Calls" value={stats?.noAnswerCalls ?? 0} icon={<PhoneOff size={24} />} tone="warning" />
        <StatCard label="Failed Calls" value={stats?.failedCalls ?? 0} icon={<XOctagon size={24} />} tone="danger" />
      </div>

      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Recent Fees */}
        <div className="card p-6">
          <h2 className="mb-4 text-lg font-semibold text-slate-900">Recent Fees</h2>
          {recentFees.length === 0 ? (
            <p className="py-6 text-center text-sm text-slate-500">No fee records yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-slate-500">
                    <th className="pb-2 font-medium">Student</th>
                    <th className="pb-2 font-medium">Month</th>
                    <th className="pb-2 font-medium">Amount</th>
                    <th className="pb-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {recentFees.map((fee) => (
                    <tr key={fee.id} className="border-b border-slate-100">
                      <td className="py-2.5 text-slate-900">{fee.students?.name || '—'}</td>
                      <td className="py-2.5 text-slate-600">{fee.fee_month}</td>
                      <td className="py-2.5 text-slate-600">₹{fee.amount}</td>
                      <td className="py-2.5">
                        <span className={`badge ${fee.status === 'paid' ? 'badge-success' : 'badge-warning'}`}>
                          {fee.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Recent Calls */}
        <div className="card p-6">
          <h2 className="mb-4 text-lg font-semibold text-slate-900">Recent Calls</h2>
          {recentCalls.length === 0 ? (
            <p className="py-6 text-center text-sm text-slate-500">No call records yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-left text-slate-500">
                    <th className="pb-2 font-medium">Student</th>
                    <th className="pb-2 font-medium">Type</th>
                    <th className="pb-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {recentCalls.map((call) => (
                    <tr key={call.id} className="border-b border-slate-100">
                      <td className="py-2.5 text-slate-900">{call.students?.name || '—'}</td>
                      <td className="py-2.5 text-slate-600">{call.reminder_type}</td>
                      <td className="py-2.5">
                        <span className={`badge ${
                          call.status === 'answered' ? 'badge-success' :
                          call.status === 'scheduled' ? 'badge-info' :
                          call.status === 'failed' ? 'badge-danger' :
                          'badge-warning'
                        }`}>
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
      </div>
    </div>
  );
}
