import { expect } from 'e2e';
import { test } from './support.ts';

// The account menu lives in the sidebar, which needs layout to render.
test('a user switches between assigned roles and back', { tags: ['layout'] }, async ({ screen, signIn, switchRole }) => {
  await signIn('alice');
  await expect(screen.getByRole('button', /marketing$/).first()).toBeVisible();
  await switchRole('marketing', 'finance');
  await switchRole('finance', 'regional_manager');
  await switchRole('regional_manager', 'marketing');
});
