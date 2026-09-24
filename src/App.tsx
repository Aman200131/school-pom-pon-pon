import type { ReactNode } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from '@/context/AuthContext';
import { ProtectedRoute, OwnerRoute } from '@/components/ProtectedRoute';
import { AppLayout } from '@/components/AppLayout';
import { LoginPage } from '@/pages/auth/LoginPage';
import { SchoolDashboard } from '@/pages/school/Dashboard';
import { StudentsPage } from '@/pages/school/Students';
import { ParentsPage } from '@/pages/school/Parents';
import { FeesPage } from '@/pages/school/Fees';
import { CallsPage } from '@/pages/school/Calls';
import { ImportPage } from '@/pages/school/Import';
import { OwnerDashboard } from '@/pages/owner/Dashboard';
import { SchoolsPage } from '@/pages/owner/Schools';

// Derive the profile type from the existing AuthContext.
type Profile = ReturnType<typeof useAuth>['profile'];

function isOwner(profile: Profile): boolean {
  return profile?.role === 'owner';
}

// A valid school admin must have the existing "admin" role
// and must be assigned to a school.
function isSchoolAdmin(profile: Profile): boolean {
  return profile?.role === 'admin' && Boolean(profile.school_id);
}

function AccessDenied() {
  const { signOut } = useAuth();

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
      <div className="w-full max-w-md rounded-xl bg-white p-8 text-center shadow-sm">
        <h1 className="text-xl font-semibold text-slate-900">
          Account Not Assigned
        </h1>

        <p className="mt-2 text-sm text-slate-600">
          Your account has not been assigned to a school. Please contact your
          platform administrator.
        </p>

        <button
          type="button"
          onClick={() => {
            void signOut();
          }}
          className="mt-6 inline-flex h-10 items-center justify-center rounded-md border border-slate-300 px-4 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          Sign Out
        </button>
      </div>
    </div>
  );
}

// Only authenticated school admins with a valid school_id
// can access school-management pages.
function SchoolAdminRoute({ children }: { children: ReactNode }) {
  const { profile } = useAuth();

  if (!isSchoolAdmin(profile)) {
    return <Navigate to="/" replace />;
  }

  return <AppLayout>{children}</AppLayout>;
}

function HomeRoute() {
  const { profile } = useAuth();

  if (isOwner(profile)) {
    return (
      <AppLayout>
        <OwnerDashboard />
      </AppLayout>
    );
  }

  if (isSchoolAdmin(profile)) {
    return (
      <AppLayout>
        <SchoolDashboard />
      </AppLayout>
    );
  }

  return <AccessDenied />;
}

function AppRoutes() {
  return (
    <Routes>
      {/* Login */}
      <Route path="/login" element={<LoginPage />} />

      {/* Main dashboard */}
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <HomeRoute />
          </ProtectedRoute>
        }
      />

      {/* Owner-only */}
      <Route
        path="/schools"
        element={
          <OwnerRoute>
            <AppLayout>
              <SchoolsPage />
            </AppLayout>
          </OwnerRoute>
        }
      />

      {/* School admin-only */}
      <Route
        path="/students"
        element={
          <ProtectedRoute>
            <SchoolAdminRoute>
              <StudentsPage />
            </SchoolAdminRoute>
          </ProtectedRoute>
        }
      />

      <Route
        path="/parents"
        element={
          <ProtectedRoute>
            <SchoolAdminRoute>
              <ParentsPage />
            </SchoolAdminRoute>
          </ProtectedRoute>
        }
      />

      <Route
        path="/fees"
        element={
          <ProtectedRoute>
            <SchoolAdminRoute>
              <FeesPage />
            </SchoolAdminRoute>
          </ProtectedRoute>
        }
      />

      <Route
        path="/calls"
        element={
          <ProtectedRoute>
            <SchoolAdminRoute>
              <CallsPage />
            </SchoolAdminRoute>
          </ProtectedRoute>
        }
      />

      <Route
        path="/import"
        element={
          <ProtectedRoute>
            <SchoolAdminRoute>
              <ImportPage />
            </SchoolAdminRoute>
          </ProtectedRoute>
        }
      />

      {/* Unknown routes */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  );
}
