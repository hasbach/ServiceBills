import { connectorFilename, describeStaleConnectors } from './connectorFiles';

describe('connectorFilename', () => {
    test('maps the short names the server reports to real filenames', () => {
        expect(connectorFilename('agent')).toBe('agent/servicebills_agent.py');
        expect(connectorFilename('mikrotik')).toBe('mikrotik.py');
        expect(connectorFilename('vsol_olt')).toBe('vsol_olt.py');
    });

    test('passes an unrecognised name through rather than dropping it', () => {
        // A later server may fingerprint a file this build has not heard of.
        // Naming it imperfectly beats shortening the list of files to re-copy.
        expect(connectorFilename('something_new')).toBe('something_new');
    });
});

describe('describeStaleConnectors', () => {
    test('names a single stale file', () => {
        expect(describeStaleConnectors(['vsol_olt'])).toBe('vsol_olt.py');
    });

    test('joins two with "and"', () => {
        expect(describeStaleConnectors(['agent', 'vsol_olt']))
            .toBe('agent/servicebills_agent.py and vsol_olt.py');
    });

    test('joins three with commas and a final "and"', () => {
        expect(describeStaleConnectors(['agent', 'mikrotik', 'vsol_olt']))
            .toBe('agent/servicebills_agent.py, mikrotik.py and vsol_olt.py');
    });

    test('is empty for no stale files, and tolerates a missing list', () => {
        // stale_connectors is [] whenever the status is current or unknown,
        // and the whole field is absent when talking to an older server.
        expect(describeStaleConnectors([])).toBe('');
        expect(describeStaleConnectors(undefined)).toBe('');
    });
});
