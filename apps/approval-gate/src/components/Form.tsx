import { useState, useEffect } from 'react';
import { Theme } from '@uipath/coded-action-app';
import { codedActionAppService } from '../uipath';
import './Form.css';

interface FormData {
  overall_strength: string;
  violation_count: number;
  recovery_probability: number;
  total_amount_usd: number;
  letter_data: unknown;
  decision: string;
}

const initialData: FormData = {
  overall_strength: '',
  violation_count: 0,
  recovery_probability: 0,
  total_amount_usd: 0,
  letter_data: null,
  decision: '',
};

const isDarkTheme = (theme: Theme) =>
  theme === Theme.Dark || theme === Theme.DarkHighContrast;

interface FormProps {
  onInitTheme: (isDark: boolean) => void;
}

// letter_data is an untyped object from DemandSmith — pull a text body if present,
// otherwise pretty-print the whole object so nothing is hidden from the reviewer.
function letterText(ld: unknown): string {
  if (!ld) return '';
  if (typeof ld === 'string') return ld;
  if (typeof ld === 'object') {
    const o = ld as Record<string, unknown>;
    for (const k of ['letter_text', 'letter', 'body', 'content', 'text', 'demand_letter']) {
      if (typeof o[k] === 'string') return o[k] as string;
    }
    return JSON.stringify(ld, null, 2);
  }
  return String(ld);
}

function fmtPct(n: number): string {
  if (n === null || n === undefined) return '-';
  return n <= 1 ? `${Math.round(n * 100)}%` : `${n}%`;
}

function fmtUsd(n: number): string {
  if (n === null || n === undefined) return '-';
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}

function Form({ onInitTheme }: FormProps) {
  const [formData, setFormData] = useState<FormData>(initialData);
  const [isReadOnly, setIsReadOnly] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    codedActionAppService.getTask().then((task) => {
      if (task.data) setFormData({ ...initialData, ...(task.data as Partial<FormData>) });
      setIsReadOnly(task.isReadOnly);
      onInitTheme(isDarkTheme(task.theme));
    });
  }, [onInitTheme]);

  const complete = async (outcome: 'Approve' | 'Reject', decision: string) => {
    if (isReadOnly || submitting) return;
    setSubmitting(true);
    const updated = { ...formData, decision };
    setFormData(updated);
    await codedActionAppService.completeTask(outcome, updated);
  };

  const strength = (formData.overall_strength || 'unknown').toLowerCase();

  return (
    <div className="approval">
      <header className="approval-header">
        <h1>Dispute Approval</h1>
        <p>Review the analysis and draft demand letter, then approve or reject this dispute.</p>
      </header>

      <section className="metrics">
        <div className="metric">
          <span className="metric-label">Verdict strength</span>
          <span className={`metric-value strength-${strength}`}>{formData.overall_strength || '-'}</span>
        </div>
        <div className="metric">
          <span className="metric-label">Violations</span>
          <span className="metric-value">{formData.violation_count ?? '-'}</span>
        </div>
        <div className="metric">
          <span className="metric-label">Recovery probability</span>
          <span className="metric-value">{fmtPct(formData.recovery_probability)}</span>
        </div>
        <div className="metric">
          <span className="metric-label">Disputed amount</span>
          <span className="metric-value">{fmtUsd(formData.total_amount_usd)}</span>
        </div>
      </section>

      <section className="letter">
        <h2>Draft demand letter</h2>
        <pre className="letter-body">{letterText(formData.letter_data) || 'No letter provided.'}</pre>
      </section>

      <footer className="actions">
        <button
          type="button"
          className="btn btn-reject"
          disabled={isReadOnly || submitting}
          onClick={() => complete('Reject', 'rejected')}
        >
          Reject
        </button>
        <button
          type="button"
          className="btn btn-approve"
          disabled={isReadOnly || submitting}
          onClick={() => complete('Approve', 'approved')}
        >
          Approve
        </button>
      </footer>
      {isReadOnly && <p className="readonly-note">This task has already been completed.</p>}
    </div>
  );
}

export default Form;
