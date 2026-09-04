import { apiService } from '../context/AppContext';

// A relayed device check finishes in seconds, but the agent claims work on a
// ~2s poll of its own, so allow generous headroom before giving up. The
// backend also expires stale jobs on read, so this loop normally ends because
// the job reached a terminal state, not because of this ceiling.
const INTERVAL_MS = 1000;
const MAX_ATTEMPTS = 180;

const TERMINAL = ['done', 'failed', 'expired'];

export default async function pollNetworkJob(jobId) {
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
        // eslint-disable-next-line no-await-in-loop
        const res = await apiService.fetchNetworkJob(jobId);
        if (TERMINAL.includes(res.data.status)) return res.data;
        // eslint-disable-next-line no-await-in-loop
        await new Promise((resolve) => setTimeout(resolve, INTERVAL_MS));
    }
    return { status: 'failed', result: null, error: 'Timed out waiting for the check to finish.' };
}
