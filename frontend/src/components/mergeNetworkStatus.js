// The customer status endpoint used to make two connector calls inline and
// return one blob. Under the relay it queues two jobs instead -- deliberately
// two operations the on-prem agent already ships, rather than one new combined
// one that would force everybody to update their agent. This folds the two job
// results back into the shape the UI already renders.
//
// See docs/superpowers/specs/2026-09-07-mikrotik-device-consolidation-design.md

const TERMINAL = ['done', 'failed', 'expired'];

function isTerminal(job) {
    return !!job && TERMINAL.includes(job.status);
}

export function mergeNetworkStatus(secretJob, sessionJob) {
    return {
        secret_status: secretJob && !secretJob.error ? (secretJob.result ?? null) : null,
        secret_error: (secretJob && secretJob.error) || null,
        active_session: sessionJob && !sessionJob.error ? (sessionJob.result ?? null) : null,
        session_error: (sessionJob && sessionJob.error) || null,
        // Both polls have to land before the panel stops showing a spinner;
        // they do not resolve together, and in agent mode the agent handles one
        // job per 2-second poll.
        pending: !isTerminal(secretJob) || !isTerminal(sessionJob),
    };
}
