import { documentLink } from '../api/endpoints'
import { navigateTo } from './nav'

/** Asks for a 60-second link bound to this attachment and starts the download. */
export async function downloadDocument(attachmentId: number): Promise<void> {
  const link = await documentLink(attachmentId)
  navigateTo(link.url)
}
