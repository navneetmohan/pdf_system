/* Shared logout helper for PDF Search System */
async function logout() {
  try {
    const resp = await fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'same-origin',
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      console.warn('Logout returned non-OK:', data?.error || resp.status);
    }
  } catch (err) {
    console.error('Logout request failed:', err);
  } finally {
    window.location.href = '/';
  }
}
