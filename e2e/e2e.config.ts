import type { E2EConfig } from 'e2e';
import { web } from '@e2e-dev/web';
import { createOpenAICompatible } from '@ai-sdk/openai-compatible';

/** A password that must come from the environment, never from the repo. */
const fromEnv = (name: string) => () => {
  const value = process.env[name];
  if (!value) throw new Error(`Set ${name} to run the e2e suite`);
  return value;
};

const appUrl = process.env.NOVA_E2E_URL ?? 'http://127.0.0.1:5182';

/**
 * Agent steps use the same OpenAI-compatible provider Nova uses, given through
 * the environment. Without it, tests tagged `agentic` skip and nothing else
 * needs a model. Only synthetic fixture data is on screen for the model.
 */
const model = process.env.NOVA_E2E_MODEL_API_KEY
  ? createOpenAICompatible({
      name: 'nova-provider',
      baseURL: fromEnv('NOVA_E2E_MODEL_BASE_URL')(),
      apiKey: process.env.NOVA_E2E_MODEL_API_KEY,
    }).chatModel(fromEnv('NOVA_E2E_MODEL_ID')())
  : undefined;

const vocabulary =
  'This is Nova, a governed data platform. Studio is the chat page where a user asks ' +
  'questions about data and Nova answers with tables, charts, and text. ' +
  'analytics.sales holds one row per sale with a city and an amount.';

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
  ...(model ? { agents: { default: { model, context: vocabulary } } } : {}),
  credentials: {
    admin: { username: 'nova_admin', password: fromEnv('NOVA_E2E_ADMIN_PASSWORD') },
    alice: { username: 'alice', password: fromEnv('NOVA_E2E_ALICE_PASSWORD') },
    bob: { username: 'bob', password: fromEnv('NOVA_E2E_BOB_PASSWORD') },
  },
} satisfies E2EConfig;
