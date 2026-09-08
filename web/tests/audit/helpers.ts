import type { Page } from '@playwright/test'

/** Pages and the GET endpoint each one depends on for its primary data. */
export const PAGE_PRIMARY: Array<{ route: string; api: string; label: string }> = [
  { route: '/tools', api: '**/api/tools*', label: 'Tools' },
  { route: '/skills', api: '**/api/skills*', label: 'Skills' },
  { route: '/agents', api: '**/api/agents*', label: 'Agents' },
  { route: '/mcp', api: '**/api/mcp*', label: 'MCP' },
  { route: '/memory', api: '**/api/memory/status*', label: 'Memory' },
  { route: '/sessions', api: '**/api/sessions*', label: 'Sessions' },
  { route: '/config', api: '**/api/config*', label: 'Config' },
  { route: '/doctor', api: '**/api/doctor*', label: 'Doctor' },
  { route: '/fusion', api: '**/api/fusion/config*', label: 'Fusion' },
  { route: '/tasks', api: '**/api/tasks*', label: 'Tasks' },
  { route: '/search', api: '**/api/search*', label: 'Search' },
  { route: '/schedules', api: '**/api/schedules*', label: 'Schedules' },
  { route: '/artifacts', api: '**/api/artifacts*', label: 'Artifacts' },
]

export type Fault = 'http500' | 'http401' | 'http404' | 'abort' | 'malformed' | 'timeout' | 'empty'

export async function injectFault(page: Page, pattern: string, fault: Fault) {
  await page.route(pattern, async (route) => {
    switch (fault) {
      case 'http500':
        return route.fulfill({ status: 500, contentType: 'application/json',
          body: JSON.stringify({ detail: 'Internal Server Error' }) })
      case 'http401':
        return route.fulfill({ status: 401, contentType: 'application/json',
          body: JSON.stringify({ detail: 'Unauthorized' }) })
      case 'http404':
        return route.fulfill({ status: 404, contentType: 'application/json',
          body: JSON.stringify({ detail: 'Not Found' }) })
      case 'abort':
        return route.abort('failed')
      case 'malformed':
        return route.fulfill({ status: 200, contentType: 'application/json', body: '{not json' })
      case 'timeout':
        await new Promise((r) => setTimeout(r, 60_000))
        return route.abort('timedout')
      case 'empty':
        return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    }
  })
}

/** The four things an error surface must tell the user. */
export interface ErrorQuality {
  visible: boolean          // any error text shown at all
  what: boolean             // says what went wrong
  why: boolean              // gives a reason / status / cause
  nextAction: boolean       // tells the user what to do
  retry: boolean            // an actual retry control, or a statement about retry safety
  role: boolean             // announced to assistive tech
  text: string
}

export async function assessError(page: Page): Promise<ErrorQuality> {
  const body = await page.locator('body').innerText()
  const hay = body.toLowerCase()
  const errRe = /(error|fail|failed|could not|couldn't|unable|unavailable|problem|went wrong|denied|invalid)/
  const visible = errRe.test(hay)
  const why = /(500|401|404|status|because|reason|network|offline|timeout|timed out|disconnect|unauthorized|not found|server)/.test(hay)
  const nextAction = /(try again|retry|reload|refresh|check |verify |ensure |configure|run |install|contact|sign in|log in)/.test(hay)
  const retryBtn = await page.getByRole('button', { name: /retry|try again|reload|refresh/i }).count()
  const roleCount = await page.locator('[role="alert"], [role="status"], [aria-live]').count()
  return {
    visible, what: visible, why, nextAction,
    retry: retryBtn > 0 || /safe to retry|retrying|will retry/.test(hay),
    role: roleCount > 0,
    text: body.replace(/\s+/g, ' ').slice(0, 400),
  }
}
