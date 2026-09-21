export type Role = 'owner' | 'admin';

export type SchoolStatus = 'active' | 'inactive';

export type FeeStatus = 'paid' | 'unpaid';

export type ReminderType = '7d' | '3d' | '2d' | '1d' | 'overdue';

export type CallStatus = 'scheduled' | 'answered' | 'no_answer' | 'busy' | 'failed';

export interface School {
  id: string;
  name: string;
  status: SchoolStatus;
  created_at: string;
  updated_at: string;
}

export interface Profile {
  id: string;
  role: Role;
  school_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Student {
  id: string;
  school_id: string;
  admission_number: string;
  name: string;
  class: string;
  section: string | null;
  created_at: string;
  updated_at: string;
}

export interface Parent {
  id: string;
  school_id: string;
  name: string;
  phone: string;
  relationship: string | null;
  created_at: string;
  updated_at: string;
}

export interface StudentParent {
  student_id: string;
  parent_id: string;
  school_id: string;
}

export interface Fee {
  id: string;
  school_id: string;
  student_id: string;
  fee_month: string;
  amount: number;
  due_date: string;
  status: FeeStatus;
  payment_date: string | null;
  created_at: string;
  updated_at: string;
}

export interface CallRecord {
  id: string;
  school_id: string;
  student_id: string;
  parent_id: string;
  fee_id: string;
  reminder_type: ReminderType;
  scheduled_at: string;
  called_at: string | null;
  status: CallStatus;
  provider_call_id: string | null;
  created_at: string;
}

export interface FeeWithStudent extends Fee {
  students: Pick<Student, 'name' | 'admission_number' | 'class' | 'section'>;
}

export interface CallWithDetails extends CallRecord {
  students: Pick<Student, 'name' | 'admission_number'>;
  parents: Pick<Parent, 'name' | 'phone'>;
  fees: Pick<Fee, 'fee_month' | 'amount' | 'due_date'>;
}
