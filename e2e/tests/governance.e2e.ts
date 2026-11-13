import { expect } from 'e2e';
import { test } from './support.ts';

// Fixture data from docker/init-nova.sql and the Ranger bootstrap:
// sales Jakarta 100 + 300, Bandung 200 + 400; customers' phones are masked
// for marketing; each user's city scope is a Ranger user attribute.
const TOTALS = 'SELECT city, SUM(amount) AS total FROM analytics.sales GROUP BY city ORDER BY city';
const RAW_PHONES = ['+62-811-0001', '+62-811-0002'];

test('marketing sees only the city in its own scope (alice: Jakarta)', async ({ signIn, sql }) => {
  await signIn('alice');
  const result = await sql(TOTALS);
  expect(result.success).toBe(true);
  expect(result.rows).toEqual([['Jakarta', '400.00']]);
});

test('the same role scopes another user to another city (bob: Bandung)', async ({ signIn, sql }) => {
  await signIn('bob');
  const result = await sql(TOTALS);
  expect(result.success).toBe(true);
  expect(result.rows).toEqual([['Bandung', '600.00']]);
});

test('regional_manager sees every city in its scope', async ({ signIn, useRole, sql }) => {
  await signIn('alice');
  await useRole('regional_manager');
  try {
    const result = await sql(TOTALS);
    expect(result.rows).toEqual([
      ['Bandung', '600.00'],
      ['Jakarta', '400.00'],
    ]);
  } finally {
    await useRole('marketing');
  }
});

test('a role without a policy is denied the table', async ({ signIn, useRole, sql }) => {
  await signIn('alice');
  await useRole('finance');
  try {
    const result = await sql('SELECT city FROM analytics.sales');
    expect(result.success).toBe(false);
    expect(result.rows).toEqual([]);
    expect(result.error ?? '').toContain('Access denied');
  } finally {
    await useRole('marketing');
  }
});

test('marketing gets masked phones and regional_manager the raw values', async ({ signIn, useRole, sql }) => {
  await signIn('alice');
  const masked = await sql('SELECT phone FROM analytics.customers ORDER BY id');
  expect(masked.rows.length).toBe(2);
  for (const [phone] of masked.rows) expect(RAW_PHONES).not.toContain(phone);

  await useRole('regional_manager');
  try {
    const raw = await sql('SELECT phone FROM analytics.customers ORDER BY id');
    expect(raw.rows).toEqual(RAW_PHONES.map((phone) => [phone]));
  } finally {
    await useRole('marketing');
  }
});
