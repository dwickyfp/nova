import { expect } from 'e2e';
import { test } from './support.ts';

const databases = (screen: any) => screen.getByRole('heading', /^Databases \(\d+\)$/);

test('ACCOUNTADMIN sees the control-plane database', async ({ app, screen, signIn }) => {
  await signIn('admin');
  await app.open('/database-explorer');
  await expect(screen.getByText('NOVA_SYSTEM', { exact: true }).first()).toBeVisible();
  await expect(screen.getByText('analytics', { exact: true }).first()).toBeVisible();
});

test('marketing sees only the database Ranger grants it', async ({ app, screen, signIn }) => {
  await signIn('alice');
  await app.open('/database-explorer');
  await expect(databases(screen)).toHaveText('Databases (1)');
  await expect(screen.getByText('analytics', { exact: true }).first()).toBeVisible();
  await expect(screen.getByText('NOVA_SYSTEM', { exact: true })).toHaveCount(0);
});

test('a role without policies sees no databases', async ({ app, screen, signIn, useRole }) => {
  await signIn('alice');
  await useRole('finance');
  try {
    await app.open('/database-explorer');
    await expect(databases(screen)).toHaveText('Databases (0)');
  } finally {
    await useRole('marketing');
  }
});

test('the Explorer API answers 404 for a database the role cannot list', async ({ signIn, api }) => {
  await signIn('alice');
  const hidden = await api('/explorer/databases/NOVA_SYSTEM');
  expect(hidden.status).toBe(404);
  const visible = await api('/explorer/databases/analytics');
  expect(visible.status).toBe(200);
  expect(visible.headers.get('content-type')).toContain('application/json');
});
