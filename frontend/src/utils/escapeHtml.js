// Escapes customer-supplied text before it's interpolated into a raw HTML
// string (e.g. the document.write() calls used for receipt printing). Those
// print windows are same-origin, so unescaped HTML/script in a customer's
// name/address/phone would execute there with access to the app's own
// localStorage (JWT session token).
export const escapeHtml = (value) => {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
};
