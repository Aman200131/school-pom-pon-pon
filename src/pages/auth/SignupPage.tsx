import { useState, type FormEvent } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { supabase } from '@/lib/supabase';
import { GraduationCap, Loader2, AlertCircle, CheckCircle2 } from 'lucide-react';
import type { School } from '@/types/database';

export function SignupPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState<'select' | 'register'>('select');
  const [role, setRole] = useState<'admin' | 'owner'>('admin');
  const [selectedSchoolId, setSelectedSchoolId] = useState('');
  const [schools, setSchools] = useState<School[]>([]);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSchools = async () => {
    const { data, error: queryError } = await supabase
      .from('schools')
      .select('*')
      .eq('status', 'active')
      .order('name');

    if (queryError) {
      setError(queryError.message);
      return;
    }
    setSchools((data as School[]) || []);
  };

  const handleRoleSelect = (r: 'admin' | 'owner') => {
    setRole(r);
    if (r === 'admin') {
      loadSchools();
    }
    setStep('register');
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);

    if (role === 'admin' && !selectedSchoolId) {
      setError('Please select a school.');
      return;
    }

    if (password.length < 6) {
      setError('Password must be at least 6 characters.');
      return;
    }

    setLoading(true);

    const { data: authData, error: signUpError } = await supabase.auth.signUp({
      email,
      password,
      options: { data: { full_name: fullName } },
    });

    if (signUpError) {
      setError(signUpError.message);
      setLoading(false);
      return;
    }

    if (authData.user) {
      // Update profile with role and school_id
      const updates: Record<string, string> = { role };
      if (role === 'admin') {
        updates.school_id = selectedSchoolId;
      }

      const { error: profileError } = await supabase
        .from('profiles')
        .update(updates)
        .eq('id', authData.user.id);

      if (profileError) {
        setError('Account created but profile setup failed: ' + profileError.message);
        setLoading(false);
        return;
      }
    }

    navigate('/');
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-900 via-blue-900 to-slate-800 p-4">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-blue-600 shadow-lg">
            <GraduationCap className="text-white" size={32} />
          </div>
          <h1 className="text-2xl font-bold text-white">Create Account</h1>
          <p className="mt-1 text-sm text-blue-200">Join the School Fee Reminder CRM</p>
        </div>

        <div className="card p-8">
          {step === 'select' ? (
            <div className="space-y-4">
              <h2 className="text-lg font-semibold text-slate-900">Choose your role</h2>
              <button
                onClick={() => handleRoleSelect('admin')}
                className="flex w-full items-center gap-4 rounded-xl border-2 border-slate-200 p-4 text-left transition-all hover:border-blue-500 hover:bg-blue-50"
              >
                <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-blue-100">
                  <GraduationCap className="text-blue-600" size={24} />
                </div>
                <div>
                  <p className="font-semibold text-slate-900">School Admin</p>
                  <p className="text-sm text-slate-500">Manage students, fees & reminders for your school</p>
                </div>
              </button>

              <button
                onClick={() => handleRoleSelect('owner')}
                className="flex w-full items-center gap-4 rounded-xl border-2 border-slate-200 p-4 text-left transition-all hover:border-blue-500 hover:bg-blue-50"
              >
                <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-slate-100">
                  <CheckCircle2 className="text-slate-600" size={24} />
                </div>
                <div>
                  <p className="font-semibold text-slate-900">Platform Owner</p>
                  <p className="text-sm text-slate-500">Manage all schools on the platform</p>
                </div>
              </button>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-5">
              {error && (
                <div className="flex items-center gap-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
                  <AlertCircle size={18} />
                  {error}
                </div>
              )}

              <div>
                <label className="label-text">Role</label>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => { setRole('admin'); loadSchools(); }}
                    className={`flex-1 rounded-lg border px-3 py-2 text-sm font-medium ${role === 'admin' ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-slate-300 text-slate-600'}`}
                  >
                    School Admin
                  </button>
                  <button
                    type="button"
                    onClick={() => setRole('owner')}
                    className={`flex-1 rounded-lg border px-3 py-2 text-sm font-medium ${role === 'owner' ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-slate-300 text-slate-600'}`}
                  >
                    Platform Owner
                  </button>
                </div>
              </div>

              {role === 'admin' && (
                <div>
                  <label className="label-text" htmlFor="school">School</label>
                  <select
                    id="school"
                    value={selectedSchoolId}
                    onChange={(e) => setSelectedSchoolId(e.target.value)}
                    className="input-field"
                    required
                  >
                    <option value="">Select your school...</option>
                    {schools.map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                  {schools.length === 0 && (
                    <p className="mt-1 text-xs text-amber-600">
                      No active schools yet. Ask the platform owner to add your school first.
                    </p>
                  )}
                </div>
              )}

              <div>
                <label className="label-text" htmlFor="name">Full Name</label>
                <input
                  id="name"
                  type="text"
                  required
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  className="input-field"
                  placeholder="John Doe"
                />
              </div>

              <div>
                <label className="label-text" htmlFor="email">Email</label>
                <input
                  id="email"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="input-field"
                  placeholder="you@school.com"
                />
              </div>

              <div>
                <label className="label-text" htmlFor="password">Password</label>
                <input
                  id="password"
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="input-field"
                  placeholder="At least 6 characters"
                />
              </div>

              <button type="submit" disabled={loading} className="btn-primary w-full">
                {loading ? <Loader2 className="animate-spin" size={18} /> : 'Create Account'}
              </button>
            </form>
          )}

          <p className="mt-6 text-center text-sm text-slate-600">
            Already have an account?{' '}
            <Link to="/login" className="font-medium text-blue-600 hover:text-blue-700">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
