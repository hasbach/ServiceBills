import { mergeNetworkStatus } from './mergeNetworkStatus';

const done = (result) => ({ status: 'done', result, error: null });
const failed = (error) => ({ status: 'done', result: null, error });

describe('mergeNetworkStatus', () => {
    test('folds two finished jobs into the old single-response shape', () => {
        expect(mergeNetworkStatus(done('enabled'), done({ address: '10.0.0.9' })))
            .toEqual({
                secret_status: 'enabled',
                secret_error: null,
                active_session: { address: '10.0.0.9' },
                session_error: null,
                pending: false,
            });
    });

    test('reports each job’s error independently', () => {
        // One connector call can fail while the other succeeds -- the old
        // inline endpoint had the same property and the UI relies on it.
        const merged = mergeNetworkStatus(failed('auth failed'), done(null));
        expect(merged.secret_status).toBeNull();
        expect(merged.secret_error).toBe('auth failed');
        expect(merged.active_session).toBeNull();
        expect(merged.session_error).toBeNull();
    });

    test('is pending until both jobs are terminal', () => {
        expect(mergeNetworkStatus({ status: 'pending' }, done(null)).pending).toBe(true);
        expect(mergeNetworkStatus(done('enabled'), { status: 'claimed' }).pending).toBe(true);
        expect(mergeNetworkStatus(done('enabled'), done(null)).pending).toBe(false);
    });

    test('treats a missing job as still pending rather than throwing', () => {
        // The two polls do not resolve together; the first render has one.
        expect(mergeNetworkStatus(null, null).pending).toBe(true);
        expect(mergeNetworkStatus(done('enabled'), undefined).pending).toBe(true);
    });

    test('an expired job surfaces as an error, not a silent blank', () => {
        expect(mergeNetworkStatus({ status: 'expired', result: null, error: 'Job expired' },
                                  done(null)).secret_error).toBe('Job expired');
    });
});
