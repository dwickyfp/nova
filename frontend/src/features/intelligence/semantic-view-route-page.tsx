import { useParams } from '@tanstack/react-router'
import { IntelligencePage } from './intelligence-page'

export function SemanticViewRoutePage() {
  const { viewId } = useParams({ from: '/_authenticated/semantic-views_/$viewId' })
  return <IntelligencePage section='semantic' semanticViewId={viewId} />
}
