export type QuerySnapshot = Readonly<{
  sql: string;
  tabId: string;
  database: string | null;
  schema: string | null;
  correlationId?: string;
}>;

export function snapshotQuery(query: QuerySnapshot): QuerySnapshot {
  return Object.freeze({ ...query });
}
