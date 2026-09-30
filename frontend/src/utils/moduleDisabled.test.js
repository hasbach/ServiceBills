import { shouldNotifyModuleDisabled } from './moduleDisabled';

const err = (method) => ({ config: { method }, response: { status: 403, data: { module: 'network' } } });

test('does not notify for a GET module-403', () => {
    expect(shouldNotifyModuleDisabled(err('get'))).toBe(false);
});
test('notifies for a write module-403', () => {
    expect(shouldNotifyModuleDisabled(err('post'))).toBe(true);
    expect(shouldNotifyModuleDisabled(err('put'))).toBe(true);
});
test('ignores other errors', () => {
    expect(shouldNotifyModuleDisabled({ config: { method: 'post' }, response: { status: 403, data: {} } })).toBe(false);
    expect(shouldNotifyModuleDisabled({ config: { method: 'post' }, response: { status: 500, data: { module: 'x' } } })).toBe(false);
});
