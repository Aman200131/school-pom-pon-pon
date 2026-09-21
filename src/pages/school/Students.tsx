import { useEffect, useState, type FormEvent } from 'react';
import { useAuth } from '@/context/AuthContext';
import { supabase } from '@/lib/supabase';
import { Modal } from '@/components/Modal';
import { EmptyState } from '@/components/EmptyState';
import { Plus, Users, Search, Loader2, Pencil, Trash2 } from 'lucide-react';
import type { Student } from '@/types/database';

export function StudentsPage() {
  const { profile } = useAuth();
  const schoolId = profile?.school_id;
  const [students, setStudents] = useState<Student[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Student | null>(null);
  const [form, setForm] = useState({ admission_number: '', name: '', class: '', section: '' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (schoolId) fetchStudents();
  }, [schoolId]);

  const fetchStudents = async () => {
    if (!schoolId) return;
    setLoading(true);
    const { data } = await supabase
      .from('students')
      .select('*')
      .eq('school_id', schoolId)
      .order('name')
      .limit(500);
    setStudents((data as Student[]) || []);
    setLoading(false);
  };

  const openAdd = () => {
    setEditing(null);
    setForm({ admission_number: '', name: '', class: '', section: '' });
    setError(null);
    setModalOpen(true);
  };

  const openEdit = (s: Student) => {
    setEditing(s);
    setForm({ admission_number: s.admission_number, name: s.name, class: s.class, section: s.section || '' });
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
        .from('students')
        .update({
          admission_number: form.admission_number,
          name: form.name,
          class: form.class,
          section: form.section || null,
        })
        .eq('id', editing.id);
      if (updateError) setError(updateError.message);
    } else {
      const { error: insertError } = await supabase.from('students').insert({
        school_id: schoolId,
        admission_number: form.admission_number,
        name: form.name,
        class: form.class,
        section: form.section || null,
      });
      if (insertError) setError(insertError.message);
    }

    setSaving(false);
    if (!error) {
      setModalOpen(false);
      fetchStudents();
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this student? This will also remove their fee and call records.')) return;
    await supabase.from('students').delete().eq('id', id);
    fetchStudents();
  };

  const filtered = students.filter(
    (s) =>
      s.name.toLowerCase().includes(search.toLowerCase()) ||
      s.admission_number.toLowerCase().includes(search.toLowerCase()) ||
      s.class.toLowerCase().includes(search.toLowerCase())
  );

  if (loading) {
    return <div className="flex h-full items-center justify-center py-20 text-slate-400">Loading...</div>;
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-slate-900">Students</h1>
        <button onClick={openAdd} className="btn-primary">
          <Plus size={18} />
          Add Student
        </button>
      </div>

      <div className="mb-4 relative max-w-sm">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
        <input
          type="text"
          placeholder="Search by name, ID, or class..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="input-field pl-10"
        />
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          icon={<Users size={32} />}
          title={search ? 'No students found' : 'No students yet'}
          description={search ? 'Try a different search term.' : 'Add students manually or import via Excel.'}
        />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-slate-500">
                <th className="px-4 py-3 font-medium">Admission No.</th>
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Class</th>
                <th className="px-4 py-3 font-medium">Section</th>
                <th className="px-4 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((s) => (
                <tr key={s.id} className="border-b border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-3 font-medium text-slate-900">{s.admission_number}</td>
                  <td className="px-4 py-3 text-slate-700">{s.name}</td>
                  <td className="px-4 py-3 text-slate-600">{s.class}</td>
                  <td className="px-4 py-3 text-slate-600">{s.section || '—'}</td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-2">
                      <button onClick={() => openEdit(s)} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-blue-600">
                        <Pencil size={16} />
                      </button>
                      <button onClick={() => handleDelete(s.id)} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-red-600">
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
        title={editing ? 'Edit Student' : 'Add Student'}
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
          )}
          <div>
            <label className="label-text">Admission Number</label>
            <input
              required
              value={form.admission_number}
              onChange={(e) => setForm({ ...form, admission_number: e.target.value })}
              className="input-field"
            />
          </div>
          <div>
            <label className="label-text">Name</label>
            <input
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="input-field"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label-text">Class</label>
              <input
                required
                value={form.class}
                onChange={(e) => setForm({ ...form, class: e.target.value })}
                className="input-field"
              />
            </div>
            <div>
              <label className="label-text">Section</label>
              <input
                value={form.section}
                onChange={(e) => setForm({ ...form, section: e.target.value })}
                className="input-field"
              />
            </div>
          </div>
          <button type="submit" disabled={saving} className="btn-primary w-full">
            {saving ? <Loader2 className="animate-spin" size={18} /> : editing ? 'Update' : 'Add Student'}
          </button>
        </form>
      </Modal>
    </div>
  );
}
