// The agent reports a content hash per source file it loaded, under a short
// name; the server compares those against its own copies and returns the names
// that differ (see NetworkAgent.to_dict -> stale_connectors). These are the
// filenames those short names correspond to on the on-prem box, so the warning
// can say "vsol_olt.py is out of date" rather than "vsol_olt".
//
// Naming the file is the whole point of the feature. A stale connector's
// symptom is not an error -- it is a working-looking feature that quietly
// returns nothing -- and "something is out of date" would not have shortened
// the two live debugging rounds that prompted this.
export const CONNECTOR_FILENAMES = {
    agent: 'agent/servicebills_agent.py',
    mikrotik: 'mikrotik.py',
    vsol_olt: 'vsol_olt.py',
};

// An unrecognised name is passed through rather than dropped: a later server
// version may fingerprint a file this build has never heard of, and naming it
// imperfectly beats silently shortening the list of files to re-copy.
export function connectorFilename(name) {
    return CONNECTOR_FILENAMES[name] || name;
}

// ['agent', 'vsol_olt'] -> 'agent/servicebills_agent.py and vsol_olt.py'
export function describeStaleConnectors(names) {
    const files = (names || []).map(connectorFilename);
    if (files.length === 0) return '';
    if (files.length === 1) return files[0];
    return files.slice(0, -1).join(', ') + ' and ' + files[files.length - 1];
}
