import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from '@/context/AuthContext';
import { ProtectedRoute, OwnerRoute } from '@/components/ProtectedRoute';
import { AppLayout } from '@/components/AppLayout';
import { LoginPage } from '@/pages/auth/LoginPage';
import { SignupPage } from '@/pages/auth/SignupPage';
import { SchoolDashboard } from '@/pages/school/Dashboard';
import { StudentsPage } from '@/pages/school/Students';
import { ParentsPage } from '@/pages/school/Parents';
import { FeesPage } from '@/pages/school/Fees';
import { CallsPage } from '@/pages/school/Calls';
import { ImportPage } from '@/pages/school/Import';
import { OwnerDashboard } from '@/pages/owner/Dashboard';
import { SchoolsPage } from '@/pages/owner/Schools';

function AppRoutes() {
  const { profile } = useAuth();
  const isOwner = profile?.role === 'owner';

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />

      <Route
        path="/"
        element={
          <ProtectedRoute>
            <AppLayout>
              {isOwner ? <OwnerDashboard /> : <SchoolDashboard />}
            </AppLayout>
          </ProtectedRoute>
        }
      />

      {/* Owner-only routes */}
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

      {/* School admin routes — owners also blocked from these */}
      <Route
        path="/students"
        element={
          <ProtectedRoute>
            <AppLayout>
              {isOwner ? <Navigate to="/" replace /> : <StudentsPage />}
            </AppLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/parents"
        element={
          <ProtectedRoute>
            <AppLayout>
              {isOwner ? <Navigate to="/" replace /> : <ParentsPage />}
            </AppLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/fees"
        element={
          <ProtectedRoute>
            <AppLayout>
              {isOwner ? <Navigate to="/" replace /> : <FeesPage />}
            </AppLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/calls"
        element={
          <ProtectedRoute>
            <AppLayout>
              {isOwner ? <Navigate to="/" replace /> : <CallsPage />}
            </AppLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/import"
        element={
          <ProtectedRoute>
            <AppLayout>
              {isOwner ? <Navigate to="/" replace /> : <ImportPage />}
            </AppLayout>
          </ProtectedRoute>
        }
      />

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
