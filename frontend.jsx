import React, { useState, useEffect } from 'react';

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const EmployerPanel = () => {
  const [sessionToken, setSessionToken] = useState(null);
  const [currentPage, setCurrentPage] = useState('login');
  const [engagements, setEngagements] = useState([]);
  const [selectedEngagement, setSelectedEngagement] = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [formData, setFormData] = useState({
    current_job_role: '',
    employment_department: '',
    employment_duration: '',
    rating_core_knowledge: 3,
    rating_knowledge_application: 3,
    rating_problem_solving: 3,
    rating_dev_contribution: 3,
    rating_tool_usage: 3,
    rating_teamwork: 3,
    rating_communication: 3,
    rating_professionalism: 3,
    rating_ethics: 3,
    rating_learning_attitude: 3,
    comments: ''
  });

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get('token');
    if (token) {
      handleLogin(token);
    }
  }, []);

  const handleLogin = async (token) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_URL}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token })
      });
      
      if (!response.ok) throw new Error('Authentication failed');
      const data = await response.json();
      setSessionToken(data.session_token);
      setCurrentPage('dashboard');
      loadDashboard(data.session_token);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const loadDashboard = async (token) => {
    try {
      const response = await fetch(`${API_URL}/api/dashboard`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (!response.ok) throw new Error('Failed to load dashboard');
      const data = await response.json();
      setEngagements(data);
    } catch (err) {
      setError(err.message);
    }
  };

  const loadEngagementDetails = async (engagementId, token) => {
    try {
      const response = await fetch(`${API_URL}/api/engagement/${engagementId}`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (!response.ok) throw new Error('Failed to load engagement');
      const data = await response.json();
      setSelectedEngagement(data);
      if (data.survey_id) {
        setFormData({
          current_job_role: data.current_job_role || '',
          employment_department: data.employment_department || '',
          employment_duration: data.employment_duration || '',
          rating_core_knowledge: data.rating_core_knowledge || 3,
          rating_knowledge_application: data.rating_knowledge_application || 3,
          rating_problem_solving: data.rating_problem_solving || 3,
          rating_dev_contribution: data.rating_dev_contribution || 3,
          rating_tool_usage: data.rating_tool_usage || 3,
          rating_teamwork: data.rating_teamwork || 3,
          rating_communication: data.rating_communication || 3,
          rating_professionalism: data.rating_professionalism || 3,
          rating_ethics: data.rating_ethics || 3,
          rating_learning_attitude: data.rating_learning_attitude || 3,
          comments: data.comments || ''
        });
      }
    } catch (err) {
      setError(err.message);
    }
  };

  const handleSubmitFeedback = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_URL}/api/survey/submit`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${sessionToken}`
        },
        body: JSON.stringify({
          engagement_id: selectedEngagement.id,
          ...formData
        })
      });
      
      if (!response.ok) throw new Error('Failed to submit feedback');
      setCurrentPage('dashboard');
      loadDashboard(sessionToken);
      setSelectedEngagement(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleRatingChange = (field, value) => {
    setFormData(prev => ({ ...prev, [field]: parseInt(value) }));
  };

  const handleTextChange = (field, value) => {
    setFormData(prev => ({ ...prev, [field]: value }));
  };

  if (currentPage === 'login' && !sessionToken) {
    return <LoginPage error={error} loading={loading} />;
  }

  if (currentPage === 'feedback' && selectedEngagement) {
    return (
      <FeedbackForm
        engagement={selectedEngagement}
        formData={formData}
        onRatingChange={handleRatingChange}
        onTextChange={handleTextChange}
        onSubmit={handleSubmitFeedback}
        loading={loading}
        error={error}
        onBack={() => {
          setCurrentPage('dashboard');
          setSelectedEngagement(null);
        }}
      />
    );
  }

  return (
    <Dashboard
      engagements={engagements}
      expandedId={expandedId}
      onToggleExpand={setExpandedId}
      onSelectEngagement={(eng) => {
        loadEngagementDetails(eng.engagement_id, sessionToken);
        setCurrentPage('feedback');
      }}
      loading={loading}
      error={error}
      sessionToken={sessionToken}
      onLogout={() => {
        setSessionToken(null);
        setCurrentPage('login');
      }}
    />
  );
};

const LoginPage = ({ error, loading }) => (
  <div style={styles.loginContainer}>
    <div style={styles.loginCard}>
      <h1 style={styles.loginTitle}>Employer Feedback Portal</h1>
      <p style={styles.loginSubtitle}>
        You should receive a secure link via email to access this portal
      </p>
      {error && <div style={styles.errorBox}>{error}</div>}
      {loading && <div style={styles.loadingBox}>Authenticating...</div>}
      <p style={styles.helpText}>
        Check your email for the access link. Click it to proceed to the feedback form.
      </p>
    </div>
  </div>
);

