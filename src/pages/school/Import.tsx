import { useState, useRef } from 'react';
import { useAuth } from '@/context/AuthContext';
import { supabase } from '@/lib/supabase';
import * as XLSX from 'xlsx';
import {
  Upload,
  Download,
  FileSpreadsheet,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Loader2,
  ArrowRight,
} from 'lucide-react';
import type { Student, Parent, Fee } from '@/types/database';

interface ImportRow {
  rowIndex: number;
  studentId: string;
  studentName: string;
  class: string;
  section: string;
  parentName: string;
  parentPhone: string;
  monthlyFee: string;
  dueDate: string;
  paymentStatus: string;
  feeMonth: string;
  errors: string[];
}

type ImportPhase = 'upload' | 'preview' | 'importing' | 'done';

export function ImportPage() {
  const { profile } = useAuth();
  const schoolId = profile?.school_id;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [phase, setPhase] = useState<ImportPhase>('upload');
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [fileName, setFileName] = useState('');
  const [importResult, setImportResult] = useState<{ students: number; parents: number; fees: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const templateColumns = [
    'Student ID',
    'Student Name',
    'Class',
    'Section',
    'Parent Name',
    'Parent Phone',
    'Monthly Fee',
    'Due Date',
    'Payment Status',
    'Fee Month',
  ];

  const downloadTemplate = () => {
    const sampleRow = {
      'Student ID': 'STU001',
      'Student Name': 'Rahul Sharma',
      'Class': '10',
      'Section': 'A',
      'Parent Name': 'Rajesh Sharma',
      'Parent Phone': '+919876543210',
      'Monthly Fee': '5000',
      'Due Date': '2026-09-15',
      'Payment Status': 'unpaid',
      'Fee Month': '2026-09',
    };
    const ws = XLSX.utils.json_to_sheet([sampleRow]);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Students');
    XLSX.writeFile(wb, 'school_fee_import_template.xlsx');
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !schoolId) return;

    setError(null);
    setFileName(file.name);

    try {
      const buffer = await file.arrayBuffer();
      const wb = XLSX.read(buffer, { type: 'array' });
      const ws = wb.Sheets[wb.SheetNames[0]];
      const jsonData = XLSX.utils.sheet_to_json<Record<string, unknown>>(ws);

      if (jsonData.length === 0) {
        setError('The Excel file is empty.');
        return;
      }

      // Validate each row
      const parsedRows: ImportRow[] = [];
      const seenStudentIds = new Set<string>();
      const seenFeeKeys = new Set<string>();

      // Fetch existing student IDs and fee keys for this school to detect duplicates
      const [existingStudents, existingFees] = await Promise.all([
        supabase.from('students').select('admission_number').eq('school_id', schoolId),
        supabase.from('fees').select('id, student_id, fee_month').eq('school_id', schoolId),
      ]);

      const dbStudentIds = new Set((existingStudents.data || []).map((s) => (s as { admission_number: string }).admission_number));
      const dbFeeKeys = new Set();
      (existingFees.data || []).forEach((f) => {
        const fee = f as { student_id: string; fee_month: string };
        // Map by student+month — we need to cross-reference with admission numbers later
        dbFeeKeys.add(`${fee.student_id}_${fee.fee_month}`);
      });

      // Build student admission_number -> id map for fee duplicate check
      const studentIdMap = new Map<string, string>();
      (existingStudents.data || []).forEach((s) => {
        const student = s as { id?: string; admission_number: string };
        if (student.id) studentIdMap.set(student.admission_number, student.id);
      });

      jsonData.forEach((raw, index) => {
        const row: ImportRow = {
          rowIndex: index + 2, // +2 because Excel row 1 is headers
          studentId: String(raw['Student ID'] || '').trim(),
          studentName: String(raw['Student Name'] || '').trim(),
          class: String(raw['Class'] || '').trim(),
          section: String(raw['Section'] || '').trim(),
          parentName: String(raw['Parent Name'] || '').trim(),
          parentPhone: String(raw['Parent Phone'] || '').trim(),
          monthlyFee: String(raw['Monthly Fee'] || '').trim(),
          dueDate: String(raw['Due Date'] || '').trim(),
          paymentStatus: String(raw['Payment Status'] || 'unpaid').trim().toLowerCase(),
          feeMonth: String(raw['Fee Month'] || '').trim(),
          errors: [],
        };

        // Required fields
        if (!row.studentId) row.errors.push('Student ID is required');
        if (!row.studentName) row.errors.push('Student Name is required');
        if (!row.class) row.errors.push('Class is required');
        if (!row.parentName) row.errors.push('Parent Name is required');
        if (!row.parentPhone) row.errors.push('Parent Phone is required');
        if (!row.monthlyFee) row.errors.push('Monthly Fee is required');
        if (!row.dueDate) row.errors.push('Due Date is required');
        if (!row.feeMonth) row.errors.push('Fee Month is required');

        // Phone validation (basic: digits, +, spaces, min 10 digits)
        const phoneDigits = row.parentPhone.replace(/\D/g, '');
        if (row.parentPhone && phoneDigits.length < 10) {
          row.errors.push('Invalid phone number (must be at least 10 digits)');
        }

        // Amount validation
        if (row.monthlyFee) {
          const amount = parseFloat(row.monthlyFee);
          if (isNaN(amount) || amount < 0) {
            row.errors.push('Invalid fee amount');
          }
        }

        // Date validation
        if (row.dueDate) {
          const date = new Date(row.dueDate);
          if (isNaN(date.getTime())) {
            row.errors.push('Invalid due date');
          }
        }

        // Payment status validation
        if (row.paymentStatus && !['paid', 'unpaid'].includes(row.paymentStatus)) {
          row.errors.push('Payment Status must be "paid" or "unpaid"');
        }

        // Duplicate Student ID within file
        if (row.studentId) {
          if (seenStudentIds.has(row.studentId)) {
            row.errors.push('Duplicate Student ID within file');
          } else {
            seenStudentIds.add(row.studentId);
          }

          // Duplicate Student ID in database
          if (dbStudentIds.has(row.studentId)) {
            row.errors.push('Student ID already exists in database');
          }
        }

        // Duplicate school + student + fee month
        if (row.studentId && row.feeMonth) {
          const fileKey = `${row.studentId}_${row.feeMonth}`;
          if (seenFeeKeys.has(fileKey)) {
            row.errors.push('Duplicate Student + Fee Month within file');
          } else {
            seenFeeKeys.add(fileKey);
          }

          // Check database
          const dbStudentId = studentIdMap.get(row.studentId);
          if (dbStudentId && dbFeeKeys.has(`${dbStudentId}_${row.feeMonth}`)) {
            row.errors.push('Fee for this student + month already exists');
          }
        }

        parsedRows.push(row);
      });

      setRows(parsedRows);
      setPhase('preview');
    } catch (err) {
      setError(`Failed to read file: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  const validRows = rows.filter((r) => r.errors.length === 0);
  const invalidRows = rows.filter((r) => r.errors.length > 0);

  const confirmImport = async () => {
    if (!schoolId) return;
    setPhase('importing');
    setError(null);

    try {
      let studentsCreated = 0;
      let parentsCreated = 0;
      let feesCreated = 0;

      // Group rows by student to avoid creating duplicate students
      const studentMap = new Map<string, { admission_number: string; name: string; class: string; section: string | null }>();
      const parentMap = new Map<string, { name: string; phone: string; relationship: string | null }>();

      for (const row of validRows) {
        if (!studentMap.has(row.studentId)) {
          studentMap.set(row.studentId, {
            admission_number: row.studentId,
            name: row.studentName,
            class: row.class,
            section: row.section || null,
          });
        }
        // Key parents by phone+name to avoid duplicates
        const parentKey = `${row.parentPhone}_${row.parentName}`;
        if (!parentMap.has(parentKey)) {
          parentMap.set(parentKey, {
            name: row.parentName,
            phone: row.parentPhone,
            relationship: null,
          });
        }
      }

      // Insert students
      const studentRows = Array.from(studentMap.values()).map((s) => ({ ...s, school_id: schoolId }));
      const { data: insertedStudents, error: studentError } = await supabase
        .from('students')
        .insert(studentRows)
        .select('id, admission_number');
      if (studentError) throw new Error(`Student insert failed: ${studentError.message}`);
      studentsCreated = insertedStudents?.length || 0;

      // Map admission_number -> student id
      const studentIdByAdmission = new Map<string, string>();
      (insertedStudents || []).forEach((s) => {
        const student = s as { id: string; admission_number: string };
        studentIdByAdmission.set(student.admission_number, student.id);
      });

      // Insert parents
      const parentRows = Array.from(parentMap.values()).map((p) => ({ ...p, school_id: schoolId }));
      const { data: insertedParents, error: parentError } = await supabase
        .from('parents')
        .insert(parentRows)
        .select('id, phone, name');
      if (parentError) throw new Error(`Parent insert failed: ${parentError.message}`);
      parentsCreated = insertedParents?.length || 0;

      // Map parent by phone+name -> parent id
      const parentIdByKey = new Map<string, string>();
      (insertedParents || []).forEach((p) => {
        const parent = p as { id: string; phone: string; name: string };
        parentIdByKey.set(`${parent.phone}_${parent.name}`, parent.id);
      });

      // Insert student_parents and fees
      const studentParentRows: { student_id: string; parent_id: string; school_id: string }[] = [];
      const feeRows: {
        school_id: string;
        student_id: string;
        fee_month: string;
        amount: number;
        due_date: string;
        status: 'paid' | 'unpaid';
        payment_date: string | null;
      }[] = [];

      const seenLinks = new Set<string>();

      for (const row of validRows) {
        const studentId = studentIdByAdmission.get(row.studentId);
        const parentKey = `${row.parentPhone}_${row.parentName}`;
        const parentId = parentIdByKey.get(parentKey);

        if (studentId && parentId) {
          const linkKey = `${studentId}_${parentId}`;
          if (!seenLinks.has(linkKey)) {
            seenLinks.add(linkKey);
            studentParentRows.push({ student_id: studentId, parent_id: parentId, school_id: schoolId });
          }

          feeRows.push({
            school_id: schoolId,
            student_id: studentId,
            fee_month: row.feeMonth,
            amount: parseFloat(row.monthlyFee),
            due_date: row.dueDate,
            status: row.paymentStatus === 'paid' ? 'paid' : 'unpaid',
            payment_date: row.paymentStatus === 'paid' ? new Date().toISOString().split('T')[0] : null,
          });
        }
      }

      if (studentParentRows.length > 0) {
        const { error: linkError } = await supabase.from('student_parents').insert(studentParentRows);
        if (linkError) console.error('student_parents insert error:', linkError.message);
      }

      if (feeRows.length > 0) {
        const { data: insertedFees, error: feeError } = await supabase
          .from('fees')
          .insert(feeRows)
          .select('id');
        if (feeError) throw new Error(`Fee insert failed: ${feeError.message}`);
        feesCreated = insertedFees?.length || 0;
      }

      setImportResult({ students: studentsCreated, parents: parentsCreated, fees: feesCreated });
      setPhase('done');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed');
      setPhase('preview');
    }
  };

  const reset = () => {
    setPhase('upload');
    setRows([]);
    setFileName('');
    setError(null);
    setImportResult(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  if (phase === 'done') {
    return (
      <div>
        <h1 className="mb-6 text-2xl font-bold text-slate-900">Import Complete</h1>
        <div className="card mx-auto max-w-lg p-8 text-center">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-emerald-100">
            <CheckCircle2 className="text-emerald-600" size={32} />
          </div>
          <h2 className="text-lg font-semibold text-slate-900">Import Successful</h2>
          <div className="mt-6 space-y-2 text-sm">
            <p className="text-slate-700">{importResult?.students} students created</p>
            <p className="text-slate-700">{importResult?.parents} parents created</p>
            <p className="text-slate-700">{importResult?.fees} fee records created</p>
          </div>
          <p className="mt-4 text-xs text-slate-500">
            The reminder engine will automatically schedule calls for unpaid fees based on due dates.
          </p>
          <div className="mt-6 flex justify-center gap-3">
            <button onClick={reset} className="btn-secondary">Import Another File</button>
          </div>
        </div>
      </div>
    );
  }

  if (phase === 'importing') {
    return (
      <div>
        <h1 className="mb-6 text-2xl font-bold text-slate-900">Importing...</h1>
        <div className="card mx-auto max-w-lg p-8 text-center">
          <Loader2 className="mx-auto animate-spin text-blue-600" size={32} />
          <p className="mt-4 text-sm text-slate-600">Creating students, parents, and fee records...</p>
        </div>
      </div>
    );
  }

  if (phase === 'preview') {
    return (
      <div>
        <h1 className="mb-6 text-2xl font-bold text-slate-900">Preview Import</h1>
        <p className="mb-4 text-sm text-slate-600">
          File: <span className="font-medium text-slate-900">{fileName}</span> — {rows.length} rows found
        </p>

        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
            <XCircle size={18} />
            {error}
          </div>
        )}

        <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="card p-4">
            <div className="flex items-center gap-2">
              <FileSpreadsheet className="text-slate-400" size={20} />
              <span className="text-sm text-slate-500">Total Rows</span>
            </div>
            <p className="mt-1 text-2xl font-bold text-slate-900">{rows.length}</p>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="text-emerald-500" size={20} />
              <span className="text-sm text-slate-500">Valid Rows</span>
            </div>
            <p className="mt-1 text-2xl font-bold text-emerald-600">{validRows.length}</p>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-2">
              <AlertTriangle className="text-red-500" size={20} />
              <span className="text-sm text-slate-500">Rows with Errors</span>
            </div>
            <p className="mt-1 text-2xl font-bold text-red-600">{invalidRows.length}</p>
          </div>
        </div>

        {invalidRows.length > 0 && (
          <div className="card mb-6 overflow-x-auto">
            <div className="border-b border-slate-200 px-4 py-3">
              <h2 className="text-sm font-semibold text-red-700">Rows with Validation Errors</h2>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-slate-500">
                  <th className="px-4 py-2 font-medium">Row</th>
                  <th className="px-4 py-2 font-medium">Student ID</th>
                  <th className="px-4 py-2 font-medium">Student Name</th>
                  <th className="px-4 py-2 font-medium">Errors</th>
                </tr>
              </thead>
              <tbody>
                {invalidRows.map((row) => (
                  <tr key={row.rowIndex} className="border-b border-slate-100">
                    <td className="px-4 py-2 text-slate-500">{row.rowIndex}</td>
                    <td className="px-4 py-2 font-medium text-slate-900">{row.studentId || '—'}</td>
                    <td className="px-4 py-2 text-slate-600">{row.studentName || '—'}</td>
                    <td className="px-4 py-2">
                      <ul className="list-inside list-disc text-xs text-red-600">
                        {row.errors.map((err, i) => (
                          <li key={i}>{err}</li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {validRows.length > 0 && (
          <div className="card mb-6 overflow-x-auto">
            <div className="border-b border-slate-200 px-4 py-3">
              <h2 className="text-sm font-semibold text-emerald-700">Valid Rows (Ready to Import)</h2>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-slate-500">
                  <th className="px-4 py-2 font-medium">Student ID</th>
                  <th className="px-4 py-2 font-medium">Name</th>
                  <th className="px-4 py-2 font-medium">Class</th>
                  <th className="px-4 py-2 font-medium">Parent</th>
                  <th className="px-4 py-2 font-medium">Phone</th>
                  <th className="px-4 py-2 font-medium">Fee</th>
                  <th className="px-4 py-2 font-medium">Due Date</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {validRows.slice(0, 50).map((row) => (
                  <tr key={row.rowIndex} className="border-b border-slate-100">
                    <td className="px-4 py-2 font-medium text-slate-900">{row.studentId}</td>
                    <td className="px-4 py-2 text-slate-600">{row.studentName}</td>
                    <td className="px-4 py-2 text-slate-600">{row.class}</td>
                    <td className="px-4 py-2 text-slate-600">{row.parentName}</td>
                    <td className="px-4 py-2 text-slate-600">{row.parentPhone}</td>
                    <td className="px-4 py-2 text-slate-600">₹{row.monthlyFee}</td>
                    <td className="px-4 py-2 text-slate-600">{row.dueDate}</td>
                    <td className="px-4 py-2">
                      <span className={`badge ${row.paymentStatus === 'paid' ? 'badge-success' : 'badge-warning'}`}>
                        {row.paymentStatus}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {validRows.length > 50 && (
              <p className="px-4 py-2 text-xs text-slate-500">
                Showing first 50 of {validRows.length} valid rows.
              </p>
            )}
          </div>
        )}

        <div className="flex gap-3">
          <button onClick={reset} className="btn-secondary">Cancel</button>
          <button
            onClick={confirmImport}
            disabled={validRows.length === 0}
            className="btn-primary"
          >
            Confirm Import
            <ArrowRight size={18} />
          </button>
        </div>
      </div>
    );
  }

  // Upload phase
  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-slate-900">Import Students & Fees</h1>

      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          <XCircle size={18} />
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Step 1: Download template */}
        <div className="card p-6">
          <div className="mb-4 flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-100">
              <span className="font-bold text-blue-600">1</span>
            </div>
            <h2 className="text-lg font-semibold text-slate-900">Download Template</h2>
          </div>
          <p className="mb-4 text-sm text-slate-600">
            Download the Excel template with the correct column headers. Fill in your student, parent, and fee data.
          </p>
          <div className="mb-4 rounded-lg bg-slate-50 p-3">
            <p className="mb-2 text-xs font-medium text-slate-500">Template columns:</p>
            <div className="flex flex-wrap gap-1.5">
              {templateColumns.map((col) => (
                <span key={col} className="badge badge-neutral">{col}</span>
              ))}
            </div>
          </div>
          <button onClick={downloadTemplate} className="btn-secondary">
            <Download size={18} />
            Download Template
          </button>
        </div>

        {/* Step 2: Upload */}
        <div className="card p-6">
          <div className="mb-4 flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-100">
              <span className="font-bold text-blue-600">2</span>
            </div>
            <h2 className="text-lg font-semibold text-slate-900">Upload Excel File</h2>
          </div>
          <p className="mb-4 text-sm text-slate-600">
            Upload your filled Excel file. We'll validate all rows and show a preview before importing.
          </p>
          <div
            className="mb-4 rounded-xl border-2 border-dashed border-slate-300 p-8 text-center transition-colors hover:border-blue-400"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const file = e.dataTransfer.files[0];
              if (file) {
                if (fileInputRef.current) {
                  Object.defineProperty(fileInputRef.current, 'files', { value: [file] });
                }
                handleFileUpload({ target: { files: [file] } } as unknown as React.ChangeEvent<HTMLInputElement>);
              }
            }}
          >
            <Upload className="mx-auto mb-3 text-slate-400" size={32} />
            <p className="text-sm text-slate-600">
              Drag &amp; drop your Excel file here, or
            </p>
            <label className="mt-2 inline-block">
              <span className="btn-primary cursor-pointer">Browse Files</span>
              <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx,.xls"
                className="hidden"
                onChange={handleFileUpload}
              />
            </label>
            {fileName && (
              <p className="mt-3 text-xs font-medium text-slate-700">{fileName}</p>
            )}
          </div>
        </div>
      </div>

      {/* Step 3: Info */}
      <div className="mt-6 card p-6">
        <h3 className="mb-3 text-sm font-semibold text-slate-900">Validation Rules</h3>
        <ul className="space-y-1.5 text-sm text-slate-600">
          <li className="flex items-start gap-2"><CheckCircle2 size={16} className="mt-0.5 text-emerald-500 shrink-0" /> Required fields: Student ID, Student Name, Class, Parent Name, Parent Phone, Monthly Fee, Due Date, Fee Month</li>
          <li className="flex items-start gap-2"><CheckCircle2 size={16} className="mt-0.5 text-emerald-500 shrink-0" /> Phone numbers must be at least 10 digits</li>
          <li className="flex items-start gap-2"><CheckCircle2 size={16} className="mt-0.5 text-emerald-500 shrink-0" /> Fee amounts must be valid positive numbers</li>
          <li className="flex items-start gap-2"><CheckCircle2 size={16} className="mt-0.5 text-emerald-500 shrink-0" /> Due dates must be valid dates (YYYY-MM-DD format)</li>
          <li className="flex items-start gap-2"><CheckCircle2 size={16} className="mt-0.5 text-emerald-500 shrink-0" /> Payment Status must be "paid" or "unpaid" (defaults to unpaid)</li>
          <li className="flex items-start gap-2"><CheckCircle2 size={16} className="mt-0.5 text-emerald-500 shrink-0" /> Duplicate Student IDs (within file or database) are rejected</li>
          <li className="flex items-start gap-2"><CheckCircle2 size={16} className="mt-0.5 text-emerald-500 shrink-0" /> Duplicate Student + Fee Month combinations are rejected</li>
        </ul>
      </div>
    </div>
  );
}
