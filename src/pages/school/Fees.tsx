import { useEffect, useState, type FormEvent } from 'react';
import { useAuth } from '@/context/AuthContext';
import { supabase } from '@/lib/supabase';
import { Modal } from '@/components/Modal';
import { EmptyState } from '@/components/EmptyState';
import { Plus, DollarSign, Search, Loader2, CheckCircle2 } from 'lucide-react';
import type { FeeWithStudent, Student } from '@/types/database';

export function FeesPage() {
  const { profile } = useAuth();
  const schoolId = profile?.school_id;
  const [fees, setFees] = useState<FeeWithStudent[]>([]);
  const [students, setStudents] = useState<Student[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'paid' | 'unpaid'>('all');
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState({ student_id: '', fee_month: '', amount: '', due_date: '' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (schoolId) {
      fetchFees();
      fetchStudents();
    }
  }, [schoolId]);

  const fetchFees = async () => {
    if (!schoolId) return;
    setLoading(true);
    const { data } = await supabase
      .from('fees')
      .select('*, students(name, admission_number, class, section)')
      .eq('school_id', schoolId)
      .order('due_date', { ascending: false })
      .limit(500);
    setFees((data as FeeWithStudent[]) || []);
    setLoading(false);
  };

  const fetchStudents = async () => {
    if (!schoolId) return;
    const { data } = await supabase
      .from('students')
      .select('*')
      .eq('school_id', schoolId)
      .order('name');
    setStudents((data as Student[]) || []);
  };

  const openAdd = () => {
    setForm({ student_id: '', fee_month: '', amount: '', due_date: '' });
    setError(null);
    setModalOpen(true);
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!schoolId || !form.student_id) return;
    setSaving(true);
    setError(null);

    const { error: insertError } = await supabase.from('fees').insert({
      school_id: schoolId,
      student_id: form.student_id,
      fee_month: form.fee_month,
      amount: parseFloat(form.amount),
      due_date: form.due_date,
      status: 'unpaid',
    });

    if (insertError) {
      setError(insertError.message);
      setSaving(false);
      return;
    }

    setSaving(false);
    setModalOpen(false);
    fetchFees();
  };

  const markPaid = async (feeId: string) => {
    const today = new Date().toISOString().split('T')[0];
    await supabase
      .from('fees')
      .update({ status: 'paid', payment_date: today })
      .eq('id', feeId);
    fetchFees();
  };

  const markUnpaid = async (feeId: string) => {
    await supabase
      .from('fees')
      .update({ status: 'unpaid', payment_date: null })
      .eq('id', feeId);
    fetchFees();
  };

  const today = new Date().toISOString().split('T')[0];

  const filtered = fees.filter((f) => {
    const matchesSearch =
      f.students?.name?.toLowerCase().includes(search.toLowerCase()) ||
      f.fee_month.toLowerCase().includes(search.toLowerCase());
    const matchesStatus = statusFilter === 'all' || f.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  if (loading) {
    return <div className="flex h-full items-center justify-center py-20 text-slate-400">Loading...</div>;
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-slate-900">Fees</h1>
        <button onClick={openAdd} className="btn-primary" disabled={students.length === 0}>
          <Plus size={18} />
          Add Fee
        </button>
      </div>

      {students.length === 0 && (
        <div className="mb-4 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Add students first before creating fee records.
        </div>
      )}

      <div className="mb-4 flex flex-wrap gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
          <input
            type="text"
            placeholder="Search by student or month..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="input-field pl-10"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as 'all' | 'paid' | 'unpaid')}
          className="input-field max-w-[140px]"
        >
          <option value="all">All</option>
          <option value="paid">Paid</option>
          <option value="unpaid">Unpaid</option>
        </select>
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          icon={<DollarSign size={32} />}
          title="No fee records"
          description="Add fees manually or import via Excel."
        />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-slate-500">
                <th className="px-4 py-3 font-medium">Student</th>
                <th className="px-4 py-3 font-medium">Month</th>
                <th className="px-4 py-3 font-medium">Amount</th>
                <th className="px-4 py-3 font-medium">Due Date</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((fee) => {
                const isOverdue = fee.status === 'unpaid' && fee.due_date < today;
                return (
                  <tr key={fee.id} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="px-4 py-3 font-medium text-slate-900">
                      {fee.students?.name || '—'}
                      <span className="block text-xs text-slate-400">{fee.students?.admission_number}</span>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{fee.fee_month}</td>
                    <td className="px-4 py-3 text-slate-600">₹{Number(fee.amount).toLocaleString()}</td>
                    <td className="px-4 py-3 text-slate-600">{fee.due_date}</td>
                    <td className="px-4 py-3">
                      <span className={`badge ${fee.status === 'paid' ? 'badge-success' : isOverdue ? 'badge-danger' : 'badge-warning'}`}>
                        {isOverdue ? 'overdue' : fee.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      {fee.status === 'unpaid' ? (
                        <button
                          onClick={() => markPaid(fee.id)}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100"
                        >
                          <CheckCircle2 size={14} />
                          Mark Paid
                        </button>
                      ) : (
                        <button
                          onClick={() => markUnpaid(fee.id)}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100"
                        >
                          Mark Unpaid
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title="Add Fee">
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
          <div>
            <label className="label-text">Student</label>
            <select
              required
              value={form.student_id}
              onChange={(e) => setForm({ ...form, student_id: e.target.value })}
              className="input-field"
            >
              <option value="">Select student...</option>
              {students.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.admission_number}) — {s.class}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label-text">Fee Month</label>
            <input
              required
              value={form.fee_month}
              onChange={(e) => setForm({ ...form, fee_month: e.target.value })}
              className="input-field"
              placeholder="e.g. 2026-09 or September 2026"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label-text">Amount (₹)</label>
              <input
                required
                type="number"
                min="0"
                step="0.01"
                value={form.amount}
                onChange={(e) => setForm({ ...form, amount: e.target.value })}
                className="input-field"
              />
            </div>
            <div>
              <label className="label-text">Due Date</label>
              <input
                required
                type="date"
                value={form.due_date}
                onChange={(e) => setForm({ ...form, due_date: e.target.value })}
                className="input-field"
              />
            </div>
          </div>
          <button type="submit" disabled={saving} className="btn-primary w-full">
            {saving ? <Loader2 className="animate-spin" size={18} /> : 'Add Fee'}
          </button>
        </form>
      </Modal>
    </div>
  );
}
