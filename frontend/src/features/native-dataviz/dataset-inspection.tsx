import { useState } from 'react'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { DatasetDefinition, ObjectInspection, SemanticInspection, SqlInspection } from './contracts'

export type DatasetInspection = ObjectInspection | SqlInspection | SemanticInspection

export function DatasetInspectionPanel({ inspection }: {
  inspection: Pick<DatasetDefinition, 'source' | 'fields' | 'schema_fingerprint'>
}) {
  const [search, setSearch] = useState('')
  const source = inspection.source
  const dependencies = 'dependencies' in source ? source.dependencies : 'object_name' in source ? [source] : []
  const fields = inspection.fields.filter(field => field.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()))

  return <Tabs defaultValue='fields' className='min-w-0'>
    <TabsList aria-label='Dataset inspection' className='h-11'>
      <TabsTrigger value='fields'>Fields ({inspection.fields.length})</TabsTrigger>
      <TabsTrigger value='lineage'>Lineage</TabsTrigger>
    </TabsList>
    <TabsContent value='fields' className='min-w-0 space-y-2'>
      <Input aria-label='Search inspected fields' placeholder='Search fields' value={search}
        onChange={event => setSearch(event.target.value)} />
      <div className='max-h-80 overflow-auto rounded-md border'>
        <table className='w-full text-left text-sm' aria-label='Inspected dataset fields'>
          <thead className='sticky top-0 bg-muted'><tr>
            <th scope='col' className='p-2'>Field</th><th scope='col' className='p-2'>Type</th>
            <th scope='col' className='p-2'>Role</th><th scope='col' className='p-2'>Nullable</th>
          </tr></thead>
          <tbody>{fields.map(field => <tr key={field.field_id} className='border-t'>
            <th scope='row' className='p-2 font-mono font-normal'>{field.name}</th>
            <td className='p-2 font-mono'>{field.physical_type}</td>
            <td className='p-2'>{field.semantic_role === 'measure' ? 'Measure' : 'Dimension'}</td>
            <td className='p-2'>{field.nullable ? 'Yes' : 'No'}</td>
          </tr>)}</tbody>
        </table>
        {!fields.length && <p role='status' className='p-3 text-sm text-muted-foreground'>No matching fields.</p>}
      </div>
    </TabsContent>
    <TabsContent value='lineage' className='space-y-2 text-sm'>
      {'model_revision' in source && <p>Semantic View version {source.model_revision}</p>}
      {'dataset_revision' in source && <p>Dashboard metric view · Source revision {source.dataset_revision}</p>}
      <p className='text-muted-foreground'>The source inspection resolved these tables and views using your current access.</p>
      <ul className='space-y-2'>{dependencies.map(item => <li
        key={`${item.catalog}:${item.database}:${item.object_name}`} className='break-words rounded-md border p-2'>
        <span className='font-mono'>{item.database}.{item.object_name}</span>
        <span className='ml-2 text-muted-foreground'>{item.kind === 'view' ? 'View' : 'Table'}</span>
      </li>)}</ul>
    </TabsContent>
  </Tabs>
}
