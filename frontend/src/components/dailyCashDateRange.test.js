import { localDayRange } from './dailyCashDateRange';

test('returns local midnight of the given day through local midnight of the next day', () => {
  const { startIso, endIso } = localDayRange(new Date(2026, 8, 22, 14, 3, 0));
  expect(new Date(startIso)).toEqual(new Date(2026, 8, 22, 0, 0, 0, 0));
  expect(new Date(endIso)).toEqual(new Date(2026, 8, 23, 0, 0, 0, 0));
});

test('a date already at local midnight is its own start', () => {
  const { startIso } = localDayRange(new Date(2026, 8, 22, 0, 0, 0, 0));
  expect(new Date(startIso)).toEqual(new Date(2026, 8, 22, 0, 0, 0, 0));
});

test('a date just before local midnight stays in that day, not the next one', () => {
  const { startIso, endIso } = localDayRange(new Date(2026, 8, 22, 23, 59, 59, 999));
  expect(new Date(startIso)).toEqual(new Date(2026, 8, 22, 0, 0, 0, 0));
  expect(new Date(endIso)).toEqual(new Date(2026, 8, 23, 0, 0, 0, 0));
});

test('crosses a month boundary correctly', () => {
  const { endIso } = localDayRange(new Date(2026, 8, 30, 10, 0, 0));
  expect(new Date(endIso)).toEqual(new Date(2026, 9, 1, 0, 0, 0, 0));
});
