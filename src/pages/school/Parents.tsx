import { useEffect, useState, type FormEvent } from 'react';
import { useAuth } from '@/context/AuthContext';
import { supabase } from '@/lib/supabase';
import { Modal } from '@/components/Modal';
import { EmptyState } from '@/components/EmptyState';
import { Plus, UserCircle, Search, Loader2, Pencil, Trash2 } from 'lucide-react';
import type { Parent } from '@/types/database';

export function ParentsPage() {
  const { profile } = useAuth();
  const schoolId = profile?.school_id;
  const [parents, setParents] = useState<(Parent & { student_count?: number })[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Parent | null>(null);
  const [form, setForm] = useState({ name: '', phone: '', relationship: '' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (schoolId) fetchParents();
  }, [schoolId]);

  const fetchParents = async () => {
    if (!schoolId) return;
    setLoading(true);
    const { data } = await supabase
      .from('parents')
      .select('*')
      .eq('school_id', schoolId)
      .order('name')
      .limit(500);
    setParents((data as Parent[]) || []);
    setLoading(false);
  };

  const openAdd = () => {
    setEditing(null);
    setForm({ name: '', phone: '', relationship: '' });
    setError(null);
    setModalOpen(true);
  };

  const openEdit = (p: Parent) => {
    setEditing(p);
    setForm({ name: p.name, phone: p.phone, relationship: p.relationship || '' });
    setError(null);
    setModalOpen(true);
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!schoolId) return;
    setSaving(true);
    setError(null);

    if (editing) {
      const { error: updateError } = await supabase
        .from('parents')
        .update({ name: form.name, phone: form.phone, relationship: form.relationship || null })
        .eq('id', editing.id);
      if (updateError) setError(updateError.message);
    } else {
      const { error: insertError } = await supabase.from('parents').insert({
        school_id: schoolId,
        name: form.name,
        phone: form.phone,
        relationship: form.relationship || null,
      });
      if (insertError) setError(insertError.message);
    }

    setSaving(false);
    if (!error) {
      setModalOpen(false);
      fetchParents();
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this parent?')) return;
    await supabase.from('parents').delete().eq('id', id);
    fetchParents();
  };

  const filtered = parents.filter(
    (p) =>
      p.name.toLowerCase().includes(search.toLowerCase()) ||
      p.phone.includes(search)
  );

  if (loading) {
    return <div className="flex h-full items-center justify-center py-20 text-slate-400">Loading...</div>;
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-slate-900">Parents</h1>
        <button onClick={openAdd} className="btn-primary">
          <Plus size={18} />
          Add Parent
        </button>
      </div>

      <div className="mb-4 relative max-w-sm">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
        <input
          type="text"
          placeholder="Search by name or phone..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="input-field pl-10"
        />
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          icon={<UserCircle size={32} />}
          title={search ? 'No parents found' : 'No parents yet'}
          description={search ? 'Try a different search.' : 'Add parents manually or import via Excel.'}
        />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-slate-500">
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Phone</th>
                <th className="px-4 py-3 font-medium">Relationship</th>
                <th className="px-4 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((p) => (
                <tr key={p.id} className="border-b border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-3 font-medium text-slate-900">{p.name}</td>
                  <td className="px-4 py-3 text-slate-600">{p.phone}</td>
                  <td className="px-4 py-3 text-slate-600">{p.relationship || '—'}</td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-2">
                      <button onClick={() => openEdit(p)} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-blue-600">
                        <Pencil size={16} />
                      </button>
                      <button onClick={() => handleDelete(p.id)} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-red-600">
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editing ? 'Edit Parent' : 'Add Parent'}
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
          <div>
            <label className="label-text">Name</label>
            <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="input-field" />
          </div>
          <div>
            <label className="label-text">Phone</label>
            <input required value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} className="input-field" placeholder="+91XXXXXXXXXX" />
          </div>
          <div>
            <label className="label-text">Relationship</label>
            <input value={form.relationship} onChange={(e) => setForm({ ...form, relationship: e.target.value })} className="input-field" placeholder="Father, Mother, Guardian..." />
          </div>
          <button type="submit" disabled={saving} className="btn-primary w-full">
            {saving ? <Loader2 className="animate-spin" size={18} /> : editing ? 'Update' : 'Add Parent'}
          </button>
        </form>
      </Modal>
    </div>
  );
}
