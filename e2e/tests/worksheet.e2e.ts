import { expect } from 'e2e';
import { test } from './support.ts';

type Tree = { entries: { id: string }[] };

// The SQL editor is Monaco, which measures layout to place its input.
test('marketing runs SQL and gets only its rows with phones masked', { tags: ['layout'] }, async ({ app, screen, signIn, api }) => {
  await signIn('alice');
  const before = new Set(((await (await api('/workspaces/tree')).json()) as Tree).entries.map((e) => e.id));
  try {
    await app.open('/workspaces');
    await screen.getByRole('button', 'New file').tap();
    // Monaco's input surface sits under its view layer, so focus it, not tap.
    const editor = screen.getByRole('textbox', /Editor content/);
    await editor.focus();
    await editor.pressSequentially(
      "SELECT DISTINCT city FROM analytics.sales UNION ALL SELECT phone FROM analytics.customers",
    );
    await screen.getByRole('button', 'Run').tap();

    // Row filter: marketing sees Jakarta and never Bandung.
    await expect(screen.getByRole('gridcell', 'Jakarta').or(screen.getByText('Jakarta', { exact: true })).first()).toBeVisible();
    await expect(screen.getByText('Bandung', { exact: true })).toHaveCount(0);
    // Mask: the raw phone numbers never reach marketing.
    await expect(screen.getByText('+62-811-0001', { exact: true })).toHaveCount(0);
    await expect(screen.getByText('+62-811-0002', { exact: true })).toHaveCount(0);
  } finally {
    const after = ((await (await api('/workspaces/tree')).json()) as Tree).entries;
    for (const entry of after.filter((e) => !before.has(e.id))) {
      await api(`/workspaces/files/${entry.id}`, { method: 'DELETE' });
    }
  }
});

test('New File in the Workspaces sidebar opens an editor', { tags: ['layout'] }, async ({ app, screen, signIn, api }) => {
  await signIn('alice');
  const before = new Set(((await (await api('/workspaces/tree')).json()) as Tree).entries.map((e) => e.id));
  try {
    await app.open('/workspaces');
    await screen.getByRole('button', 'New File').tap();
    await expect(screen.getByRole('textbox', /Editor content/)).toBeVisible();
  } finally {
    const after = ((await (await api('/workspaces/tree')).json()) as Tree).entries;
    for (const entry of after.filter((e) => !before.has(e.id))) {
      await api(`/workspaces/files/${entry.id}`, { method: 'DELETE' });
    }
  }
});
