import type { E2EConfig } from 'e2e';
import { web } from '@e2e-dev/web';

/** A password that must come from the environment, never from the repo. */
const fromEnv = (name: string) => () => {
  const value = process.env[name];
  if (!value) throw new Error(`Set ${name} to run the e2e suite`);
  return value;
};

const appUrl = process.env.NOVA_E2E_URL ?? 'http://127.0.0.1:5182';

export default {
  projectId: 'nova',
  targets: [
    {
      name: 'chromium',
      engine: web({ browser: 'chromium' }),
      app: { url: appUrl },
    },
    {
      // Lightpanda has no layout engine: smoke and governance flows only.
      // It runs in a container on the host network, so it shares the app URL.
      name: 'lightpanda',
      engine: web({
        connect: { cdpEndpoint: () => process.env.LIGHTPANDA_URL ?? 'ws://127.0.0.1:9333/' },
      }),
      app: { url: appUrl },
    },
  ],
  timeout: 90_000,
  assertionTimeout: 15_000,
  workers: 1,
  reporters: ['list', 'junit'],
  credentials: {
    admin: { username: 'nova_admin', password: fromEnv('NOVA_E2E_ADMIN_PASSWORD') },
    alice: { username: 'alice', password: fromEnv('NOVA_E2E_ALICE_PASSWORD') },
  },
} satisfies E2EConfig;
