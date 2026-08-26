import { escapeHtml } from './escapeHtml';

test('escapes HTML special characters', () => {
    expect(escapeHtml('<script>alert(1)</script>')).toBe(
        '&lt;script&gt;alert(1)&lt;/script&gt;'
    );
    expect(escapeHtml(`"'&<>`)).toBe('&quot;&#39;&amp;&lt;&gt;');
});

test('passes through plain text unchanged', () => {
    expect(escapeHtml('Jane Doe')).toBe('Jane Doe');
});

test('handles null/undefined safely', () => {
    expect(escapeHtml(null)).toBe('');
    expect(escapeHtml(undefined)).toBe('');
});

test('a malicious customer name never appears unescaped in a receipt HTML string', () => {
    // Mirrors the interpolation pattern used by PaymentsView/ReceiptsView's
    // document.write() receipt template.
    const maliciousName = '<script>alert(document.cookie)</script>';
    const html = `<span>الإسم: ${escapeHtml(maliciousName)}</span>`;
    expect(html).not.toContain('<script>alert(document.cookie)</script>');
    expect(html).toContain('&lt;script&gt;alert(document.cookie)&lt;/script&gt;');
});
