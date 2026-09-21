import { useEffect, useState, type FormEvent } from 'react';
import { supabase } from '@/lib/supabase';
import { Modal } from '@/components/Modal';
import { EmptyState } from '@/components/EmptyState';
import { Plus, Building2, Loader2, Pencil, Trash2 } from 'lucide-react';
import type { School } from '@/types/database';

export function SchoolsPage() {
  const [schools, setSchools] = useState<School[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<School | null>(null);
  const [form, setForm] = useState({ name: '', status: 'active' as 'active' | 'inactive' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchSchools();
  }, []);

  const fetchSchools = async () => {
    setLoading(true);
    const { data } = await supabase.from('schools').select('*').order('created_at', { ascending: false });
    setSchools((data as School[]) || []);
    setLoading(false);
  };

  const openAdd = () => {
    setEditing(null);
    setForm({ name: '', status: 'active' });
    setError(null);
    setModalOpen(true);
  };

  const openEdit = (s: School) => {
    setEditing(s);
    setForm({ name: s.name, status: s.status });
    setError(null);
    setModalOpen(true);
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);

    if (editing) {
      const { error: updateError } = await supabase
        .from('schools')
        .update({ name: form.name, status: form.status })
        .eq('id', editing.id);
      if (updateError) setError(updateError.message);
    } else {
      const { error: insertError } = await supabase.from('schools').insert({
        name: form.name,
        status: form.status,
      });
      if (insertError) setError(insertError.message);
    }

    setSaving(false);
    if (!error) {
      setModalOpen(false);
      fetchSchools();
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this school? This will remove all associated data (students, parents, fees, calls). This cannot be undone.')) return;
    await supabase.from('schools').delete().eq('id', id);
    fetchSchools();
  };

  if (loading) {
    return <div className="flex h-full items-center justify-center py-20 text-slate-400">Loading...</div>;
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-slate-900">Schools</h1>
        <button onClick={openAdd} className="btn-primary">
          <Plus size={18} />
          Add School
        </button>
      </div>

      {schools.length === 0 ? (
        <EmptyState
          icon={<Building2 size={32} />}
          title="No schools yet"
          description="Add your first school to get started."
          action={
            <button onClick={openAdd} className="btn-primary">
              <Plus size={18} />
              Add School
            </button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {schools.map((school) => (
            <div key={school.id} className="card p-5">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-blue-100">
                    <Building2 className="text-blue-600" size={24} />
                  </div>
                  <div>
                    <h3 className="font-semibold text-slate-900">{school.name}</h3>
                    <span className={`badge mt-1 ${school.status === 'active' ? 'badge-success' : 'badge-neutral'}`}>
                      {school.status}
                    </span>
                  </div>
                </div>
              </div>
              <div className="mt-4 flex justify-end gap-2 border-t border-slate-100 pt-3">
                <button onClick={() => openEdit(school)} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-blue-600">
                  <Pencil size={16} />
                </button>
                <button onClick={() => handleDelete(school.id)} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-red-600">
                  <Trash2 size={16} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editing ? 'Edit School' : 'Add School'}
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
          <div>
            <label className="label-text">School Name</label>
            <input
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="input-field"
              placeholder="e.g. Delhi Public School"
            />
          </div>
          <div>
            <label className="label-text">Status</label>
            <select
              value={form.status}
              onChange={(e) => setForm({ ...form, status: e.target.value as 'active' | 'inactive' })}
              className="input-field"
            >
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </div>
          <button type="submit" disabled={saving} className="btn-primary w-full">
            {saving ? <Loader2 className="animate-spin" size={18} /> : editing ? 'Update School' : 'Add School'}
          </button>
        </form>
      </Modal>
    </div>
  );
}
