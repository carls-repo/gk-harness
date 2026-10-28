export const meta = {name: 'gk-selftest', description: 'Echo the profile and project the harness injected; runs no agents'}

return {profile: args?.profile ?? null, project: args?.project ? {name: args.project.name ?? null, root: args.project.root ?? null, guards: (args.project.guards || []).length} : null, scratchpad: args?.scratchpad ?? null}
