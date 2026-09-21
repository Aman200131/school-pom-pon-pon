import { useEffect, useState } from 'react';
import { supabase } from '@/lib/supabase';
import { StatCard } from '@/components/StatCard';
import { Building2, School as SchoolIcon, Users } from 'lucide-react';
import type { School } from '@/types/database';

interface SchoolWithStats extends School {
  student_count: number;
}

export function OwnerDashboard() {
  const [schools, setSchools] = useState<SchoolWithStats[]>([]);
  const [totalStudents, setTotalStudents] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    setLoading(true);

    const { data: schoolsData } = await supabase
      .from('schools')
      .select('*')
      .order('created_at', { ascending: false });

    const schoolsList = (schoolsData as School[]) || [];
    const activeCount = schoolsList.filter((s) => s.status === 'active').length;

    // Fetch student counts per school
    const schoolsWithStats: SchoolWithStats[] = [];
    let totalStu = 0;

    for (const school of schoolsList) {
      const { count } = await supabase
        .from('students')
        .select('id', { count: 'exact', head: true })
        .eq('school_id', school.id);

      const sc = count || 0;
      totalStu += sc;
      schoolsWithStats.push({ ...school, student_count: sc });
    }

    setSchools(schoolsWithStats);
    setTotalStudents(totalStu);
    setLoading(false);
  };

  if (loading) {
    return <div className="flex h-full items-center justify-center py-20 text-slate-400">Loading...</div>;
  }

  const activeCount = schools.filter((s) => s.status === 'active').length;

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-slate-900">Platform Dashboard</h1>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Total Schools" value={schools.length} icon={<Building2 size={24} />} tone="info" />
        <StatCard label="Active Schools" value={activeCount} icon={<SchoolIcon size={24} />} tone="success" />
        <StatCard label="Total Students" value={totalStudents} icon={<Users size={24} />} tone="default" />
      </div>

      <div className="mt-8">
        <h2 className="mb-4 text-lg font-semibold text-slate-900">School Overview</h2>
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-slate-500">
                <th className="px-4 py-3 font-medium">School Name</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Students</th>
                <th className="px-4 py-3 font-medium">Created</th>
              </tr>
            </thead>
            <tbody>
              {schools.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-4 py-8 text-center text-slate-500">
                    No schools yet. Add schools from the Schools page.
                  </td>
                </tr>
              ) : (
                schools.map((school) => (
                  <tr key={school.id} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="px-4 py-3 font-medium text-slate-900">{school.name}</td>
                    <td className="px-4 py-3">
                      <span className={`badge ${school.status === 'active' ? 'badge-success' : 'badge-neutral'}`}>
                        {school.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{school.student_count}</td>
                    <td className="px-4 py-3 text-slate-500 text-xs">
                      {new Date(school.created_at).toLocaleDateString('en-IN', { dateStyle: 'medium' })}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
