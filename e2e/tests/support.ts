import { test as base } from '@e2e-dev/web';
import { credentials, expect } from 'e2e';

type Persona = 'admin' | 'alice' | 'bob';

/** Sign in through the real form and wait for the signed-in shell. */
async function signIn(
  { app, screen }: { app: { open(path: string): Promise<void> }; screen: any },
  persona: Persona,
): Promise<void> {
  const account = credentials.user(persona);
  await app.open('/sign-in');
  await screen.getByLabel('Username').fill(account.username);
  await screen.getByLabel('Password').fill(account.password);
  await screen.getByRole('button', 'Sign in').tap();
  // The signed-in shell, not the welcome toast: Lightpanda never exposes the toast.
  await expect(screen.getByRole('button', 'Toggle Sidebar').first()).toBeVisible();
  await expect(screen.getByRole('button', 'Sign in')).toHaveCount(0);
}

/** Switch the active role from the account menu in the sidebar. */
async function switchRole(screen: any, from: string, to: string): Promise<void> {
  await screen.getByRole('button', new RegExp(`${from}$`)).first().tap();
  await screen.getByRole('menuitem', from).tap();
  await screen.getByRole('menuitem', to).tap();
  await expect(screen.getByRole('button', new RegExp(`${to}$`)).first()).toBeVisible();
}

export type QueryResult = {
  success: boolean;
  columns: string[];
  rows: unknown[][];
  error?: string | null;
};

export const test = base.extend<{
  signIn: (persona: Persona) => Promise<void>;
  switchRole: (from: string, to: string) => Promise<void>;
  api: (path: string, init?: RequestInit) => Promise<Response>;
  useRole: (role: string) => Promise<void>;
  sql: (statement: string) => Promise<QueryResult>;
}>({
  signIn: async ({ app, screen }, use) => {
    await use((persona) => signIn({ app, screen }, persona));
  },
  switchRole: async ({ screen }, use) => {
    await use((from, to) => switchRole(screen, from, to));
  },
  /** Call Nova's API as the signed-in user, with the session's own token. */
  api: async ({ app, browser }, use) => {
    await use(async (path, init) => {
      const cookie = (await browser.cookies()).find((c) => c.name === 'nova_access_token');
      if (!cookie) throw new Error('No Nova session: sign in first');
      const headers = new Headers(init?.headers);
      headers.set('authorization', `Bearer ${cookie.value}`);
      // baseUrl may end with a slash; resolve so the path never becomes //api.
      return fetch(new URL(`/api/v1${path}`, app.baseUrl), { ...init, headers });
    });
  },
  /**
   * Activate a role through the same API the account menu calls. Use it where
   * the test is about what a role sees, not about the menu, so it also runs
   * on engines without layout.
   */
  useRole: async ({ api }, use) => {
    await use(async (role) => {
      const response = await api('/auth/switch-role', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ role }),
      });
      expect(response.status).toBe(200);
    });
  },
  /** Run SQL through Nova's query API as the signed-in user and active role. */
  sql: async ({ api }, use) => {
    await use(async (statement) => {
      const response = await api('/query/execute', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sql: statement, database: 'analytics' }),
      });
      expect(response.status).toBe(200);
      const [result] = (await response.json()) as QueryResult[];
      return result;
    });
  },
});
