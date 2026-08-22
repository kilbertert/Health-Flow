const BASE = '/api';

export async function parseError(res) {
  let detail = `请求失败（HTTP ${res.status}）`;
  try {
    const data = await res.json();
    if (data && typeof data.detail === 'string') detail = data.detail;
    else if (data && data.detail !== undefined) detail = JSON.stringify(data.detail);
    else if (data && typeof data.message === 'string') detail = data.message;
  } catch {
    // Keep the HTTP status fallback for non-JSON responses.
  }
  return detail;
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, { credentials: 'include', ...options });
  if (!res.ok) throw new Error(await parseError(res));
  if (res.status === 204) return null;
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) return res.json();
  return res.text();
}

function reportHeaders(token, headers = {}) {
  return token ? { ...headers, 'X-Report-Token': token } : headers;
}

function reportJson(token, method, body) {
  return {
    method,
    headers: reportHeaders(token, { 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  };
}

export function uploadReport(formData) {
  return request('/health/report/upload', { method: 'POST', body: formData });
}

export const getMetricCatalog = () => request('/health/metric-catalog');

export const getReport = (id, token = '') => request(`/health/report/${id}`, {
  headers: reportHeaders(token),
});

export const confirmReport = (id, token, body) => request(
  `/health/report/${id}/confirm`,
  reportJson(token, 'POST', body),
);

export const assessReport = (id, token) => request(`/health/report/${id}/assess`, {
  method: 'POST',
  headers: reportHeaders(token),
});

export async function fetchReportPage(reportId, fileIndex, pageNumber, token) {
  const res = await fetch(
    `${BASE}/health/report/${reportId}/files/${fileIndex}/pages/${pageNumber}`,
    { credentials: 'include', headers: reportHeaders(token) },
  );
  if (!res.ok) throw new Error(await parseError(res));
  return res.blob();
}

export const registerAccount = (body) => request('/auth/register', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const loginAccount = (body) => request('/auth/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const getCurrentAccount = () => request('/auth/me');

export const logoutAccount = () => request('/auth/logout', { method: 'POST' });

export const updateProfile = (body) => request('/auth/profile', {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const getReportHistory = () => request('/auth/reports');
