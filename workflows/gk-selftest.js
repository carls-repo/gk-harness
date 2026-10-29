export const meta = {name: 'gk-selftest', description: 'Echo the profile and project the harness injected; runs no agents'}
// gk-harness:args-v1

const A = typeof args === 'string' ? (() => { try { return JSON.parse(args) } catch (e) { return {} } })() : (args || {})
const OK = A && typeof A === 'object'
return {profile: OK ? (A.profile ?? null) : null, project: OK && A.project ? {name: A.project.name ?? null, root: A.project.root ?? null, guards: (A.project.guards || []).length} : null, scratchpad: OK ? (A.scratchpad ?? null) : null}
