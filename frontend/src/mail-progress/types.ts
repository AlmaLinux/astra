export type MailProgressState = "running" | "done" | "failed" | "stalled";

export interface MailProgress {
  state: MailProgressState;
  total: number;
  processed: number;
  emailed: number;
  skipped: number;
  failures: number;
  message: string;
  updated_at: number;
}

export interface MailProgressResponse {
  ok: boolean;
  errors?: string[];
  mail_progress?: MailProgress | null;
}
