import { expect } from 'e2e';
import { z } from 'zod';
import { test } from './support.ts';

const CityTotals = z.object({
  rows: z.array(z.object({ city: z.string(), total: z.number() })),
});

// Studio answers through Nova's own model provider, and the answer is read by
// the e2e agent. Both need a model, so this runs only when NOVA_E2E_MODEL_* is
// set. The truth comes from the SQL API as the same user and role, so the test
// proves the answer stays inside Ranger's row filter.
test(
  "Studio's answer stays inside the role's row filter",
  {
    tags: ['agentic', 'layout'],
    timeout: 360_000,
    // Skipped at collection: asking for the agent without a model fails the run.
    skip: process.env.NOVA_E2E_MODEL_API_KEY ? false : 'set NOVA_E2E_MODEL_* to run agent steps',
  },
  async ({ app, screen, agent, signIn, sql }) => {
    await signIn('alice');
    const truth = await sql(
      'SELECT city, SUM(amount) AS total FROM analytics.sales GROUP BY city ORDER BY city',
    );
    expect(truth.rows).toEqual([['Jakarta', '400.00']]);

    await app.open('/studio');
    await screen
      .getByRole('textbox', 'Message Nova')
      .fill('What is the total sales amount per city in analytics.sales?');
    await screen.getByRole('button', 'Send').tap();
    await expect(screen.getByRole('button', 'Stop')).toBeVisible();
    await expect(screen.getByRole('button', 'Send')).toBeVisible({ timeout: 300_000 });

    const answer = await agent.extract(
      'every city and its total sales amount that the latest Studio answer reports',
      { schema: CityTotals },
    );
    expect(answer.rows.map((row) => row.city)).toEqual(['Jakarta']);
    expect(answer.rows[0]?.total).toBe(400);
  },
);
