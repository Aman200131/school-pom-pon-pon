import type { CallStatus } from '@/types/database';

export interface CallProviderConfig {
  provider: 'mock' | 'exotel';
  apiKey?: string;
  apiToken?: string;
  senderId?: string;
}

export interface CallRequest {
  studentName: string;
  dueDate: string;
  parentPhone: string;
  reminderType: '7d' | '3d' | '2d' | '1d' | 'overdue';
}

export interface CallResult {
  status: CallStatus;
  providerCallId: string;
}

const REMINDER_MESSAGES = {
  before: (studentName: string, dueDate: string) =>
    `Namaste. This is an automated call from your school. This is a reminder that the school fee for ${studentName} is due on ${dueDate}. Kindly make the payment before the due date. Thank you.`,
  overdue: (studentName: string) =>
    `Namaste. This is an automated call from your school. This is a reminder that the school fee for ${studentName} is overdue. Kindly make the pending payment. Thank you.`,
};

export function buildVoiceMessage(req: CallRequest): string {
  return req.reminderType === 'overdue'
    ? REMINDER_MESSAGES.overdue(req.studentName)
    : REMINDER_MESSAGES.before(req.studentName, req.dueDate);
}

export async function executeCall(
  req: CallRequest,
  config: CallProviderConfig
): Promise<CallResult> {
  const message = buildVoiceMessage(req);

  if (config.provider === 'exotel' && config.apiKey && config.apiToken) {
    // Exotel integration point — implemented when credentials are provided.
    // For now, fall through to mock mode so the workflow is fully testable.
    console.info('[Calling] Exotel provider configured but not yet connected. Using mock mode.');
  }

  // Mock / test calling mode
  // Simulate network latency and a realistic outcome distribution
  await new Promise((resolve) => setTimeout(resolve, 200));

  const outcomes: CallStatus[] = ['answered', 'answered', 'answered', 'no_answer', 'busy', 'failed'];
  const status = outcomes[Math.floor(Math.random() * outcomes.length)];

  console.info(`[Calling/Mock] TO: ${req.parentPhone} | MSG: ${message}`);

  return {
    status,
    providerCallId: 'mock_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8),
  };
}