const Dashboard = ({ engagements, expandedId, onToggleExpand, onSelectEngagement, error, sessionToken, onLogout }) => (
  <div style={styles.container}>
    <header style={styles.header}>
      <div style={styles.headerContent}>
        <h1 style={styles.pageTitle}>Employer Feedback Dashboard</h1>
        <button onClick={onLogout} style={styles.logoutBtn}>Log out</button>
      </div>
    </header>

    <main style={styles.main}>
      {error && <div style={styles.errorBox}>{error}</div>}

      <div style={styles.tableContainer}>
        <table style={styles.table}>
          <thead>
            <tr style={styles.tableHeader}>
              <th style={styles.thName}>Student Name</th>
              <th style={styles.thNumber}>Enrollment #</th>
              <th style={styles.thRole}>Role / Position</th>
              <th style={styles.thStatus}>Feedback Status</th>
              <th style={styles.thAction}>Action</th>
            </tr>
          </thead>
          <tbody>
            {engagements.map((eng) => (
              <React.Fragment key={eng.engagement_id}>
                <tr style={styles.tableRow}>
                  <td style={styles.tdName}>{eng.full_name}</td>
                  <td style={styles.tdNumber}>{eng.enrollment_number}</td>
                  <td style={styles.tdRole}>{eng.role_designation || '—'}</td>
                  <td style={styles.tdStatus}>
                    <span style={{
                      ...styles.statusBadge,
                      ...(eng.feedback_status === 'submitted' ? styles.statusSubmitted : styles.statusPending)
                    }}>
                      {eng.feedback_status === 'submitted' ? 'Submitted' : 'Pending'}
                    </span>
                  </td>
                  <td style={styles.tdAction}>
                    <button
                      onClick={() => onToggleExpand(expandedId === eng.engagement_id ? null : eng.engagement_id)}
                      style={styles.expandBtn}
                      title="Show details"
                    >
                      {expandedId === eng.engagement_id ? '▼' : '▶'} Details
                    </button>
                  </td>
                </tr>
                {expandedId === eng.engagement_id && (
                  <tr style={styles.expandedRow}>
                    <td colSpan="5">
                      <div style={styles.expandedContent}>
                        <div style={styles.detailGrid}>
                          <div style={styles.detailItem}>
                            <label style={styles.detailLabel}>Organization</label>
                            <p style={styles.detailValue}>{eng.organization_name || '—'}</p>
                          </div>
                          <div style={styles.detailItem}>
                            <label style={styles.detailLabel}>Department</label>
                            <p style={styles.detailValue}>{eng.department_served || '—'}</p>
                          </div>
                          <div style={styles.detailItem}>
                            <label style={styles.detailLabel}>Internship Period</label>
                            <p style={styles.detailValue}>
                              {eng.start_date && eng.end_date
                                ? `${new Date(eng.start_date).toLocaleDateString()} - ${new Date(eng.end_date).toLocaleDateString()}`
                                : '—'}
                            </p>
                          </div>
                          <div style={styles.detailItem}>
                            <label style={styles.detailLabel}>Supervisor</label>
                            <p style={styles.detailValue}>{eng.supervisor_name || '—'}</p>
                          </div>
                          <div style={styles.detailItem}>
                            <label style={styles.detailLabel}>Contact Email</label>
                            <p style={styles.detailValue}>{eng.contact_email || '—'}</p>
                          </div>
                          <div style={styles.detailItem}>
                            <label style={styles.detailLabel}>Contact Phone</label>
                            <p style={styles.detailValue}>{eng.contact_phone || '—'}</p>
                          </div>
                        </div>
                        <button
                          onClick={() => onSelectEngagement(eng)}
                          style={styles.feedbackBtn}
                        >
                          {eng.feedback_status === 'submitted' ? 'Edit Feedback' : 'Fill Feedback Form'}
                        </button>
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {engagements.length === 0 && (
        <div style={styles.emptyState}>
          <p>No student engagements found.</p>
        </div>
      )}
    </main>
  </div>
);

const FeedbackForm = ({ engagement, formData, onRatingChange, onTextChange, onSubmit, loading, error, onBack }) => (
  <div style={styles.container}>
    <header style={styles.header}>
      <div style={styles.headerContent}>
        <button onClick={onBack} style={styles.backBtn}>← Back</button>
        <h1 style={styles.pageTitle}>Feedback Form</h1>
      </div>
    </header>

    <main style={styles.main}>
      {error && <div style={styles.errorBox}>{error}</div>}

      <div style={styles.formContainer}>
        <div style={styles.confirmationSection}>
          <h2 style={styles.sectionTitle}>Student Information</h2>
          <div style={styles.confirmationGrid}>
            <div>
              <label style={styles.confirmLabel}>Student Name</label>
              <p style={styles.confirmValue}>{engagement.full_name}</p>
            </div>
            <div>
              <label style={styles.confirmLabel}>Enrollment Number</label>
              <p style={styles.confirmValue}>{engagement.enrollment_number}</p>
            </div>
            <div>
              <label style={styles.confirmLabel}>Organization</label>
              <p style={styles.confirmValue}>{engagement.organization_name || '—'}</p>
            </div>
            <div>
              <label style={styles.confirmLabel}>Role / Position</label>
              <p style={styles.confirmValue}>{engagement.role_designation || '—'}</p>
            </div>
          </div>
        </div>

        <form onSubmit={onSubmit} style={styles.feedbackSection}>
          <h2 style={styles.sectionTitle}>Current Employment Status</h2>
          <div style={styles.formGroup}>
            <label style={styles.label}>Current Job Role</label>
            <input
              type="text"
              value={formData.current_job_role}
              onChange={(e) => onTextChange('current_job_role', e.target.value)}
              style={styles.input}
              placeholder="e.g., Software Engineer"
            />
          </div>

          <div style={styles.formRow}>
            <div style={styles.formGroup}>
              <label style={styles.label}>Department</label>
              <input
                type="text"
                value={formData.employment_department}
                onChange={(e) => onTextChange('employment_department', e.target.value)}
                style={styles.input}
                placeholder="e.g., Engineering"
              />
            </div>
            <div style={styles.formGroup}>
              <label style={styles.label}>Employment Duration</label>
              <input
                type="text"
                value={formData.employment_duration}
                onChange={(e) => onTextChange('employment_duration', e.target.value)}
                style={styles.input}
                placeholder="e.g., 6 months - Permanent"
              />
            </div>
          </div>

          <h2 style={styles.sectionTitle}>Performance Ratings</h2>
          <p style={styles.ratingInstruction}>
            Please rate the student on the following competencies. Use a scale of 1-5, where 1 is minimal and 5 is excellent.
          </p>

          {[
            { field: 'rating_core_knowledge', label: 'Core Knowledge & Expertise' },
            { field: 'rating_knowledge_application', label: 'Application of Knowledge' },
            { field: 'rating_problem_solving', label: 'Problem-Solving Skills' },
            { field: 'rating_dev_contribution', label: 'Development & Contribution' },
            { field: 'rating_tool_usage', label: 'Technical Tool Proficiency' },
            { field: 'rating_teamwork', label: 'Teamwork & Collaboration' },
            { field: 'rating_communication', label: 'Communication Skills' },
            { field: 'rating_professionalism', label: 'Professionalism' },
            { field: 'rating_ethics', label: 'Professional Ethics' },
            { field: 'rating_learning_attitude', label: 'Learning Attitude' }
          ].map(({ field, label }) => (
            <div key={field} style={styles.ratingGroup}>
              <label style={styles.ratingLabel}>{label}</label>
              <div style={styles.ratingOptions}>
                {[1, 2, 3, 4, 5].map(val => (
                  <label key={val} style={styles.radioLabel}>
                    <input
                      type="radio"
                      name={field}
                      value={val}
                      checked={formData[field] === val}
                      onChange={() => onRatingChange(field, val)}
                      style={styles.radio}
                    />
                    <span>{val}</span>
                  </label>
                ))}
              </div>
            </div>
          ))}

          <div style={styles.formGroup}>
            <label style={styles.label}>Additional Comments</label>
            <textarea
              value={formData.comments}
              onChange={(e) => onTextChange('comments', e.target.value)}
              style={styles.textarea}
              placeholder="Provide any additional feedback or observations..."
              rows="6"
            />
          </div>

          <div style={styles.formActions}>
            <button onClick={onBack} type="button" style={styles.cancelBtn}>Cancel</button>
            <button type="submit" style={styles.submitBtn} disabled={loading}>
              {loading ? 'Submitting...' : 'Submit Feedback'}
            </button>
          </div>
        </form>
      </div>
    </main>
  </div>
);

const styles = {
  container: {
    minHeight: '100vh',
    backgroundColor: '#f9fafb',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif',
  },
  header: {
    backgroundColor: '#ffffff',
    borderBottom: '1px solid #e5e7eb',
    padding: '1.5rem',
  },
  headerContent: {
    maxWidth: '1200px',
    margin: '0 auto',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  pageTitle: {
    fontSize: '28px',
    fontWeight: '600',
    color: '#111827',
    margin: 0,
  },
  main: {
    maxWidth: '1200px',
    margin: '0 auto',
    padding: '2rem 1.5rem',
  },
  loginContainer: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: '100vh',
    backgroundColor: '#f9fafb',
    padding: '1rem',
  },
  loginCard: {
    backgroundColor: '#ffffff',
    borderRadius: '8px',
    border: '1px solid #e5e7eb',
    padding: '3rem',
    maxWidth: '500px',
    width: '100%',
    boxShadow: '0 1px 3px rgba(0, 0, 0, 0.1)',
  },
  loginTitle: {
    fontSize: '24px',
    fontWeight: '600',
    color: '#111827',
    margin: '0 0 0.5rem 0',
  },
  loginSubtitle: {
    fontSize: '14px',
    color: '#6b7280',
    margin: '0 0 1.5rem 0',
  },
  helpText: {
    fontSize: '14px',
    color: '#6b7280',
    marginTop: '1.5rem',
  },
  errorBox: {
    backgroundColor: '#fee2e2',
    border: '1px solid #fecaca',
    borderRadius: '6px',
    padding: '0.875rem 1rem',
    color: '#991b1b',
    fontSize: '14px',
    marginBottom: '1rem',
  },
  loadingBox: {
    backgroundColor: '#e0e7ff',
    border: '1px solid #c7d2fe',
    borderRadius: '6px',
    padding: '0.875rem 1rem',
    color: '#3730a3',
    fontSize: '14px',
    marginBottom: '1rem',
  },
  tableContainer: {
    backgroundColor: '#ffffff',
    borderRadius: '8px',
    border: '1px solid #e5e7eb',
    overflow: 'hidden',
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: '14px',
  },
  tableHeader: {
    backgroundColor: '#f3f4f6',
    borderBottom: '1px solid #e5e7eb',
  },
  thName: {
    padding: '1rem',
    textAlign: 'left',
    fontWeight: '600',
    color: '#111827',
    width: '20%',
  },
  thNumber: {
    padding: '1rem',
    textAlign: 'left',
    fontWeight: '600',
    color: '#111827',
    width: '15%',
  },
  thRole: {
    padding: '1rem',
    textAlign: 'left',
    fontWeight: '600',
    color: '#111827',
    width: '25%',
  },
  thStatus: {
    padding: '1rem',
    textAlign: 'left',
    fontWeight: '600',
    color: '#111827',
    width: '15%',
  },
  thAction: {
    padding: '1rem',
    textAlign: 'center',
    fontWeight: '600',
    color: '#111827',
    width: '25%',
  },
  tableRow: {
    borderBottom: '1px solid #e5e7eb',
    backgroundColor: '#ffffff',
  },
  tdName: {
    padding: '1rem',
    color: '#111827',
    fontWeight: '500',
  },
  tdNumber: {
    padding: '1rem',
    color: '#6b7280',
  },
  tdRole: {
    padding: '1rem',
    color: '#6b7280',
  },
  tdStatus: {
    padding: '1rem',
  },
  statusBadge: {
    display: 'inline-block',
    padding: '0.375rem 0.75rem',
    borderRadius: '6px',
    fontSize: '12px',
    fontWeight: '500',
  },
  statusPending: {
    backgroundColor: '#fef3c7',
    color: '#92400e',
  },
  statusSubmitted: {
    backgroundColor: '#dcfce7',
    color: '#15803d',
  },
  tdAction: {
    padding: '1rem',
    textAlign: 'center',
  },
  expandBtn: {
    padding: '0.5rem 0.75rem',
    fontSize: '12px',
    border: '1px solid #d1d5db',
    backgroundColor: '#ffffff',
    color: '#374151',
    borderRadius: '4px',
    cursor: 'pointer',
    fontWeight: '500',
  },
  expandedRow: {
    backgroundColor: '#f9fafb',
    borderBottom: '1px solid #e5e7eb',
  },
  expandedContent: {
    padding: '1.5rem',
  },
  detailGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))',
    gap: '1.5rem',
    marginBottom: '1.5rem',
  },
  detailItem: {
    borderLeft: '3px solid #3b82f6',
    paddingLeft: '1rem',
  },
  detailLabel: {
    fontSize: '12px',
    fontWeight: '600',
    color: '#6b7280',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
    display: 'block',
    marginBottom: '0.25rem',
  },
  detailValue: {
    fontSize: '14px',
    color: '#111827',
    margin: 0,
  },
  feedbackBtn: {
    padding: '0.625rem 1rem',
    fontSize: '13px',
    fontWeight: '600',
    backgroundColor: '#3b82f6',
    color: '#ffffff',
    border: 'none',
    borderRadius: '6px',
    cursor: 'pointer',
  },
  emptyState: {
    textAlign: 'center',
    padding: '3rem',
    color: '#6b7280',
  },
  logoutBtn: {
    padding: '0.5rem 1rem',
    fontSize: '13px',
    backgroundColor: '#f3f4f6',
    border: '1px solid #d1d5db',
    borderRadius: '6px',
    cursor: 'pointer',
    fontWeight: '500',
  },
  backBtn: {
    padding: '0.5rem 1rem',
    fontSize: '13px',
    backgroundColor: '#f3f4f6',
    border: '1px solid #d1d5db',
    borderRadius: '6px',
    cursor: 'pointer',
    fontWeight: '500',
  },
  formContainer: {
    backgroundColor: '#ffffff',
    borderRadius: '8px',
    border: '1px solid #e5e7eb',
    padding: '2rem',
  },
  confirmationSection: {
    marginBottom: '2rem',
    paddingBottom: '2rem',
    borderBottom: '1px solid #e5e7eb',
  },
  sectionTitle: {
    fontSize: '16px',
    fontWeight: '600',
    color: '#111827',
    marginBottom: '1rem',
    marginTop: 0,
  },
  confirmationGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
    gap: '1.5rem',
  },
  confirmLabel: {
    fontSize: '12px',
    fontWeight: '600',
    color: '#6b7280',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
    display: 'block',
    marginBottom: '0.5rem',
  },
  confirmValue: {
    fontSize: '14px',
    color: '#111827',
    margin: 0,
    fontWeight: '500',
  },
  feedbackSection: {
    marginTop: '2rem',
  },
  formGroup: {
    marginBottom: '1.5rem',
  },
  formRow: {
    display: 'grid',
    gridTemplateColumns: 'repeat(2, 1fr)',
    gap: '1.5rem',
    marginBottom: '1.5rem',
  },
  label: {
    display: 'block',
    fontSize: '14px',
    fontWeight: '600',
    color: '#111827',
    marginBottom: '0.5rem',
  },
  input: {
    width: '100%',
    padding: '0.625rem 0.75rem',
    fontSize: '14px',
    border: '1px solid #d1d5db',
    borderRadius: '6px',
    fontFamily: 'inherit',
    boxSizing: 'border-box',
  },
  textarea: {
    width: '100%',
    padding: '0.625rem 0.75rem',
    fontSize: '14px',
    border: '1px solid #d1d5db',
    borderRadius: '6px',
    fontFamily: 'inherit',
    boxSizing: 'border-box',
    resize: 'vertical',
  },
  ratingInstruction: {
    fontSize: '13px',
    color: '#6b7280',
    marginBottom: '1.5rem',
    marginTop: 0,
  },
  ratingGroup: {
    marginBottom: '1.5rem',
    paddingBottom: '1rem',
    borderBottom: '1px solid #e5e7eb',
  },
  ratingLabel: {
    display: 'block',
    fontSize: '14px',
    fontWeight: '500',
    color: '#111827',
    marginBottom: '0.75rem',
  },
  ratingOptions: {
    display: 'flex',
    gap: '1rem',
  },
  radioLabel: {
    display: 'flex',
    alignItems: 'center',
    fontSize: '13px',
    color: '#6b7280',
    cursor: 'pointer',
    fontWeight: '500',
  },
  radio: {
    marginRight: '0.5rem',
    cursor: 'pointer',
  },
  formActions: {
    display: 'flex',
    gap: '1rem',
    justifyContent: 'flex-end',
    marginTop: '2rem',
    paddingTop: '1.5rem',
    borderTop: '1px solid #e5e7eb',
  },
  submitBtn: {
    padding: '0.75rem 1.5rem',
    fontSize: '14px',
    fontWeight: '600',
    backgroundColor: '#3b82f6',
    color: '#ffffff',
    border: 'none',
    borderRadius: '6px',
    cursor: 'pointer',
  },
  cancelBtn: {
    padding: '0.75rem 1.5rem',
    fontSize: '14px',
    fontWeight: '600',
    backgroundColor: '#f3f4f6',
    color: '#374151',
    border: '1px solid #d1d5db',
    borderRadius: '6px',
    cursor: 'pointer',
  },
};

export default EmployerPanel;
